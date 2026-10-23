"""Registered, source-bound tools. Every observation comes from a real function."""

from evidence.hierarchical_retriever import retrieve_more, retrieve_initial
from evidence.verification import make_evidence_bundle, verify_evidence_bundle

from .agent_state import AgentStopped
from .consensus import aggregate_votes
from .contracts import sha256_json
from .execution import AuditPaused
from .memory import ReviewMemory
from .validators import judge_independently, model_request_count


class AgentTools:
    def __init__(self, runtime):
        self.runtime = runtime

    @property
    def state(self):
        return self.runtime.state

    def merge_evidence(self, bundle):
        state, runtime = self.state, self.runtime
        verified = verify_evidence_bundle(bundle, runtime.document, state.candidate)
        previous = {x["evidence_id"] for x in state.archive}
        all_items = make_evidence_bundle([*state.archive, *verified["items"]], state.searched_scopes)["items"]
        state.archive = all_items
        state.rejected.extend(verified.get("rejected_items", []))
        # Deterministic, stable selection. Original quote and discovered conflicts
        # are protected; every discovered item remains in the archive/checkpoint.
        initial_ids = runtime.initial_ids
        ordered = sorted(enumerate(all_items), key=lambda pair: (
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
        return len({x["evidence_id"] for x in state.archive} - previous)

    def execute(self, name, args):
        # No dynamic import, eval, arbitrary attribute or filesystem/SQL tool.
        registry = {"inspect_context": self.inspect_context, "search_evidence": self.search_evidence,
                    "inspect_ontology": self.inspect_ontology, "lookup_review_memory": self.lookup_review_memory,
                    "run_validation": self.run_validation, "propose_revision": self.propose_revision,
                    "finish_review": self.finish_review}
        return registry[name](**args)

    def inspect_context(self, evidence_id, before, after):
        state = self.state
        item = next(i for i in state.archive if i["evidence_id"] == evidence_id)
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
        if scope not in state.searched_scopes:
            state.searched_scopes.append(scope)
        count = self.merge_evidence(found)
        return {"status": "ok" if count else "empty", "scope": scope, "query": query, "purpose": purpose,
                "query_used_for": "lexical_recall_and_ranking", "new_evidence_count": count,
                "items": found["items"], "semantic_status": "unknown", "deduplicated_against_archive": True}

    def inspect_ontology(self, relation_id, issue, endpoint_types=None):
        guard = self.runtime.orchestrator.ontology_guard
        self.state.guard = guard.check(self.state.candidate)
        self.state.ontology_checked = True
        definition = getattr(guard, "relations", {}).get(relation_id)
        if self.state.guard.get("status") != "pass":
            self.runtime.issue("ontology_gap", "冻结本体检查未通过", critical=True)
        return {"status": "ok", "relation_id": relation_id, "definition": definition,
                "requested_endpoint_types": endpoint_types, "issue": issue,
                "check": self.state.guard,
                "ontology_version": self.state.candidate["ontology_version"]}

    def lookup_review_memory(self, relation_id, issue_type, limit):
        state = self.state
        result = ReviewMemory(self.runtime.repository).lookup(relation_id=relation_id or None,
            issue_type=issue_type or None, snapshot_id=state.context.get("memory_snapshot_id", 0),
            exclude_candidate_id=state.candidate["candidate_id"], domain=state.candidate.get("domain"),
            max_positive=limit, max_negative=limit)
        return {"status": "ok", "examples": result, "citable_as_current_evidence": False}

    def run_validation(self):
        runtime, state, budget = self.runtime, self.state, self.runtime.budget
        key = state.validation_key()
        if key in state.validations:
            return {"status": "cached", "validation": state.validations[key], "model_calls": 0}
        maximum = int(budget.config.get("max_validation_rounds", 2))
        if budget.usage["validation_rounds"] >= maximum:
            return {"status": "budget_exhausted", "error": "validation round limit reached"}
        if not state.bundle.get("items"):
            return {"status": "blocked", "error": "no verified evidence"}
        voters = runtime.orchestrator.validators
        if not voters:
            return {"status": "blocked", "error": "no configured validators"}
        baseline = len(voters)
        retries = min(int(budget.config.get("max_retries_per_request", 1)),
                      max(0, (budget.remaining_calls() - baseline) // baseline))
        reserved = baseline * (1 + retries)
        if not budget.reserve_calls(reserved):
            return {"status": "budget_exhausted", "error": "cannot reserve full committee cost"}
        budget.usage["validation_rounds"] += 1
        round_id = budget.usage["validation_rounds"]
        proposal = runtime.orchestrator._proposal(state.audit_id, state.candidate,
            state.guard, state.bundle, round_id, state.context)
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
                on_vote=record_vote, repository=runtime.repository)
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
        valid = [v for v in votes if v.get("response_status") == "ok"]
        supports = any(v.get("source_support") == "supported" for v in valid)
        contradicts = any(v.get("source_support") == "contradicted" for v in valid)
        counter = any(v.get("verdict") == "invalid" and v.get("counter_evidence_ids") for v in valid)
        if supports and (contradicts or counter):
            runtime.issue("source_conflict", "独立审核发现支持与反证冲突，需核查适用范围或转人工", critical=True)
            state.gaps.update(unresolved_counterevidence=True, unresolved_support_conflict=True)
        material = {"CONDITION_MISSING", "QUALIFIER_MISMATCH", "DIRECTION_MISMATCH", "EVIDENCE_INSUFFICIENT"}
        for vote in valid:
            for code in set(vote.get("reason_codes", [])) & material:
                runtime.issue(code, "审核委员报告关键限定条件/方向/证据缺口", critical=True)
            if vote.get("ontology_disagreement"):
                state.gaps["ontology_disagreement"] = True
            for eid in vote.get("counter_evidence_ids", []):
                if eid not in state.protected_ids:
                    state.protected_ids.append(eid)
        state.gaps["need_more_evidence"] = bool(state.issues or any(
            v.get("source_support") == "insufficient" or v.get("requested_context") for v in valid))
        consensus = aggregate_votes(votes, runtime.orchestrator.committee,
            proposal_digest=proposal["proposal_digest"],
            unresolved_counterevidence=bool(state.gaps.get("unresolved_counterevidence")),
            unresolved_support_conflict=bool(state.gaps.get("unresolved_support_conflict")))
        validation = {"proposal_digest": proposal["proposal_digest"], "round_id": proposal["round_id"],
            "evidence_hash": state.bundle.get("evidence_hash"), "votes": votes, "consensus": consensus}
        state.validations[key], state.votes, state.consensus = validation, votes, consensus
        runtime.repository.update_round_gaps(state.audit_id, proposal["round_id"], {**state.gaps, "validation_key": key})
        # append_round's frozen proposal is immutable; consensus is a system result.
        with runtime.repository.transaction(immediate=True) as connection:
            from .contracts import canonical_json
            connection.execute("UPDATE proposals SET consensus_json=? WHERE audit_id=? AND round_id=?",
                (canonical_json(consensus), state.audit_id, proposal["round_id"]))
        return {"status": "ok", "validation": validation, "independent_committee": True}

    def propose_revision(self, changes, evidence_ids, reason):
        draft = {"changes": changes, "evidence_ids": evidence_ids, "reason": reason,
                 "source_revision": self.state.candidate["revision"], "requires_human_confirmation": True}
        self.state.revision_drafts.append(draft)
        self.runtime.issue("revision_proposed", "修改草案待人工确认；当前候选与旧票未被修改", critical=True)
        return {"status": "draft_only", "draft": draft, "candidate_unchanged": True}

    def finish_review(self, stop_reason, unresolved_issues, evidence_ids):
        for issue in unresolved_issues:
            self.runtime.issue("planner_reported", issue, critical=True)
        self.state.stop_reason = stop_reason
        return {"status": "finish_requested", "stop_reason": stop_reason,
                "cited_evidence_ids": evidence_ids, "system_gaps": self.runtime.policy.final_gaps(self.state),
                "admission_decision_by": "deterministic_finalizer"}
