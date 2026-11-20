"""Registered, source-bound tools. Every observation comes from a real function."""

from evidence.hierarchical_retriever import retrieve_more, retrieve_initial
from evidence.verification import make_evidence_bundle, verify_evidence_bundle

from .agent_state import AgentStopped
from .consensus import aggregate_votes
from .contracts import sha256_json
from .execution import AuditPaused
from .memory import ReviewMemory
from .validators import judge_independently, model_request_count


def checked_source_scopes(state):
    return {('adjacent_sentence' if r['tool']=='inspect_context' else r['arguments']['scope'])
        for r in state.context.get('resolution_search_history',{}).values() if r.get('status') in {'ok','empty'}}


class AgentTools:
    def __init__(self, runtime):
        self.runtime = runtime

    @property
    def state(self):
        return self.runtime.state

    def merge_evidence(self, bundle):
        state, runtime = self.state, self.runtime
        verified = verify_evidence_bundle(bundle, runtime.document, state.candidate)
        if runtime.orchestrator.config.get('validator_contract_version') in {'validator_response_v5','validator_response_v5_1','validator_response_v5_2','validator_response_v6','validator_response_v6_1','validator_response_v6_2','validator_response_v6_3','validator_response_v7','validator_response_v8'}:
            from evidence.units import enrich_bundle
            verified = enrich_bundle(verified, runtime.document, state.candidate)
        previous = {x["evidence_id"] for x in state.archive}
        all_items = make_evidence_bundle([*state.archive, *verified["items"]], state.searched_scopes)["items"]
        state.archive = all_items
        state.rejected.extend(verified.get("rejected_items", []))
        # Deterministic, stable selection. Original quote and discovered conflicts
        # are protected; every discovered item remains in the archive/checkpoint.
        initial_ids = runtime.initial_ids
        use_units=runtime.orchestrator.config.get('validator_contract_version')in {'validator_response_v5','validator_response_v5_1','validator_response_v5_2','validator_response_v6','validator_response_v6_1','validator_response_v6_2','validator_response_v6_3','validator_response_v7','validator_response_v8'}
        ordered = sorted(enumerate(all_items), key=lambda pair: (
            -1 if use_units and pair[1]["evidence_id"] in state.protected_ids else
            3 if use_units and pair[1].get('substantive') is False else
            0 if pair[1]["evidence_id"] in initial_ids else
            1 if pair[1]["evidence_id"] in state.protected_ids else 2, pair[0]))
        from .orchestrator import bounded_evidence_bundle
        selected = make_evidence_bundle([item for _, item in ordered], state.searched_scopes)
        next_bundle = bounded_evidence_bundle(verify_evidence_bundle(selected, runtime.document, state.candidate),
            max_items=int(runtime.budget.config.get("max_evidence_items", 12)),
            max_chars=int(runtime.budget.config.get("max_evidence_chars", 12000)))
        # Searching an empty scope, retrieval metadata or a different ordering
        # must not manufacture a new proposal and allow favourable re-voting.
        content = lambda bundle: sorted((x["evidence_id"], x["quote"], x.get("anchor_valid")) for x in bundle.get("items", []))
        if not state.bundle or content(next_bundle) != content(state.bundle):
            state.bundle = next_bundle
            if state.ontology_checked:self.refresh_mapping_preflight()
        return len({x["evidence_id"] for x in state.archive} - previous)

    def refresh_mapping_preflight(self):
        state=self.state
        if 'mapping_preflight_policy' not in state.context:return
        from .ontology_context import select_context
        from .mapping_preflight import prepare
        guard=self.runtime.orchestrator.ontology_guard
        retrieval=state.context.get('ontology_retrieval') or {}
        selected=select_context(guard,state.candidate,state.bundle,state.context['ontology_context_policy'],retrieval)
        selected,report=prepare(guard,state.candidate,state.bundle,selected,state.context['ontology_context_policy'],
            state.context['mapping_preflight_policy'],retrieval)
        state.ontology_lookup.update(mapping_preflight=report,retrieved_context=selected,retrieved_context_hash=sha256_json(selected))
        if state.context.get('resolution_snapshots_enabled'):
            from .definition_tasks import sync
            state.context['shared_definition_tasks']=sync(self.runtime.repository,state)

    def execute(self, name, args):
        # No dynamic import, eval, arbitrary attribute or filesystem/SQL tool.
        registry = {"inspect_context": self.inspect_context, "search_evidence": self.search_evidence,
                    "inspect_ontology": self.inspect_ontology, "lookup_review_memory": self.lookup_review_memory,
                    "run_validation": self.run_validation, "propose_revision": self.propose_revision,
                    "finish_review": self.finish_review, "request_ontology_extension": self.request_ontology_extension,
                    "get_ontology_extension_status": self.get_ontology_extension_status}
        registry['recover_validation']=self.recover_validation
        registry.update(review_revision=self.review_revision,apply_reviewed_revision=self.apply_reviewed_revision)
        registry['review_dispute']=self.review_dispute
        registry.update(propose_mapping=self.propose_mapping,propose_split_revision=self.propose_split_revision,
            review_split_revision=self.review_split_revision,apply_reviewed_split=self.apply_reviewed_split,
            review_semantic_contract=self.review_semantic_contract,assess_ontology_coverage=self.assess_ontology_coverage)
        return registry[name](**args)

    def recover_validation(self):
        from .validation_recovery import recover_validation
        return recover_validation(self.runtime)

    def inspect_context(self, evidence_id, before, after):
        state = self.state
        item = next(i for i in state.archive if i["evidence_id"] == evidence_id)
        if self.runtime.document.get("schema_version") == "document_index_v3":
            from evidence.hierarchical_retriever import context_sentences, document_sentence_evidence
            context = context_sentences(self.runtime.document, item, before, after)
            bundle = make_evidence_bundle([document_sentence_evidence(self.runtime.document, s) for s in context], ["adjacent_sentence"])
            count = self.merge_evidence(bundle)
            return {"status": "ok", "anchor_valid": item["anchor_valid"], "evidence_id": evidence_id,
                    "context": context, "new_evidence_count": count, "section_boundary_preserved": True}
        chunk = next(c for c in self.runtime.document.get("chunks", []) if c["chunk_id"] == item["chunk_id"])
        sentences = chunk.get("sentences", [])
        start, end = item.get("chunk_start", item["start"]), item.get("chunk_end", item["end"])
        covered = [n for n, sentence in enumerate(sentences) if sentence["start"] < end and sentence["end"] > start]
        context = sentences[max(0, covered[0] - before):covered[-1] + after + 1] if covered else []
        # Rebuild exact spans/IDs from frozen source instead of trusting model offsets.
        from evidence.hierarchical_retriever import _sentence_evidence
        bundle = make_evidence_bundle([_sentence_evidence(chunk, sentence, "adjacent_sentence") for sentence in context], ["adjacent_sentence"])
        count = self.merge_evidence(bundle)
        return {"status": "ok", "anchor_valid": item["anchor_valid"], "evidence_id": evidence_id,
                "context": context, "new_evidence_count": count}

    def search_evidence(self, scope, query, purpose, limit):
        state = self.state
        found = retrieve_more(state.candidate, self.runtime.document,
            {"scope": scope, "query": query, "limit": limit}, [i["evidence_id"] for i in state.archive])
        import re
        for item in found["items"]:
            item["query_purpose"] = purpose
            item["review_obligation"] = bool(purpose in {"check_qualifiers", "find_counterevidence"}
                or item.get("lexical_relevance_score", 0) > 0
                or re.search(r"不能|不得|仅在|除非|否定|\b(?:not|never|only|unless)\b", item["quote"], re.I))
        if scope not in state.searched_scopes:
            state.searched_scopes.append(scope)
        count = self.merge_evidence(found)
        return {"status": "ok" if count else "empty", "scope": scope, "query": query, "purpose": purpose,
                "query_used_for": "lexical_recall_and_ranking", "new_evidence_count": count,
                "items": found["items"], "semantic_status": "unknown", "deduplicated_against_archive": True}

    def inspect_ontology(self, relation_id, issue, endpoint_types=None, surface_relation=None, query=None):
        guard = self.runtime.orchestrator.ontology_guard
        self.state.guard = guard.check(self.state.candidate)
        self.state.ontology_checked = True
        definition = getattr(guard, "relations", {}).get(relation_id)
        from schema.relation_registry import RelationRegistry
        from ontologyBuild.ontology_adapter import normalize_name
        registry = RelationRegistry(guard.ontology)
        triple = self.state.candidate.get("triple") or {}
        candidate_lookup = registry.resolve(triple.get("relation_id") or triple.get("surface_relation") or triple.get("relation") or "")
        searches = [registry.resolve(value) for value in (relation_id, surface_relation, query) if value]
        matched = {candidate_lookup["relation_id"]} if candidate_lookup["relation_id"] else set(candidate_lookup.get("matched_ids", []))
        for result in searches:
            matched.update([result["relation_id"]] if result["relation_id"] else result.get("matched_ids", []))
        hints = self.state.candidate.get('extraction_hints') or {}
        class_queries = {name: (endpoint_types or {}).get(name) or triple.get(name + "_type")
                         or hints.get(name + '_type_candidate') for name in ("head", "tail")}
        class_matches = {name: guard.class_index.get(normalize_name(value)) for name, value in class_queries.items() if value}
        terms = {normalize_name(value) for value in (query, surface_relation, triple.get("surface_relation")) if value}
        recall = [{"id": identity, "label": item.get("label"), "kind": item.get("property_kind"), "confirmed_mapping": False}
                  for identity, item in registry.properties.items() if any(term and term in normalize_name(str(item.get("label") or identity)) for term in terms)]
        self.state.ontology_lookup = {"scope": "full_frozen_ontology", "status": candidate_lookup["status"],
            "matched_ids": sorted(matched), "candidate_revision": self.state.candidate["revision"],
            "ontology_version": guard.snapshot.ontology_version, "query": query or surface_relation or triple.get("surface_relation"),
            "ontology_release_id": self.state.candidate.get("ontology_release_id"), "profile_hash": self.state.candidate.get("profile_hash"),
            "endpoint_class_queries": class_queries, "endpoint_class_matches": class_matches,
            "matched_definitions": [registry.properties[identity] for identity in sorted(matched)],
            "recall_suggestions": recall[:12], "recall_is_not_mapping_confirmation": True,
            "class_ids_checked": len(guard.classes), "relation_ids_checked": len(guard.relations)}
        self.state.ontology_lookup['semantic_mapping'] = registry.lookup(
            triple.get('relation_id') or triple.get('surface_relation') or '',
            head_type=triple.get('head_type'),tail_type=triple.get('tail_type'),tail_kind=triple.get('tail_kind','entity'),guard=guard)
        self.state.ontology_lookup['reference_catalog_counts'] = registry.reference_catalog()['counts']
        if self.runtime.orchestrator.config.get('validator_contract_version')in {'validator_response_v6_3','validator_response_v7','validator_response_v8'}:
            from .ontology_context import select_context
            previous=self.state.context.get('ontology_retrieval') or {}
            retrieval={'retrieval_queries':sorted(set(previous.get('retrieval_queries',[])) |
                       {str(x) for x in (relation_id,surface_relation,query) if x}),
                       'retrieval_property_ids':sorted(set(previous.get('retrieval_property_ids',[])) | matched)}
            selected=select_context(guard,self.state.candidate,self.state.bundle,
                                    self.state.context['ontology_context_policy'],retrieval)
            if 'mapping_preflight_policy' in self.state.context:
                from .mapping_preflight import prepare
                selected, preflight = prepare(guard, self.state.candidate, self.state.bundle, selected,
                    self.state.context['ontology_context_policy'], self.state.context['mapping_preflight_policy'], retrieval)
                self.state.ontology_lookup['mapping_preflight'] = preflight
            self.state.context['ontology_retrieval']=retrieval
            self.state.ontology_lookup['retrieved_context']=selected
            self.state.ontology_lookup['retrieved_context_hash']=sha256_json(selected)
        elif self.runtime.orchestrator.config.get('validator_contract_version')in {'validator_response_v5','validator_response_v5_1','validator_response_v5_2','validator_response_v6','validator_response_v6_1','validator_response_v6_2'}:
            self.state.ontology_lookup['full_reference_catalog'] = registry.reference_catalog()
        if self.state.guard.get("status") != "pass":
            self.runtime.issue("ontology_gap", "冻结本体检查未通过", critical=True)
        if self.state.context.get('resolution_snapshots_enabled'):
            from .definition_tasks import sync
            self.state.context['shared_definition_tasks']=sync(self.runtime.repository,self.state)
        return {"status": "ok", "relation_id": relation_id, "definition": definition,
                "requested_endpoint_types": endpoint_types, "issue": issue,
                "check": self.state.guard,
                "existing_schema_search": self.state.ontology_lookup,
                "ontology_version": self.state.candidate["ontology_version"]}

    def request_ontology_extension(self, change_kind, description, proposed_changes, evidence_ids):
        from .planner_contracts import ActionError
        state = self.state
        validation = state.current_validation()
        from .extension_eligibility import validate_extension
        validate_extension(state, change_kind, proposed_changes)
        proposal = self.runtime.repository.get_audit(state.candidate["candidate_id"], state.audit_id)["rounds"]
        proposal = next(p for p in proposal if p["round_id"] == validation["round_id"])
        supporting = {eid for vote in validation["votes"] for eid in vote.get("supporting_evidence_ids", [])}
        supporting.update(validation.get('effective_supporting_evidence_ids',[]))
        if not evidence_ids or not set(evidence_ids) <= supporting:
            raise ActionError("extension requires supported current evidence IDs")
        full_lookup=state.ontology_lookup
        if state.context.get('resolution_snapshots_enabled') and validation.get('resolution_snapshot_id'):
            full_lookup={**full_lookup,'source_resolution_snapshot':state.context['resolution_snapshot'],
                'resolved_source_evidence':state.bundle['items']}
        if state.context.get('resolution_snapshots_enabled') and change_kind=='revise_definition':
            full_lookup={**full_lookup,'shared_definition_target':proposed_changes.get('term_id') or proposed_changes.get('target_id')}
        result = self.runtime.repository.create_extension_request(state.candidate,
            expected_row_version=state.context["execution_row_version"], proposal=proposal, change_kind=change_kind,
            description=description, proposed_changes=proposed_changes, evidence_ids=evidence_ids, full_lookup=full_lookup)
        if full_lookup.get('shared_definition_target'):
            from .definition_tasks import link
            link(self.runtime.repository,state.candidate['ontology_release_id'],full_lookup['shared_definition_target'],result['task']['extension_id'])
        state.pending_extension_id = result["task"]["extension_id"]
        state.workflow_state = result["workflow_state"]
        state.context["execution_row_version"] = result["row_version"]
        state.stop_reason = state.workflow_state
        if state.context.get('resolution_snapshots_enabled') and result['task']['status']=='published':
            self.reconcile_published_extension(state.pending_extension_id)
        return {"status": "waiting", "extension_id": state.pending_extension_id,
                "workflow_state": state.workflow_state, "release_published": False, "worker_wait_required": False}

    def get_ontology_extension_status(self, extension_id):
        result = self.runtime.repository.extension_status(extension_id, self.state.candidate["candidate_id"])
        if self.state.context.get('resolution_snapshots_enabled') and result['status']=='published':
            woke=self.reconcile_published_extension(extension_id)
            if woke.get('rebound'):return {'status':'reaudit_required','task':result,'wakeup':woke,'old_votes_reusable_for_new_release':False}
        self.state.pending_extension_id = extension_id
        self.state.workflow_state = ("reaudit_required" if result["status"] == "published" else
            "schema_unresolved" if result["status"] in {"rejected", "needs_human", "needs_rebase"} else
            "awaiting_ontology_release" if result["status"] == "approved" else "awaiting_ontology_review")
        self.state.stop_reason = self.state.workflow_state
        return {"status": "ok", "task": result, "workflow_state": self.state.workflow_state,
                "old_votes_reusable_for_new_release": False, "automatic_rebind_implemented": True}

    def reconcile_published_extension(self,extension_id):
        from pathlib import Path
        from ontology_review.publisher import OntologyPublisher
        root=Path(self.state.candidate['ontology_release_path']).resolve().parent
        result=OntologyPublisher(self.runtime.repository,root,self.runtime.orchestrator.config).reconcile_subscribers(extension_id)
        rebound=next((r for r in result['rebound'] if r['candidate_id']==self.state.candidate['candidate_id']),None)
        if rebound:
            self.state.stop_reason='revision_submitted_for_reaudit';self.state.workflow_state='reaudit_required'
            self.state.context['applied_revision']={'status':'ontology_rebound','new_revision':rebound['revision'],
                'reaudit_jobs':result['reaudit_jobs'],'new_version_requires_independent_review':True,'old_votes_reusable':False}
        return result

    def lookup_review_memory(self, relation_id, issue_type, limit):
        state = self.state
        result = ReviewMemory(self.runtime.repository).lookup(relation_id=relation_id or None,
            issue_type=issue_type or None, snapshot_id=state.context.get("memory_snapshot_id", 0),
            exclude_candidate_id=state.candidate["candidate_id"], domain=state.candidate.get("domain"),
            scope=state.context['memory_scope'],
            max_positive=limit, max_negative=limit)
        return {"status": "ok", "examples": result, "citable_as_current_evidence": False}

    def run_validation(self):
        runtime, state, budget = self.runtime, self.state, self.runtime.budget
        if state.context.get('dispute_baseline'):
            return {'status':'blocked','error':'use focused dispute review; original full ballot cannot be repeated'}
        if not state.ontology_checked:
            return {"status":"blocked","error":"inspect_ontology is required before independent validation"}
        key = state.validation_key()
        if key in state.validations:
            return {"status": "cached", "validation": state.validations[key], "model_calls": 0}
        maximum = int(budget.config.get("max_validation_rounds", 2))
        if budget.usage["validation_rounds"] >= maximum:
            return {"status": "budget_exhausted", "error": "validation round limit reached"}
        if not state.bundle.get("items"):
            return {"status": "blocked", "error": "no verified evidence"}
        voters = [{**v,'case_ids':list(getattr(budget,'case_ids',[]))} for v in runtime.orchestrator.validators]
        if not voters:
            return {"status": "blocked", "error": "no configured validators"}
        baseline = len(voters)
        retries = min(int(budget.config.get("max_retries_per_request", 1)),
                      max(0, (budget.remaining_calls() - baseline) // baseline))
        reserved = baseline * (1 + retries)
        # Resolve and validate the bounded context before reserving model calls.
        round_id = budget.usage['validation_rounds'] + 1
        proposal = runtime.orchestrator._proposal(state.audit_id, state.candidate,
            state.guard, state.bundle, round_id, state.context)
        if not budget.reserve_calls(reserved):
            return {"status": "budget_exhausted", "error": "cannot reserve full committee cost"}
        budget.usage["validation_rounds"] += 1
        proposal["execution_reserved_calls"] = reserved
        state.current_validation_key = key
        runtime.repository.append_round(state.audit_id, proposal, [], {}, {"execution_status": "started", "validation_key": key})
        # A crash during calls preserves this reservation; it never gets free retries.
        runtime.checkpoint("tool_running")
        returned = []

        def record_vote(vote):
            returned.append(vote)
            runtime.repository.append_round(state.audit_id, proposal, [vote], {}, {"validation_key": key})

        try:
            votes = judge_independently(voters, proposal,
                max_workers=int(budget.config.get("max_parallel_validators", 4)), max_retries=retries,
                per_call_timeout_seconds=min(float(budget.config.get("per_call_timeout_seconds", 45)), budget.remaining_seconds()),
                on_vote=record_vote, repository=runtime.repository,deadline_epoch=budget.deadline_epoch)
        except AuditPaused:
            for vote in returned:
                for attempt in vote.get("attempts") or [vote]:
                    budget.tokens(attempt)
            raise
        runtime.check_live()
        actual = sum(model_request_count(v) for v in votes)
        budget.refund_calls(reserved - actual)
        for vote in votes:
            for attempt in vote.get("attempts") or [vote]:
                budget.tokens(attempt)
        return self.accept_validation(proposal, votes, key)

    def accept_validation(self, proposal, votes, key):
        """Also used to recover a committee that committed before a crash."""
        runtime, state = self.runtime, self.state
        consensus = aggregate_votes(votes, runtime.orchestrator.committee, proposal_digest=proposal["proposal_digest"], proposal=proposal)
        from .issue_ledger import IssueLedger
        ledger = IssueLedger(state)
        ledger.observe(proposal, votes, consensus, runtime.orchestrator.committee.required_votes)
        valid = [v for v in votes if v.get("voter_id") in consensus.get("eligible_voter_ids", [])]
        for vote in valid:
            for eid in vote.get("counter_evidence_ids", []):
                if eid not in state.protected_ids: state.protected_ids.append(eid)
        unresolved = ledger.unresolved()
        conflict = any(i["code"] in {"source_conflict", "counterevidence"} for i in unresolved)
        state.gaps.update(unresolved_counterevidence=conflict, unresolved_support_conflict=conflict,
            ontology_disagreement=any(v.get("ontology_disagreement") for v in valid),
            need_more_evidence=any(i["code"] in {"source_conflict", "counterevidence", "EVIDENCE_INSUFFICIENT"} for i in unresolved))
        consensus = aggregate_votes(votes, runtime.orchestrator.committee, proposal_digest=proposal["proposal_digest"], proposal=proposal,
                                    admission_blocked=any(i.get("critical") for i in unresolved),
                                    unresolved_counterevidence=conflict, unresolved_support_conflict=conflict)
        validation = {"proposal_digest": proposal["proposal_digest"], "round_id": proposal["round_id"],
            "evidence_hash": state.bundle.get("evidence_hash"), "votes": votes, "consensus": consensus}
        state.validations[key], state.votes, state.consensus = validation, votes, consensus
        if consensus.get('dispute_report',{}).get('disputes') and not state.context.get('dispute_baseline'):
            state.context['dispute_baseline']={'proposal_digest':proposal['proposal_digest'],'round_id':proposal['round_id']}
            state.context['dispute_report']=consensus['dispute_report']
        if state.context.get('resolution_snapshots_enabled'):
            state.context['resolution_baseline']={'proposal_digest':proposal['proposal_digest'],'round_id':proposal['round_id']}
            state.context.setdefault('dispute_source_evidence_ids',[e['evidence_id'] for e in proposal['evidence_bundle']['items']])
            from .resolution_state import CHECK_AXES
            for dispute in consensus.get('dispute_report',{}).get('disputes',[]):
                if dispute.get('status') in {'nonblocking_abstention','policy_equivalent'}:continue
                ledger.open('claim_dispute',dispute['question'],True,proposal=proposal['proposal_digest'],
                    evidence_ids=[eid for o in dispute['opinions'] for eid in o['evidence_ids']],
                    evidence_hash=proposal['evidence_bundle']['evidence_hash'],
                    target={'claim_id':dispute['claim']['claim_id'],'check':dispute['check']},
                    dimensions=CHECK_AXES[dispute['check']])
        runtime.repository.record_issue_events(state.audit_id, state.issue_events)
        runtime.repository.update_round_gaps(state.audit_id, proposal["round_id"], {**state.gaps, "validation_key": key})
        # append_round's frozen proposal is immutable; consensus is a system result.
        with runtime.repository.transaction(immediate=True) as connection:
            from .contracts import canonical_json
            connection.execute("UPDATE proposals SET consensus_json=? WHERE audit_id=? AND round_id=?",
                (canonical_json(consensus), state.audit_id, proposal["round_id"]))
        return {"status": "ok", "validation": validation, "independent_committee": True}

    def review_dispute(self,dispute_id):
        runtime,state=self.runtime,self.state
        report=state.context.get('dispute_report') or {}
        dispute=next((d for d in report.get('disputes',[]) if d['dispute_id']==dispute_id and d['status']=='open'),None)
        if not dispute:return {'status':'blocked','error':'exact open dispute required'}
        audit=runtime.repository.get_audit(state.candidate['candidate_id'],state.audit_id)
        base=next(p for p in audit['rounds'] if p['proposal_digest']==state.context['dispute_baseline']['proposal_digest'])
        bundle=verify_evidence_bundle(make_evidence_bundle([*base['evidence_bundle']['items'],*state.bundle['items']],state.searched_scopes),runtime.document,state.candidate)
        from .dispute_review import review
        prep=state.ontology_lookup.get('mapping_preflight') or {}
        result=review(runtime.repository,base,dispute,bundle,runtime.orchestrator.validators,runtime.orchestrator.committee,
            runtime.budget,state.ontology_lookup.get('retrieved_context'),prep.get('judgment_ready',False),
            resolution_policy='review_resolution_snapshot_v1' if state.context.get('resolution_snapshots_enabled') else None)
        dispute['status']=result['status'];state.context.setdefault('dispute_results',{})[dispute_id]=result
        if state.context.get('resolution_snapshots_enabled'):
            if result.get('review_proposal'):state.bundle=bundle
            runtime.refresh_resolution()
        return {'status':result['status'],'dispute_id':dispute_id,'focused_result':result,'original_votes_changed':False}

    def propose_revision(self, changes, evidence_ids, reason,field_evidence=None,reviewed_scopes=None,field_notes=None):
        self.reserve_revision_path()
        from .revision_service import RevisionService
        field_map=None;verification=None
        policy=self.state.context.get('agent_resolution_policy',{})
        if policy.get('expression_grounding_required') or policy.get('qualifier_grounding_required') or any(
                value is not None for value in (field_evidence,reviewed_scopes,field_notes)):
            from .expression_revision import validate_revision_action,VERSION
            args={'changes':changes,'evidence_ids':evidence_ids,'reason':reason,'field_evidence':field_evidence or {},
                'reviewed_scopes':reviewed_scopes or {},'field_notes':field_notes or {}}
            field_map=validate_revision_action({'action':'propose_revision','arguments':args,'target_issue':'expression',
                'rationale_summary':reason},self.state.candidate,self.state.bundle,
                checked_scopes=checked_source_scopes(self.state))
            verification={'version':VERSION,'reviewed_scopes':reviewed_scopes or {},'field_notes':field_notes or {}}
        draft=RevisionService(self.runtime.repository).propose(self.state.candidate,
            self.state.context['execution_row_version'],changes,evidence_ids,reason,self.state.bundle,
            self.runtime.document,protected_ids=self.state.protected_ids,source_audit_id=self.state.audit_id,
            field_evidence=field_map,qualifier_verification=verification)
        self.state.revision_drafts.append(draft)
        self.runtime.issue("revision_proposed", "修订草案需独立审核及新版本复核", critical=True)
        return {"status": "draft_proposed", "draft": draft, "candidate_unchanged": True}

    def review_revision(self,draft_id):
        from .revision_service import RevisionService
        result=RevisionService(self.runtime.repository).review(draft_id,self.runtime.orchestrator.validators,
            self.runtime.orchestrator.committee,self.runtime.budget)
        for draft in self.state.revision_drafts:
            if draft['draft_id']==draft_id: draft['status']=result['status']
        return result

    def apply_reviewed_revision(self,draft_id,draft_hash):
        from .revision_service import RevisionService
        if self.state.context.get('resolution_snapshots_enabled'):
            from .shadow_revision import apply_reviewed
            draft=RevisionService(self.runtime.repository).draft(draft_id)
            if draft['draft_hash']!=draft_hash:raise ValueError('exact approved draft hash required')
            result=apply_reviewed(self.runtime.repository,draft_id=draft_id)
        else:result=RevisionService(self.runtime.repository).apply(draft_id,draft_hash)
        self.state.stop_reason='revision_submitted_for_reaudit'
        self.state.workflow_state='reaudit_required'
        self.state.context['applied_revision']=result
        return result

    def reserve_revision_path(self,children=1):
        if not self.state.context.get('resolution_snapshots_enabled'):return
        from .planner_contracts import ActionError
        final=int(self.state.context['agent_resolution_policy'].get('reserve_final_committee_calls',3))
        required=children*(len(self.runtime.orchestrator.validators)+final)
        if (self.state.context['agent_resolution_policy'].get('final_review_only') or self.state.revision_drafts or self.state.context.get('split_revision_groups')
                or self.runtime.budget.remaining_calls()<required):
            raise ActionError('one revision version; reserve independent revision review and every child final committee before proposing')

    def propose_mapping(self,**arguments):
        self.reserve_revision_path()
        from .mapping_actions import propose
        return propose(self.runtime,**arguments)

    def propose_split_revision(self,children,reason):
        self.reserve_revision_path(len(children))
        from .revision_service import RevisionService
        group=RevisionService(self.runtime.repository).propose_split(self.state.candidate,
            self.state.context['execution_row_version'],children,reason,self.state.bundle,self.runtime.document,
            protected_ids=self.state.protected_ids,source_audit_id=self.state.audit_id)
        self.state.context.setdefault('split_revision_groups',[]).append(group)
        self.runtime.issue('revision_proposed','拆分草案的全部子候选须分别审核、隔离应用并重新核验',critical=True)
        return {'status':'split_proposed','group':group,'candidate_unchanged':True}

    def review_split_revision(self,group_id):
        from .revision_service import RevisionService
        service=RevisionService(self.runtime.repository)
        group=service.split(group_id)
        if group_id not in {g['group_id'] for g in self.state.context.get('split_revision_groups',[])}:
            raise ValueError('exact current split group required')
        results=[service.review(d['draft_id'],self.runtime.orchestrator.validators,
                 self.runtime.orchestrator.committee,self.runtime.budget) for d in group['drafts']]
        group=service.split(group_id)
        self.state.context['split_revision_groups']=[group if g['group_id']==group_id else g for g in self.state.context['split_revision_groups']]
        return {'status':'approved' if group['ready_to_apply'] else 'review_incomplete','group':group,'reviews':results}

    def apply_reviewed_split(self,group_id):
        from .shadow_revision import apply_reviewed
        if group_id not in {g['group_id'] for g in self.state.context.get('split_revision_groups',[])}:raise ValueError('current split group required')
        result=apply_reviewed(self.runtime.repository,group_id=group_id)
        self.state.stop_reason='revision_submitted_for_reaudit';self.state.workflow_state='reaudit_required'
        self.state.context['applied_revision']=result
        return result

    def review_semantic_contract(self,voter_id):
        from .semantic_recheck import review
        return review(self.runtime,voter_id)

    def assess_ontology_coverage(self,exclusions,search_queries):
        from .ontology_coverage import review
        return review(self.runtime,exclusions,search_queries)

    def finish_review(self, stop_reason, unresolved_issues, evidence_ids):
        self.runtime.policy.validate_request({'action':'finish_review','arguments':{'stop_reason':stop_reason,
            'unresolved_issues':unresolved_issues,'evidence_ids':evidence_ids},'target_issue':'finish','rationale_summary':'finish'},self.state)
        for issue in unresolved_issues:
            self.runtime.issue("planner_reported", issue, critical=True)
        self.state.stop_reason = stop_reason
        return {"status": "finish_requested", "stop_reason": stop_reason,
                "cited_evidence_ids": evidence_ids, "system_gaps": self.runtime.policy.final_gaps(self.state),
                "admission_decision_by": "deterministic_finalizer"}
