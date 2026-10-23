"""Observation-driven planner loop with durable checkpoints and fail-closed routing."""

import time
from uuid import uuid4

from evidence.hierarchical_retriever import retrieve_initial
from .action_policy import ActionPolicy
from .agent_state import AgentBudget, AgentState, AgentStopped
from .consensus import aggregate_votes
from .contracts import EVIDENCE_BUNDLE_VERSION, FEATURE_SCHEMA_VERSION, sha256_json
from .execution import AuditPaused
from .memory import ReviewMemory
from .planner import LLMPlanner
from .planner_contracts import ActionError
from .tools import AgentTools
from .validators import model_request_count


class AgentRuntime:
    def __init__(self, orchestrator):
        self.orchestrator, self.repository = orchestrator, orchestrator.repository
        self.planner_config = orchestrator.config.get("planner") or {}
        self.policy = ActionPolicy(self.planner_config)
        self.tools = AgentTools(self)
        self.initial_ids = set()

    def issue(self, code, description, critical=False):
        if not any(x["code"] == code and x["description"] == description for x in self.state.issues):
            self.state.issues.append({"code": code, "description": description, "critical": critical})

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
        self.repository.save_agent_checkpoint(self.state.audit_id, self.state.export(), self.budget.snapshot(), phase)

    def planner_started(self, payload, attempts):
        record = {"status": "started", "role": "planner",
            "model_id": self.orchestrator.planner_client.model_id,
            "model_family": self.orchestrator.planner_client.model_family,
            "attempts": [*attempts, {"attempt_no": len(attempts) + 1, "response_status": "started",
                "input_state": payload, "input_hash": sha256_json(payload)}]}
        self.repository.record_planner_step(self.state.audit_id, self.state.step_no,
            self.state.state_version, self.state.digest(), record,
            checkpoint=(self.state.export(), self.budget.snapshot(), "planning"))

    def planner_interrupted(self, reason):
        audit = self.repository.get_audit(self.state.candidate["candidate_id"], self.state.audit_id)
        step = next((s for s in audit["planner_steps"] if s["step_no"] == self.state.step_no), None)
        if step and step.get("status") == "started":
            self.repository.record_planner_step(self.state.audit_id, self.state.step_no,
                step["state_version"], step["state_hash"], {**step, "status": "interrupted", "error": reason})

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
            if (self.state.candidate != candidate or any(self.state.context.get(k) != v for k, v in versions.items())
                    or self.state.context.get("committee_members") != self.orchestrator.committee_members()):
                raise ValueError("checkpoint candidate or planner/committee version mismatch")
            if self.repository.get_audit(candidate_id, audit_id)["status"] == "completed":
                return "completed"
            self.state.context["job_id"] = supplied.get("job_id")
            self.budget = AgentBudget(self.state.context["budget_config"], saved["budget"], supplied.get("execution_deadline_epoch"))
            self.initial_ids = set(self.state.context.get("initial_evidence_ids", []))
            self.repository.resume_agent_audit(audit_id)
            self.repository.update_audit_context(audit_id, {"job_id": supplied.get("job_id")})
            self.recover(saved["phase"])
            self.state.stop_reason = ""
            return None
        budget_config = dict(self.orchestrator.config.get("budget") or {})
        self.budget = AgentBudget(budget_config, deadline_epoch=supplied.get("execution_deadline_epoch"))
        memory = ReviewMemory(self.repository)
        memory_version = int(supplied.get("memory_snapshot_id", memory.current_version()))
        triple = candidate.get("triple") or candidate
        examples = memory.lookup(relation_id=triple.get("relation_id"), domain=candidate.get("domain"),
            snapshot_id=memory_version, exclude_candidate_id=candidate_id) if (self.orchestrator.config.get("memory") or {}).get("enabled", True) else {}
        ctx = {**supplied, **versions, "audit_id": audit_id,
            "mode": self.orchestrator.config.get("mode", "shadow"), "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "configured_strategy": "llm_react", "executed_strategy": "llm_react",
            "orchestrator_kind": "bounded_agent_runtime", "agent_versions": versions,
            "committee_members": self.orchestrator.committee_members(),
            "budget_config": budget_config, "memory_snapshot_id": memory_version,
            "review_memory": examples, "memory_bundle_hash": sha256_json(examples),
            "execution_deadline_epoch": self.budget.deadline_epoch}
        ctx["execution_row_version"] = self.repository.get_candidate(candidate_id)["row_version"]
        self.state = AgentState(candidate=candidate, context=ctx, audit_id=audit_id)
        self.state.consensus = aggregate_votes([], self.orchestrator.committee)
        self.repository.start_audit(candidate_id, revision, ctx)
        initial = retrieve_initial(candidate, self.document, max_items=6)
        self.initial_ids = {i["evidence_id"] for i in initial["items"] if i.get("retrieval_level") == "local_sentence"}
        ctx["initial_evidence_ids"] = sorted(self.initial_ids)
        self.tools.merge_evidence(initial)
        self.repository.update_audit_context(audit_id, ctx)
        self.checkpoint()

    def recover(self, phase):
        state = self.state
        if phase == "planning":
            state.observations.append({"step_no": state.step_no, "tool": None,
                "status": "interrupted", "error": "planner outcome unknown; reserved request remains charged to budget"})
            self.planner_interrupted("planner outcome unknown after interruption")
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
        if phase != "planned":
            state.pending_action = {}
            self.checkpoint()

    def fallback(self, reason):
        state = self.state
        state.context["fallback_reason"] = reason
        if self.planner_config.get("fallback", "manual") == "deterministic":
            state.executed_strategy = "deterministic_fallback"
        else:
            state.executed_strategy = "manual_degraded"
            state.stop_reason = "planner_unavailable_or_invalid"
            self.issue("planner_failure", reason, critical=True)

    def deterministic_action(self):
        state = self.state
        relation = (state.candidate.get("triple") or {}).get("relation_id") or ""
        if not state.ontology_checked:
            name, args = "inspect_ontology", {"relation_id": relation, "issue": "规则降级核对冻结本体"}
        elif not state.current_validation():
            name, args = "run_validation", {}
        else:
            name, args = "finish_review", {"stop_reason": "deterministic_fallback_finished", "unresolved_issues": [], "evidence_ids": []}
        return {"action": name, "arguments": args, "target_issue": "规则降级", "rationale_summary": "使用剩余预算执行最低核查义务"}

    def execute(self, action):
        state, budget = self.state, self.budget
        self.check_live()
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
        self.repository.record_agent_tool(state.audit_id, state.step_no, name, "started",
            {"arguments": action["arguments"], "before_state_hash": before},
            checkpoint=(state.export(), budget.snapshot(), "tool_running"))
        try:
            fingerprint = self.policy.check(action, state)
            outcome = self.tools.execute(name, action["arguments"])
            outcome["action_fingerprint"] = fingerprint
            self.check_live()
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
        planner = LLMPlanner(self.orchestrator.planner_client,
            max_retries=int(budget.config.get("max_retries_per_request", 1)),
            max_output_tokens=int(self.planner_config.get("max_output_tokens", 1024))) if self.orchestrator.planner_client else None
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
                    state.stop_reason = "planner_step_budget_exhausted"
                    self.issue("planner_budget", "规划步数耗尽", critical=True)
                    break
                state.step_no += 1
                if state.executed_strategy == "deterministic_fallback":
                    action = self.deterministic_action()
                    record = {"status": "not_called_fallback", "action": action, "role": "system"}
                elif planner is None:
                    self.fallback("planner model connection is not configured")
                    self.checkpoint()
                    continue
                else:
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
                        fatal = next((a for a in result.get("attempts", []) if a.get("pause_job")), None)
                        if fatal:
                            raise AuditPaused({**fatal, "voter_id": "planner", "role": "planner"})
                        invalid_steps += 1
                        only_contract = result.get("attempts") and all(a["response_status"] == "invalid_action" for a in result["attempts"])
                        if not only_contract or invalid_steps >= int(self.planner_config.get("max_invalid_steps", 2)):
                            self.fallback(result.get("error") or "planner failed")
                        continue
                state.pending_action = action
                self.repository.record_planner_step(state.audit_id, state.step_no, state.state_version, state.digest(), record,
                    checkpoint=(state.export(), budget.snapshot(), "planned"))
                self.execute(action)
            self.check_live()
            gaps = {**self.policy.final_gaps(state), "stop_reason": state.stop_reason}
            state.context.update(executed_strategy=state.executed_strategy, agent_stop_reason=state.stop_reason,
                agent_budget=budget.snapshot(), unresolved_issues=state.issues, revision_drafts=state.revision_drafts)
            self.repository.update_audit_context(state.audit_id, state.context)
            current = state.current_validation()
            votes = current["votes"] if current else []
            consensus = current["consensus"] if current else aggregate_votes([], self.orchestrator.committee)
            result = self.orchestrator._finalize(state.candidate, state.context, state.audit_id, state.guard,
                state.bundle, votes, consensus, gaps, current["round_id"] if current else 0,
                {**budget.usage, "deadline_epoch": budget.deadline_epoch})
            self.checkpoint("finished")
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
                "executed_strategy": state.executed_strategy, "agent_stop_reason": str(exc)})
            self.repository.finish_incomplete_audit(state.audit_id, "timeout" if str(exc) == "total_timeout" else "interrupted", {"reason_code": str(exc)})
            return {"audit_id": state.audit_id, "status": "timeout" if str(exc) == "total_timeout" else "interrupted",
                    "committed": False, "stop_reason": str(exc), "budget": budget.usage}
