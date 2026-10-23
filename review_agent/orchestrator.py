"""Bounded evidence-and-validation state machine."""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Mapping, Optional

from confidence.calibrator import TripleCalibrator
from confidence.feature_extractor import TripleFeatureExtractor
from confidence.risk_policy import RiskPolicy
from evidence.hierarchical_retriever import retrieve_initial, retrieve_more
from evidence.verification import make_evidence_bundle, verify_evidence_bundle

from .consensus import Committee, aggregate_votes
from .contracts import FEATURE_SCHEMA_VERSION, EVIDENCE_BUNDLE_VERSION, canonical_triple, proposal_digest, sha256_json
from .response_contract import PROMPT_VERSION, CONTRACT_VERSION, CONTEXT_SCOPES
from .explanation import explain_from_verified_records
from .execution import AuditPaused
from .memory import ReviewMemory
from .repository import ReviewRepository
from .validators import judge_independently, model_request_count


MATERIAL_GAP_CODES = {"CONDITION_MISSING", "QUALIFIER_MISMATCH", "DIRECTION_MISMATCH", "EVIDENCE_INSUFFICIENT"}


class Budget:
    def __init__(self, config: Mapping[str, Any]):
        self.max_rounds = min(2, max(1, int(config.get("max_rounds", 2))))
        self.max_llm_calls = max(0, int(config.get("max_llm_calls_per_candidate", 12)))
        self.max_tool_calls = max(0, int(config.get("max_tool_calls", 6)))
        self.total_timeout_seconds = max(1.0, float(config.get("total_timeout_seconds", 180)))
        self.started_at = time.monotonic()
        self.llm_calls = 0
        self.tool_calls = 0

    def expired(self) -> bool:
        return time.monotonic() - self.started_at >= self.total_timeout_seconds

    def allow_llm(self, count: int) -> bool:
        if self.expired() or self.llm_calls + count > self.max_llm_calls:
            return False
        self.llm_calls += count
        return True

    def refund_llm(self, count: int) -> None:
        self.llm_calls = max(0, self.llm_calls - max(0, int(count)))

    def allow_tool(self) -> bool:
        if self.expired() or self.tool_calls + 1 > self.max_tool_calls:
            return False
        self.tool_calls += 1
        return True


def malformed_required_fields(candidate: Mapping[str, Any]) -> bool:
    triple = candidate.get("triple") if isinstance(candidate.get("triple"), Mapping) else candidate
    return not all(str(triple.get(key) or "").strip() for key in ("head", "tail")) or not str(
        triple.get("relation_id") or triple.get("surface_relation") or triple.get("relation") or ""
    ).strip()


def bounded_evidence_bundle(
    bundle: Mapping[str, Any], *, max_items: int, max_chars: int,
) -> Dict[str, Any]:
    selected = []
    used_chars = 0
    for item in bundle.get("items") or []:
        if len(selected) >= max(0, max_items):
            break
        item_chars = len(str(item.get("quote") or item.get("text") or ""))
        if item_chars + used_chars > max(0, max_chars):
            continue
        selected.append(item)
        used_chars += item_chars
    limited = {**dict(bundle), **make_evidence_bundle(selected, bundle.get("searched_levels") or [])}
    limited["bounded_character_count"] = used_chars
    limited["truncated_by_budget"] = len(selected) < len(bundle.get("items") or [])
    limited["evidence_hash"] = sha256_json({k: v for k, v in limited.items() if k != "evidence_hash"})
    return limited


class ReviewOrchestrator:
    def __init__(
        self, repository: ReviewRepository, ontology_guard: Any, validators: Iterable[Mapping[str, Any]],
        *, calibrator: Optional[TripleCalibrator] = None, risk_policy: Optional[RiskPolicy] = None,
        config: Optional[Mapping[str, Any]] = None,
        planner_client=None, should_cancel=None,
    ):
        self.repository = repository
        self.ontology_guard = ontology_guard
        self.validators = list(validators)
        self.calibrator = calibrator or TripleCalibrator()
        self.risk_policy = risk_policy or RiskPolicy()
        self.config = dict(config or {})
        self.planner_client = planner_client
        self.should_cancel = should_cancel
        consensus_config = dict(self.config.get("consensus") or {})
        voter_ids = [str(item["id"]) for item in self.validators]
        committee_size = int(consensus_config.get("committee_size", 4))
        if len(voter_ids) > committee_size:
            raise ValueError("configured validators exceed the frozen committee size")
        voter_ids.extend(
            f"unconfigured_{index + 1}" for index in range(committee_size - len(voter_ids))
        )
        required = int(consensus_config.get("required_votes", 3))
        self.committee = Committee(
            voter_ids,
            required_votes=required,
            minimum_distinct_families=int(consensus_config.get("minimum_distinct_families", 3)),
            version=str(consensus_config.get("version", "committee_v1")),
        )

    def committee_members(self):
        return [{"id": v["id"],
            "model_id": v.get("model_id") or getattr(v["client"], "model_id", ""),
            "model_family": v.get("model_family") or getattr(v["client"], "model_family", "")}
            for v in self.validators]

    def _proposal(
        self, audit_id: str, candidate: Mapping[str, Any], guard: Mapping[str, Any],
        bundle: Mapping[str, Any], round_id: int, context: Mapping[str, Any],
    ) -> Dict[str, Any]:
        seed = {
            "candidate_id": candidate["candidate_id"], "revision": candidate["revision"],
            "canonical_triple": canonical_triple(candidate),
            "document_version": candidate["document_version"],
            "ontology_version": candidate["ontology_version"],
            "evidence_bundle_hash": bundle["evidence_hash"],
            "prompt_version": PROMPT_VERSION,
            "response_contract_version": CONTRACT_VERSION,
            "evidence_bundle_version": EVIDENCE_BUNDLE_VERSION,
            "rubric_version": context.get("rubric_version", "triple_review_rubric_v1"),
            "committee_version": self.committee.version,
            "committee_members": self.committee_members(),
            "memory_snapshot_id": context.get("memory_snapshot_id", 0),
            "memory_bundle_hash": context.get("memory_bundle_hash"), "round_id": round_id,
            **({"agent_versions": context["agent_versions"]} if context.get("agent_versions") else {}),
        }
        return {
            **seed, "audit_id": audit_id, "proposal_digest": proposal_digest(seed),
            "candidate": dict(candidate), "ontology_check": dict(guard),
            "evidence_bundle": dict(bundle), "review_memory": context.get("review_memory", {}),
            "request_cache_namespace": context.get("request_cache_namespace", "default"),
        }

    def audit_candidate(
        self, candidate_id: str, revision: int, document_snapshot: Mapping[str, Any],
        *, context: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        planner = self.config.get("planner") or {}
        if planner.get("enabled") and planner.get("strategy") == "llm_react":
            from .runtime import AgentRuntime
            return AgentRuntime(self).run(candidate_id, revision, document_snapshot, context=context)
        if planner.get("strategy", "deterministic") not in {"deterministic", "llm_react"}:
            raise ValueError("unsupported planner strategy")
        return self._audit_deterministic(candidate_id, revision, document_snapshot, context=context)

    def _audit_deterministic(
        self, candidate_id: str, revision: int, document_snapshot: Mapping[str, Any],
        *, context: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        planner_config = dict(self.config.get("planner") or {})
        context = {
            "mode": self.config.get("mode", "shadow"),
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "planner_strategy": planner_config.get("strategy", "deterministic"),
            "orchestrator_kind": "bounded_state_machine",
            "configured_strategy": planner_config.get("strategy", "deterministic"),
            "executed_strategy": "deterministic",
            **dict(context or {}),
            "prompt_version": PROMPT_VERSION,
            "response_contract_version": CONTRACT_VERSION,
            "evidence_bundle_version": EVIDENCE_BUNDLE_VERSION,
            "committee_version": self.committee.version,
        }
        candidate = self.repository.get_revision(candidate_id, revision)
        memory_config = dict(self.config.get("memory") or {})
        if memory_config.get("enabled", True):
            memory = ReviewMemory(self.repository)
            snapshot_id = int(context.get("memory_snapshot_id", memory.current_version()))
            triple = candidate.get("triple") or candidate
            memory_bundle = memory.lookup(
                relation_id=triple.get("relation_id"), domain=candidate.get("domain"),
                snapshot_id=snapshot_id, exclude_candidate_id=candidate_id,
                max_positive=int(memory_config.get("max_positive_cases", 3)),
                max_negative=int(memory_config.get("max_negative_cases", 3)),
            )
            context.update({
                "memory_snapshot_id": snapshot_id, "review_memory": memory_bundle,
                "memory_bundle_hash": sha256_json(memory_bundle),
            })
        audit_id = self.repository.start_audit(candidate_id, revision, context)
        guard = self.ontology_guard.check(candidate)
        budget_config = {**dict(self.config.get("budget") or {}),
                         "max_rounds": (self.config.get("consensus") or {}).get("max_rounds", 2)}
        budget = Budget(budget_config)
        max_evidence_items = max(0, int(budget_config.get("max_evidence_items", 12)))
        max_evidence_chars = max(0, int(budget_config.get("max_evidence_chars", 12_000)))
        bundle = bounded_evidence_bundle(
            verify_evidence_bundle(retrieve_initial(
                dict(candidate), dict(document_snapshot),
                max_items=min(6, max_evidence_items),
            ), document_snapshot, candidate),
            max_items=max_evidence_items, max_chars=max_evidence_chars,
        )
        last_votes: List[Dict[str, Any]] = []
        consensus: Dict[str, Any] = aggregate_votes([], self.committee)
        gaps: Dict[str, Any] = {"need_more_evidence": False}
        final_round = 0
        for round_id in range(1, budget.max_rounds + 1):
            final_round = round_id
            proposal = self._proposal(audit_id, candidate, guard, bundle, round_id, context)
            configured_retries = max(0, int(
                (self.config.get("budget") or {}).get("max_retries_per_request", 1)
            ))
            base_calls = len(self.validators)
            remaining_calls = max(0, budget.max_llm_calls - budget.llm_calls)
            effective_retries = min(
                configured_retries,
                max(0, (remaining_calls - base_calls) // max(1, base_calls)),
            )
            reserved_calls = base_calls * (1 + effective_retries)
            calls_allowed = bool(self.validators and bundle.get("items") and budget.allow_llm(reserved_calls))
            call_budget_exhausted = bool(self.validators and bundle.get("items") and not calls_allowed)
            if calls_allowed:
                try:
                    last_votes = judge_independently(
                        self.validators, proposal,
                        max_workers=int((self.config.get("budget") or {}).get("max_parallel_validators", 4)),
                        max_retries=effective_retries,
                        repository=self.repository,
                        per_call_timeout_seconds=min(
                            float(budget_config.get("per_call_timeout_seconds", 45)),
                            max(.001, budget.total_timeout_seconds - (time.monotonic() - budget.started_at))),
                    )
                except AuditPaused as exc:
                    self.repository.append_round(audit_id, proposal, [exc.vote],
                        aggregate_votes([exc.vote], self.committee, proposal_digest=proposal["proposal_digest"]),
                        {"stop_reason": "insufficient_balance", "pause_job": True})
                    self.repository.finish_incomplete_audit(audit_id, "paused", {
                        "reason_code": "INSUFFICIENT_BALANCE", "voter_id": exc.vote.get("voter_id"),
                        "error": str(exc),
                    })
                    raise
                actual_calls = sum(model_request_count(vote) for vote in last_votes)
                budget.refund_llm(reserved_calls - actual_calls)
            else:
                last_votes = []
            preliminary = aggregate_votes(last_votes, self.committee, proposal_digest=proposal["proposal_digest"])
            material_counter = any(
                vote.get("response_status") == "ok" and vote.get("verdict") == "invalid"
                and vote.get("counter_evidence_ids") for vote in last_votes
            ) and preliminary.get("valid_votes", 0) > 0
            material_support = any(
                vote.get("response_status") == "ok" and vote.get("source_support") == "supported"
                and vote.get("supporting_evidence_ids") for vote in last_votes
            ) and any(v.get("response_status") == "ok" and v.get("source_support") == "contradicted"
                      and v.get("counter_evidence_ids") for v in last_votes)
            material_reason = any(
                set(vote.get("reason_codes") or []) & MATERIAL_GAP_CODES for vote in last_votes
                if vote.get("response_status") == "ok"
            )
            consensus = aggregate_votes(
                last_votes, self.committee, proposal_digest=proposal["proposal_digest"],
                unresolved_counterevidence=material_counter,
                unresolved_support_conflict=material_support,
            )
            gaps = {
                "need_more_evidence": bool(material_counter or material_support or material_reason
                    or not bundle.get("items") or any(v.get("requested_context") or
                    (v.get("response_status") == "ok" and v.get("source_support") == "insufficient") for v in last_votes)),
                "unresolved_counterevidence": material_counter,
                "unresolved_support_conflict": material_support,
                "ontology_disagreement": any(v.get("ontology_disagreement") for v in last_votes),
                "material_reason": material_reason,
                "budget_exhausted": call_budget_exhausted,
            }
            self.repository.append_round(audit_id, proposal, last_votes, consensus, gaps)
            if call_budget_exhausted or not gaps["need_more_evidence"] or round_id >= budget.max_rounds:
                gaps["stop_reason"] = ("call_budget_exhausted" if call_budget_exhausted else
                    "round_limit" if gaps["need_more_evidence"] else
                    "committee_unresolved" if consensus["result"] == "unresolved" else "resolved")
                self.repository.update_round_gaps(audit_id, round_id, gaps)
                break
            request = next((vote.get("requested_context") for vote in last_votes if vote.get("requested_context")), None)
            request = dict(request or {"scope": "current_chunk", "query": "补充限定条件与反证", "limit": 4})
            trace, extra_items = [], []
            scopes = list(CONTEXT_SCOPES)
            first = scopes.index(request["scope"])
            retrieval_candidate = dict(candidate)
            if bundle.get("items"):
                retrieval_candidate["source_chunk_id"] = bundle["items"][0]["chunk_id"]
            for scope in scopes[first:]:
                if not budget.allow_tool():
                    gaps["budget_exhausted"] = True
                    break
                actual_request = {**request, "scope": scope}
                extra = verify_evidence_bundle(retrieve_more(
                    retrieval_candidate, dict(document_snapshot), actual_request,
                    [item["evidence_id"] for item in bundle.get("items", [])],
                    max_chars=max_evidence_chars,
                ), document_snapshot, candidate)
                trace.append({"request": actual_request, "returned": len(extra["items"]),
                              "rejected_items": extra.get("rejected_items", [])})
                if extra.get("items"):
                    extra_items = extra["items"]
                    break
            gaps["retrieval_trace"] = trace
            if not extra_items:
                gaps["stop_reason"] = "budget_exhausted" if gaps["budget_exhausted"] else "no_new_evidence"
                self.repository.update_round_gaps(audit_id, round_id, gaps)
                break
            next_bundle = bounded_evidence_bundle(
                verify_evidence_bundle({**make_evidence_bundle(
                    [*bundle.get("items", []), *extra_items],
                    [*bundle.get("searched_levels", []), *[t["request"]["scope"] for t in trace]],
                ), "rejected_items": bundle.get("rejected_items", [])}, document_snapshot, candidate),
                max_items=max_evidence_items, max_chars=max_evidence_chars,
            )
            if {i["evidence_id"] for i in next_bundle["items"]} == {i["evidence_id"] for i in bundle["items"]}:
                gaps["stop_reason"] = "evidence_budget_no_change"
                self.repository.update_round_gaps(audit_id, round_id, gaps)
                break
            bundle = next_bundle
            self.repository.update_round_gaps(audit_id, round_id, gaps)

        return self._finalize(candidate, context, audit_id, guard, bundle, last_votes, consensus, gaps,
                              final_round, {"llm_calls": budget.llm_calls, "tool_calls": budget.tool_calls})

    def _finalize(self, candidate, context, audit_id, guard, bundle, last_votes, consensus, gaps,
                  final_round, usage):
        candidate_id, revision = candidate["candidate_id"], candidate["revision"]
        features = TripleFeatureExtractor().extract_v3(
            candidate, guard, bundle, last_votes, consensus,
            round_count=final_round,
            retrieval_depth=len(bundle.get("searched_levels", [])),
        )
        calibration = self.calibrator.predict_if_compatible(features, {
            "domain": candidate.get("domain"), "committee_version": self.committee.version,
            "prompt_version": PROMPT_VERSION,
            "response_contract_version": CONTRACT_VERSION,
            "evidence_bundle_version": EVIDENCE_BUNDLE_VERSION,
            **{key: context[key] for key in context if key.startswith("planner_") or key in {"executed_strategy", "configured_strategy"}},
        })
        route = self.risk_policy.route_v3(
            guard=guard, evidence=bundle, consensus=consensus, probability=calibration,
            gaps=gaps, frozen=context, malformed_required_fields=malformed_required_fields(candidate),
        )
        routing = dict(self.config.get("routing") or {})
        route_disabled = (
            route["decision"] == "auto_accept" and not routing.get("auto_accept_enabled", False)
        ) or (
            route["decision"] == "auto_reject" and not routing.get("semantic_auto_reject_enabled", False)
        )
        if route_disabled:
            route = {
                "decision": "manual_review",
                "reason_codes": [*route.get("reason_codes", []), "AUTOMATIC_ROUTE_DISABLED"],
            }
        elif route["decision"] in {"auto_accept", "auto_reject"} and context.get("mode") != "active":
            route = {
                "decision": "manual_review",
                "reason_codes": [*route.get("reason_codes", []), "NON_ACTIVE_MODE_REQUIRES_HUMAN"],
            }
        explanation = explain_from_verified_records(candidate, guard, bundle, last_votes, route)
        result = {
            "audit_id": audit_id, "candidate_id": candidate_id, "revision": revision,
            "final_round_id": final_round, "consensus": consensus,
            "calibrated_probability": calibration.probability,
            "calibration_status": calibration.status, "calibrator_version": calibration.model_version,
            "policy_version": self.risk_policy.version,
            "decision": route["decision"], "reason_codes": route.get("reason_codes", []),
            "review_versions": {field: context[field] for field in (
                "prompt_version", "response_contract_version", "evidence_bundle_version", "committee_version",
                "planner_prompt_version", "planner_contract_version", "planner_strategy_version", "planner_model_id",
                "planner_model_family", "configured_strategy", "executed_strategy") if field in context},
            "features": features, "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "explanation": explanation, "budget": usage,
            "configured_strategy": context.get("configured_strategy", "deterministic"),
            "executed_strategy": context.get("executed_strategy", "deterministic"),
            "stop_reason": gaps.get("stop_reason"),
        }
        result["committed"] = self.repository.commit_audit_if_current(
            candidate_id, revision, audit_id, result
        )
        return result
