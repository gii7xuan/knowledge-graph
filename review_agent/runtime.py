"""Observation-driven planner loop with durable checkpoints and fail-closed routing."""

import time
from uuid import uuid4

from evidence.hierarchical_retriever import retrieve_initial
from .action_policy import ActionPolicy
from .agent_state import AgentState, AgentStopped
from .consensus import aggregate_votes
from .contracts import EVIDENCE_BUNDLE_VERSION, FEATURE_SCHEMA_VERSION, sha256_json
from .execution import AuditPaused
from .memory import ReviewMemory
from .memory_scope import resolve_scope, scope_hash
from .planner import LLMPlanner
from .planner_contracts import ActionError
from .tools import AgentTools
from .validators import model_request_count
from .case_budget import CaseBudget
from .task_adapter import TripleTaskAdapter


class AgentRuntime:
    def __init__(self, orchestrator):
        self.orchestrator, self.repository = orchestrator, orchestrator.repository
        self.planner_config = orchestrator.config.get("planner") or {}
        self.policy = ActionPolicy({**self.planner_config,
            'enable_resolution':bool((orchestrator.config.get('agent_resolution') or {}).get('enabled')),
            'enable_dispute_review':orchestrator.config.get('validator_contract_version')=='validator_response_v8' and bool((orchestrator.config.get('dispute_review') or {}).get('enabled'))})
        self.tools = AgentTools(self)
        self.adapter = TripleTaskAdapter(self)
        self.initial_ids = set()

    def issue(self, code, description, critical=False):
        from .issue_ledger import IssueLedger
        IssueLedger(self.state).open(code, description, critical)

    def should_stop(self):
        current = self.repository.get_candidate(self.state.candidate["candidate_id"])
        return bool(self.orchestrator.should_cancel and self.orchestrator.should_cancel()) or (
            current["revision"] != self.state.candidate["revision"]) or (
            self.state.context.get("execution_row_version") is not None and
            current["row_version"] != self.state.context["execution_row_version"])

    def check_live(self):
        if self.should_stop():
            raise AgentStopped("cancelled_or_revision_changed")
        if self.budget.remaining_seconds() <= 0:
            raise AgentStopped("total_timeout")

    def checkpoint(self, phase="ready"):
        self.repository.record_issue_events(self.state.audit_id, self.state.issue_events)
        self.repository.save_agent_checkpoint(self.state.audit_id, self.state.export(), self.budget.snapshot(), phase)

    def planner_started(self, payload, attempts, request_proposal=None):
        record = {"status": "started", "role": "planner",
            "model_id": self.orchestrator.planner_client.model_id,
            "model_family": self.orchestrator.planner_client.model_family,
            "attempts": [*attempts, {"attempt_no": len(attempts) + 1, "response_status": "started",
                "input_state": payload, "input_hash": sha256_json(payload)}]}
        if request_proposal is not None:
            record['request_proposal'] = request_proposal
        self.repository.record_planner_step(self.state.audit_id, self.state.step_no,
            self.state.state_version, self.state.digest(), record,
            checkpoint=(self.state.export(), self.budget.snapshot(), "planning"))

    def planner_interrupted(self, reason):
        audit = self.repository.get_audit(self.state.candidate["candidate_id"], self.state.audit_id)
        step = next((s for s in audit["planner_steps"] if s["step_no"] == self.state.step_no), None)
        if step and step.get("status") == "started":
            from .planner_receipts import durable_planner_rows,reconcile_attempts
            with self.repository.connect() as connection:
                attempts=reconcile_attempts(step,durable_planner_rows(connection))
            self.repository.record_planner_step(self.state.audit_id, self.state.step_no,
                step["state_version"], step["state_hash"], {**step, "status": "interrupted", "error": reason,'attempts':attempts})

    def initialize(self, candidate_id, revision, document, context):
        from .batch import versions_for
        self.document = dict(document)
        candidate = self.repository.get_revision(candidate_id, revision)
        supplied = dict(context or {})
        audit_id = supplied.get("audit_id") or "audit_" + uuid4().hex
        versions = versions_for(self.orchestrator.config)
        saved = self.repository.get_agent_checkpoint(audit_id)
        if saved:
            self.state = AgentState(**saved["state"])
            memory_scope=resolve_scope(self.orchestrator.config,supplied,pinned=self.state.context.get('memory_scope'))
            if self.state.context.get('memory_scope_hash')!=scope_hash(memory_scope):
                raise ValueError('checkpoint memory scope changed; fresh audit required')
            if (self.state.candidate != candidate or any(self.state.context.get(k) != v for k, v in versions.items())
                    or self.state.context.get("committee_members") != self.orchestrator.committee_members()):
                raise ValueError("checkpoint candidate or planner/committee version mismatch")
            if self.repository.get_audit(candidate_id, audit_id)["status"] == "completed":
                return "completed"
            self.state.context["job_id"] = supplied.get("job_id")
            case_id = self.repository.ensure_case(candidate_id, self.orchestrator.config.get("case_budget"))
            self.budget = CaseBudget(self.state.context["budget_config"], self.repository, saved['budget'].get('case_ids') or [case_id], task_type="triple_review", task_id=candidate_id,
                                    saved=saved["budget"], deadline_epoch=supplied.get("execution_deadline_epoch"))
            self.initial_ids = set(self.state.context.get("initial_evidence_ids", []))
            self.repository.resume_agent_audit(audit_id)
            self.repository.update_audit_context(audit_id, {"job_id": supplied.get("job_id")})
            self.recover(saved["phase"])
            self.state.stop_reason = ""
            return None
        budget_config = dict(self.orchestrator.config.get("budget") or {})
        case_id = self.repository.ensure_case(candidate_id, self.orchestrator.config.get("case_budget"))
        case_ids=[case_id]
        split=candidate.get('split_origin') or {}
        if (self.orchestrator.config.get('agent_resolution') or {}).get('enabled') and split.get('parent_candidate_id'):
            case_ids.append(self.repository.ensure_case(split['parent_candidate_id'],self.orchestrator.config.get('case_budget')))
        self.budget = CaseBudget(budget_config, self.repository, case_ids, task_type="triple_review", task_id=candidate_id,
                                deadline_epoch=supplied.get("execution_deadline_epoch"))
        memory = ReviewMemory(self.repository)
        memory_scope=resolve_scope(self.orchestrator.config,supplied)
        memory_version = int(supplied.get("memory_snapshot_id", memory.current_version()))
        triple = candidate.get("triple") or candidate
        examples = memory.lookup(relation_id=triple.get("relation_id"), domain=candidate.get("domain"),
            snapshot_id=memory_version, exclude_candidate_id=candidate_id,scope=memory_scope) if (self.orchestrator.config.get("memory") or {}).get("enabled", True) else {}
        ctx = {**supplied, **versions, "audit_id": audit_id,
            "mode": self.orchestrator.config.get("mode", "shadow"), "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "configured_strategy": "llm_react", "executed_strategy": "llm_react",
            "orchestrator_kind": "bounded_agent_runtime", "agent_versions": versions,
            "committee_members": self.orchestrator.committee_members(),
            "budget_config": budget_config, "memory_snapshot_id": memory_version,
            'memory_scope':memory_scope,'memory_scope_hash':scope_hash(memory_scope),
            "review_memory": examples, "memory_bundle_hash": sha256_json(examples),
            "execution_deadline_epoch": self.budget.deadline_epoch}
        if self.planner_config.get('compact_view'):
            ctx['planner_compact_view']=True
        if (self.orchestrator.config.get('agent_resolution') or {}).get('enabled'):
            ctx.update(resolution_snapshots_enabled=True,
                       agent_resolution_policy=self.orchestrator.config['agent_resolution'])
        elif self.planner_config.get('fixed_candidate_limits'):
            # Focused review must affect the same bound consensus in both arms,
            # without enabling revision application or ontology publication.
            ctx.update(resolution_snapshots_enabled=True,
                       agent_resolution_policy={'max_attempts_per_issue':3,'max_semantic_revisions':0,
                           'reserve_final_committee_calls':0,'fixed_candidate_limits':True})
        ctx["execution_row_version"] = self.repository.get_candidate(candidate_id)["row_version"]
        self.state = AgentState(candidate=candidate, context=ctx, audit_id=audit_id)
        from .issue_ledger import IssueLedger
        IssueLedger(self.state).inherit(self.repository)
        prior=self.repository.previous_revision_audit(candidate_id,revision,audit_id)
        if prior: self.state.votes=prior.get('votes',[])
        self.state.consensus = aggregate_votes([], self.orchestrator.committee)
        self.repository.start_audit(candidate_id, revision, ctx)
        frozen_initial=supplied.get('initial_evidence_bundle')
        if frozen_initial is not None:
            from copy import deepcopy
            from evidence.verification import verify_evidence_bundle
            checked=verify_evidence_bundle(frozen_initial,self.document,candidate)
            if checked['evidence_hash']!=frozen_initial.get('evidence_hash'):
                raise ValueError('frozen initial evidence bundle changed during verification')
            self.initial_ids={x['evidence_id'] for x in checked['items']}
            self.state.archive=deepcopy(checked['items']);self.state.bundle=deepcopy(checked)
            self.state.searched_scopes=list(checked.get('searched_levels',[]))
            ctx.pop('initial_evidence_bundle',None)
            ctx.update(initial_evidence_ids=sorted(self.initial_ids),frozen_initial_evidence_hash=checked['evidence_hash'])
            self.repository.update_audit_context(audit_id,ctx);self.checkpoint()
            return None
        initial = retrieve_initial(candidate, self.document, max_items=6)
        self.initial_ids = {i["evidence_id"] for i in initial["items"] if i.get("retrieval_level") == "local_sentence" or i.get("source_anchor_kind") == "document_span"}
        ctx["initial_evidence_ids"] = sorted(self.initial_ids)
        self.tools.merge_evidence(initial)
        self.repository.update_audit_context(audit_id, ctx)
        self.checkpoint()

    def refresh_resolution(self):
        state=self.state
        baseline=state.context.get('dispute_baseline') or state.context.get('resolution_baseline')
        if not state.context.get('resolution_snapshots_enabled') or not baseline:
            return None
        from .resolution_state import resolve_review_state,apply_consensus,update_issue_ledger
        audit=self.repository.get_audit(state.candidate['candidate_id'],state.audit_id)
        base=next(p for p in audit['rounds'] if p['proposal_digest']==baseline['proposal_digest'])
        votes=[v for v in audit['votes'] if v['proposal_digest']==base['proposal_digest']]
        with self.repository.connect() as connection:
            import json
            results=[json.loads(r[0]) for r in connection.execute(
                'SELECT result_json FROM dispute_focus_reviews WHERE audit_id=?',(state.audit_id,)) if r[0]!='{}']
        from .resolution_recovery import hydrate
        refreshed=hydrate(self.repository,audit,state.context)
        for key in ('focused_review_results','semantic_recheck_results','original_receipt_results'):
            if key in refreshed:state.context[key]=refreshed[key]
        results=state.context.get('focused_review_results',results)
        ontology=state.ontology_lookup.get('retrieved_context',base.get('ontology_context'))
        snapshot=resolve_review_state(base,votes,results,state.issues,self.orchestrator.committee,
            candidate=state.candidate,bundle=state.bundle,ontology_context=ontology,
            semantic_results=list(state.context.get('semantic_recheck_results',{}).values()),
            original_receipts=state.context.get('original_receipt_results',[]))
        update_issue_ledger(state,base,votes,self.orchestrator.committee,snapshot)
        snapshot=resolve_review_state(base,votes,results,state.issues,self.orchestrator.committee,
            candidate=state.candidate,bundle=state.bundle,ontology_context=ontology,
            semantic_results=list(state.context.get('semantic_recheck_results',{}).values()),
            original_receipts=state.context.get('original_receipt_results',[]))
        state.context.update(focused_review_results=results,resolution_candidate=state.candidate,
            resolution_bundle=state.bundle,resolution_ontology_context=ontology,resolution_snapshot=snapshot,
            issue_history=state.issues)
        state.consensus=apply_consensus(state.consensus,snapshot)
        self.repository.record_issue_events(state.audit_id,state.issue_events)
        return base,votes,snapshot

    def recover(self, phase):
        state = self.state
        recovered_planner = False
        if phase == "planning":
            import json
            from .planner_requests import current_action
            audit = self.repository.get_audit(state.candidate['candidate_id'], state.audit_id)
            step = next((s for s in audit['planner_steps'] if s['step_no'] == state.step_no), {})
            proposal = step.get('request_proposal')
            receipt = None
            if proposal:
                with self.repository.connect() as connection:
                    row = connection.execute("SELECT result_json FROM request_attempts WHERE "
                        "json_extract(metadata_json,'$.task_type')='planner_action' AND "
                        "json_extract(metadata_json,'$.proposal.proposal_digest')=? ORDER BY started_at DESC LIMIT 1",
                        (proposal['proposal_digest'],)).fetchone()
                receipt = json.loads(row[0] or '{}') if row else None
                if receipt:
                    self.budget.tokens(receipt)
                    action, error = current_action(receipt, proposal, state,
                        lambda a: self.policy.validate_request(a, state))
                    if action is not None:
                        state.pending_action = action
                        recovered_planner = True
                        self.repository.record_planner_step(state.audit_id, state.step_no,
                            state.state_version, state.digest(), {**step, 'status': 'recovered_original_receipt',
                                'action': action, 'attempts': [receipt]},
                            checkpoint=(state.export(), self.budget.snapshot(), 'planned'))
                    else:
                        state.context.setdefault('technical_recovery_tasks', []).append({
                            'task_type': 'planner_action', 'request_key': receipt.get('request_key'),
                            'attempt_id': receipt.get('attempt_id'), 'status': 'pending', 'reason': error,
                            'automatic_resend': False, 'action_executed': False})
            if not recovered_planner:
                state.context['planner_interrupted_unknown'] = True
            state.observations.append({"step_no": state.step_no, "tool": None,
                "status": "recovered_original_receipt" if recovered_planner else "interrupted",
                "error": None if recovered_planner else "planner outcome unknown; reserved request remains charged to budget"})
            if not recovered_planner:
                self.planner_interrupted("planner outcome unknown after interruption")
                self.fallback('interrupted planner request; do not redispatch or execute an unbound action')
        if phase in {"tool_running", "stopped", "paused"} and state.pending_action:
            action = state.pending_action
            outcome = {"status": "interrupted", "error": "previous tool outcome unknown; no model calls repeated"}
            if action["action"] == "run_validation":
                audit = self.repository.get_audit(state.candidate["candidate_id"], state.audit_id)
                if audit["rounds"]:
                    proposal = audit["rounds"][-1]
                    votes = [v for v in audit["votes"] if v["round_id"] == proposal["round_id"]]
                    key = state.current_validation_key or state.validation_key()
                    if votes:
                        outcome = self.tools.accept_validation(proposal, votes, key)
                        for vote in votes:
                            for attempt in vote.get("attempts") or [vote]:
                                self.budget.tokens(attempt)
                    expected = {v["id"] for v in self.orchestrator.validators}
                    if {v["voter_id"] for v in votes} == expected and "execution_reserved_calls" in proposal:
                        actual = sum(model_request_count(v) for v in votes)
                        self.budget.refund_calls(proposal["execution_reserved_calls"] - actual)
                    if {v["voter_id"] for v in votes} != expected:
                        self.issue("interrupted_validation", "部分委员调用结果未知；禁止重投已完成有效票", critical=True)
                        state.validations.setdefault(key, {"round_id": proposal["round_id"],
                            "proposal_digest": proposal["proposal_digest"], "votes": votes, "consensus": state.consensus})
                        outcome["status"] = "interrupted_partial"
                    self.budget.usage["validation_rounds"] = max(self.budget.usage["validation_rounds"], proposal["round_id"])
            state.observations.append({"step_no": state.step_no, "tool": action["action"], **outcome})
            self.repository.record_agent_tool(state.audit_id, state.step_no, action["action"], outcome["status"], outcome)
            state.pending_action = {}
            state.state_version += 1
        if phase != "planned" and not recovered_planner:
            state.pending_action = {}
            self.checkpoint()

    def fallback(self, reason):
        state = self.state
        state.context["fallback_reason"] = reason
        if self.planner_config.get('failure_policy')=='bounded_minimum_v1' or self.planner_config.get("fallback", "manual") == "deterministic":
            state.executed_strategy = "deterministic_fallback"
            state.context['failure_handling']={'version':'bounded_minimum_v1','reason':reason,
                'format_retry_limit':1,'fallback':'ontology_then_source_then_independent_review',
                'original_votes_immutable':True,'minimum_steps':state.context.get('minimum_review_steps',0)}
        else:
            state.executed_strategy = "manual_degraded"
            state.stop_reason = "planner_unavailable_or_invalid"
            self.issue("planner_failure", reason, critical=True)

    def deterministic_action(self):
        state = self.state
        relation = (state.candidate.get("triple") or {}).get("relation_id") or ""
        if not state.ontology_checked:
            name, args = "inspect_ontology", {"relation_id": relation, "issue": "规则降级核对冻结本体"}
        elif not state.bundle.get('items') and not state.searched_scopes:
            triple=state.candidate.get('triple') or {}
            name,args='search_evidence',{'scope':'same_section','query':' '.join(str(triple.get(k) or '') for k in ('head','surface_relation','tail')).strip() or relation or '核对原文',
                'purpose':'check_qualifiers','limit':6}
        elif not state.current_validation() and not state.context.get('dispute_baseline'):
            if state.bundle.get('items'): name, args = "run_validation", {}
            else: name,args='finish_review',{'stop_reason':'minimum_review_no_verified_source','unresolved_issues':['缺少可核验原文，审核未完成'],'evidence_ids':[]}
        else:
            name, args = "finish_review", {"stop_reason": "deterministic_fallback_finished", "unresolved_issues": [], "evidence_ids": []}
        return {"action": name, "arguments": args, "target_issue": "规则降级", "rationale_summary": "使用剩余预算执行最低核查义务"}

    def execute(self, action):
        state, budget = self.state, self.budget
        self.check_live()
        budget.stage=('revision_review' if action['action'] in {'review_revision','review_split_revision'} else
            'final_review' if state.context.get('agent_resolution_policy',{}).get('final_review_only') and action['action']=='run_validation' else
            'initial_review' if action['action']=='run_validation' else
            'evidence_dispute' if action['action'] in {'review_dispute','search_evidence','inspect_context','recover_validation','review_semantic_contract'} else 'preprocessing')
        if budget.exhausted():
            state.stop_reason = "token_budget_exhausted"
            self.issue("token_budget", "规划调用实际 token 已达到预算，工具不再执行", critical=True)
            return
        if not budget.consume_tool():
            state.stop_reason = "tool_budget_exhausted"
            self.issue("tool_budget", "工具预算耗尽", critical=True)
            return
        before = state.digest()
        name = action["action"]
        if state.context.get('resolution_snapshots_enabled'):
            state.context['resolution_remaining_calls']=budget.remaining_calls()
        self.repository.record_agent_tool(state.audit_id, state.step_no, name, "started",
            {"arguments": action["arguments"], "before_state_hash": before},
            checkpoint=(state.export(), budget.snapshot(), "tool_running"))
        try:
            fingerprint = self.policy.check(action, state)
            if state.context.get('resolution_snapshots_enabled'):
                from .resolution_obligations import attempt_targets
                counts=state.context.setdefault('resolution_attempts',{})
                for target in attempt_targets(action,state):counts[target]=counts.get(target,0)+1
            outcome = self.adapter.execute_tool(action, state)
            outcome["action_fingerprint"] = fingerprint
            if name not in {'apply_reviewed_revision','apply_reviewed_split'} and state.stop_reason!='revision_submitted_for_reaudit': self.check_live()
        except ActionError as exc:
            outcome = {"status": "invalid_action", "error": str(exc)}
            if "repeated" in str(exc):
                state.stop_reason = "no_information_gain"
                self.issue("repeated_action", str(exc), critical=True)
        except (AuditPaused, AgentStopped):
            raise
        except Exception as exc:
            outcome = {"status": "tool_error", "error": str(exc)}
        state.state_version += 1
        observation = {"step_no": state.step_no, "tool": name, "arguments": action["arguments"], **outcome}
        state.observations.append(observation)
        if state.context.get('resolution_snapshots_enabled'):
            if name in {'inspect_context','search_evidence'}:
                state.context['resolution_no_gain']=not bool(outcome.get('new_evidence_count'))
                if not outcome.get('new_evidence_count'):
                    from .resolution_obligations import attempt_targets
                    state.context['resolution_no_gain_targets']=sorted(set(state.context.get('resolution_no_gain_targets',[]))|set(attempt_targets(action,state)))
            if name not in {'finish_review','apply_reviewed_revision','apply_reviewed_split'}:
                self.refresh_resolution()
        state.pending_action = {}
        self.repository.record_agent_tool(state.audit_id, state.step_no, name, outcome["status"],
            {**observation, "before_state_hash": before, "after_state_hash": state.digest()},
            checkpoint=(state.export(), budget.snapshot(), "ready"))
        if outcome["status"] == "budget_exhausted":
            state.stop_reason = "validation_budget_exhausted"
            self.issue("validation_budget", outcome.get("error", "预算不足"), critical=True)

    def run(self, candidate_id, revision, document, *, context=None):
        if self.initialize(candidate_id, revision, document, context) == "completed":
            return {"audit_id": self.state.audit_id, "reused_completed": True, "committed": False}
        state, budget = self.state, self.budget
        fixed_workflow=self.planner_config.get('fixed_workflow_policy')
        if fixed_workflow:
            from .fixed_candidate_workflow import VERSION
            if fixed_workflow!=VERSION:raise ValueError('unknown fixed workflow policy')
            state.executed_strategy='fixed_workflow'
            state.context.update(configured_strategy='fixed_workflow',executed_strategy='fixed_workflow',
                                 fixed_workflow_policy=VERSION)
        planner = LLMPlanner(self.orchestrator.planner_client,
            max_retries=1 if self.planner_config.get('failure_policy')=='bounded_minimum_v1' else int(budget.config.get("max_retries_per_request", 1)),
            max_output_tokens=int(self.planner_config.get("max_output_tokens", 1024)),
            action_validator=lambda action:self.policy.validate_request(action,state),
            repository=self.repository if self.planner_config.get('durable_requests') else None,
            request_settings=self.planner_config) if self.orchestrator.planner_client else None
        invalid_steps = 0
        try:
            while not state.stop_reason:
                self.check_live()
                if budget.exhausted():
                    state.stop_reason = "token_budget_exhausted"
                    self.issue("token_budget", "实际报告 token 达到预算上限", critical=True)
                    break
                if state.pending_action:
                    self.execute(state.pending_action)
                    continue
                maximum = int(budget.config.get("max_planner_steps", 6))
                if state.executed_strategy != "deterministic_fallback" and budget.usage["planner_steps"] >= maximum:
                    if self.planner_config.get('failure_policy')=='bounded_minimum_v1':
                        self.fallback('planner step budget exhausted; perform remaining minimum obligations')
                        self.checkpoint()
                        continue
                    state.stop_reason = "planner_step_budget_exhausted"
                    self.issue("planner_budget", "规划步数耗尽", critical=True)
                    break
                state.step_no += 1
                if self.planner_config.get('fixed_candidate_limits'):
                    state.context['resolution_no_further_validation']=budget.usage['validation_rounds']>=int(budget.config.get('max_validation_rounds',2))
                fixed=None
                if fixed_workflow:
                    from .fixed_candidate_workflow import next_action
                    fixed=next_action(state)
                elif self.planner_config.get('state_driven_steps'):
                    from .resolution_obligations import ready_action,resolution_obligations
                    from .validation_recovery import recovery_plan
                    state.validation_recovery=recovery_plan(state,self.repository)
                    state.context['resolution_no_further_validation']=budget.usage['validation_rounds']>=int(budget.config.get('max_validation_rounds',2))
                    state.context['resolution_obligations']=resolution_obligations(state,budget)
                    if not state.ontology_checked:
                        fixed=self.deterministic_action()
                    elif (not state.current_validation() and not state.context.get('dispute_baseline') and state.bundle.get('items')
                          and not state.context['resolution_no_further_validation']):
                        fixed={'action':'run_validation','arguments':{},'target_issue':'initial_review','rationale_summary':'核验当前冻结候选的完整委员会'}
                    else:fixed=ready_action(state,budget)
                    if fixed and fixed['action'] not in self.policy.allowed:fixed=None
                if fixed:
                    action=fixed
                    record={'status':'not_called_fixed_transition','action':action,'role':'system'}
                elif state.executed_strategy == "deterministic_fallback":
                    steps=state.context.get('minimum_review_steps',0)
                    if steps>=4:
                        state.stop_reason='minimum_review_incomplete'
                        self.issue('minimum_review_incomplete','固定最小审核未满足关键义务，审核未完成',critical=True)
                        break
                    state.context['minimum_review_steps']=steps+1
                    state.context.setdefault('failure_handling',{})['minimum_steps']=steps+1
                    action = self.deterministic_action()
                    record = {"status": "not_called_fallback", "action": action, "role": "system"}
                elif planner is None:
                    self.fallback("planner model connection is not configured")
                    self.checkpoint()
                    continue
                else:
                    from .validation_recovery import recovery_plan
                    state.validation_recovery=recovery_plan(state,self.repository)
                    from .revision_service import RevisionService
                    for draft in state.revision_drafts:
                        if draft.get('status')=='review_incomplete':
                            draft['review_recovery_available']=RevisionService(self.repository).recovery_available(draft['draft_id'])
                    budget.usage["planner_steps"] += 1
                    before_version, before_hash = state.state_version, state.digest()
                    self.checkpoint("planning")
                    result = planner.next_action(state, budget, self.policy.definitions(), should_stop=self.should_stop,
                        reserve_validation_calls=len(self.orchestrator.validators) if not state.current_validation() else 0,
                        on_request=self.planner_started)
                    action = result["action"]
                    record = {**result, "role": "planner", "model_id": planner.client.model_id,
                              "model_family": planner.client.model_family,
                              "configured_strategy": "llm_react", "executed_strategy": state.executed_strategy}
                    if action is None:
                        state.observations.append({"step_no": state.step_no, "tool": None,
                            "status": "planner_error", "error": result.get("error")})
                        self.repository.record_planner_step(state.audit_id, state.step_no, before_version, before_hash, record,
                            checkpoint=(state.export(), budget.snapshot(), "ready"))
                        fatal = next((a for a in result.get("attempts", []) if a.get("pause_job") and
                            (not self.planner_config.get('durable_requests') or a.get('error_code')!='SERVICE_PAUSED')), None)
                        if fatal:
                            raise AuditPaused({**fatal, "voter_id": "planner", "role": "planner"})
                        invalid_steps += 1
                        only_contract = result.get("attempts") and all(a["response_status"] == "invalid_action" for a in result["attempts"])
                        if self.planner_config.get('failure_policy')=='bounded_minimum_v1' or not only_contract or invalid_steps >= int(self.planner_config.get("max_invalid_steps", 2)):
                            self.fallback(result.get("error") or "planner failed")
                        continue
                state.pending_action = action
                self.repository.record_planner_step(state.audit_id, state.step_no, state.state_version, state.digest(), record,
                    checkpoint=(state.export(), budget.snapshot(), "planned"))
                self.execute(action)
            if state.stop_reason=='revision_submitted_for_reaudit':
                self.repository.update_audit_context(state.audit_id,{'revision_drafts':state.revision_drafts,
                    'applied_revision':state.context['applied_revision'],'issue_history':state.issues,
                    'review_completion_status':'superseded_for_reaudit','independent_review_completed':False,
                    'agent_budget':budget.snapshot()})
                self.repository.finish_incomplete_audit(state.audit_id,'superseded',state.context['applied_revision'])
                self.checkpoint('revision_submitted_for_reaudit')
                return {'audit_id':state.audit_id,'committed':False,**state.context['applied_revision']}
            self.check_live()
            resolution=self.refresh_resolution()
            if state.context.get('resolution_snapshots_enabled'):
                from .resolution_obligations import resolution_obligations,human_cards
                obligations=resolution_obligations(state,budget)
                state.context.update(resolution_obligations=obligations,human_problem_cards=human_cards(state,obligations))
            gaps = {**self.policy.final_gaps(state), "stop_reason": state.stop_reason}
            state.context.update(executed_strategy=state.executed_strategy, agent_stop_reason=state.stop_reason,
                agent_budget=budget.snapshot(), unresolved_issues=[i for i in state.issues if i.get("status", "open") != "resolved"],
                issue_history=state.issues, evidence_exclusions=state.evidence_exclusions, ontology_lookup=state.ontology_lookup,
                workflow_state=state.workflow_state, pending_extension_id=state.pending_extension_id,
                revision_drafts=state.revision_drafts)
            self.repository.update_audit_context(state.audit_id, state.context)
            current = state.current_validation()
            if resolution:
                base,resolved_votes,snapshot=resolution
                current={'round_id':base['round_id'],'votes':resolved_votes,'consensus':state.consensus}
            votes = current["votes"] if current else []
            consensus = current["consensus"] if current else aggregate_votes([], self.orchestrator.committee)
            eligible=set(consensus.get('eligible_voter_ids',[]))
            complete=bool(current and state.ontology_checked and
                eligible==set(self.orchestrator.committee.voter_ids) and
                consensus.get('distinct_family_count',0)>=self.orchestrator.committee.minimum_distinct_families)
            if self.orchestrator.committee.weighting_policy:
                complete=bool(current and state.ontology_checked and consensus.get('weighted_review_complete'))
            completion=('independent_review_completed' if complete else
                'independent_review_incomplete' if current else
                'manual_handoff_no_evidence' if state.ontology_checked and not state.bundle.get('items') and state.searched_scopes else
                'not_independently_reviewed')
            state.context.update(review_completion_status=completion,independent_review_completed=complete)
            if state.context.get('failure_handling'):
                from .failure_policy import minimum_status
                state.context['failure_handling'].update(minimum_status(complete,state.guard,state.bundle,consensus))
            self.repository.update_audit_context(state.audit_id,{'review_completion_status':completion,'independent_review_completed':complete,
                **({'failure_handling':state.context['failure_handling']} if state.context.get('failure_handling') else {})})
            result = self.orchestrator._finalize(state.candidate, state.context, state.audit_id, state.guard,
                state.bundle, votes, consensus, gaps, current["round_id"] if current else 0,
                {**budget.usage, "deadline_epoch": budget.deadline_epoch})
            if result.get("committed"):
                state.workflow_state = result["workflow_state"]
                state.context["workflow_state"] = state.workflow_state
                state.context["execution_row_version"] = self.repository.get_candidate(candidate_id)["row_version"]
                self.repository.update_audit_context(state.audit_id, {"workflow_state": state.workflow_state})
            self.checkpoint("waiting_ontology" if state.workflow_state.startswith("awaiting_") else "finished")
            return result
        except AuditPaused as exc:
            self.checkpoint("paused")
            self.repository.update_audit_context(state.audit_id, {"agent_budget": budget.snapshot(),
                "executed_strategy": state.executed_strategy, "agent_stop_reason": "insufficient_balance"})
            self.repository.finish_incomplete_audit(state.audit_id, "paused", {"error": str(exc), "reason_code": "INSUFFICIENT_BALANCE"})
            raise
        except AgentStopped as exc:
            state.stop_reason = str(exc)
            self.planner_interrupted(str(exc))
            self.checkpoint("stopped")
            self.repository.update_audit_context(state.audit_id, {"agent_budget": budget.snapshot(),
                "executed_strategy": state.executed_strategy, "agent_stop_reason": str(exc),
                'review_completion_status':'independent_review_incomplete','independent_review_completed':False})
            self.repository.finish_incomplete_audit(state.audit_id, "timeout" if str(exc) == "total_timeout" else "interrupted", {"reason_code": str(exc)})
            return {"audit_id": state.audit_id, "status": "timeout" if str(exc) == "total_timeout" else "interrupted",
                    "committed": False, "stop_reason": str(exc), "budget": budget.usage,
                    'review_completion_status':'independent_review_incomplete','independent_review_completed':False}
        except Exception as exc:
            from .review_observability import redact
            detail={'reason_code':'RUNTIME_EXCEPTION','error':redact(str(exc)),
                'failed_stage':(state.pending_action or {}).get('action') or 'finalization',
                'error_type':type(exc).__name__,'recoverable':False}
            self.repository.update_audit_context(state.audit_id,{'agent_budget':budget.snapshot(),
                'issue_history':state.issues,'executed_strategy':state.executed_strategy})
            self.repository.finish_incomplete_audit(state.audit_id,'failed',detail)
            raise
