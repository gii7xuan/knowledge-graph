"""Bounded evidence-and-validation state machine."""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Mapping, Optional

from confidence.calibrator import TripleCalibrator
from confidence.feature_extractor import TripleFeatureExtractor
from confidence.risk_policy import RiskPolicy
from evidence.hierarchical_retriever import retrieve_initial, retrieve_more
from evidence.verification import make_evidence_bundle

from .consensus import Committee, aggregate_votes
from .contracts import FEATURE_SCHEMA_VERSION, canonical_triple, proposal_digest, sha256_json
from .explanation import explain_from_verified_records
from .memory import ReviewMemory
from .repository import ReviewRepository
from .validators import judge_independently


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
    limited = make_evidence_bundle(selected, bundle.get("searched_levels") or [])
    limited["bounded_character_count"] = used_chars
    limited["truncated_by_budget"] = len(selected) < len(bundle.get("items") or [])
    return limited


class ReviewOrchestrator:
    def __init__(
        self, repository: ReviewRepository, ontology_guard: Any, validators: Iterable[Mapping[str, Any]],
        *, calibrator: Optional[TripleCalibrator] = None, risk_policy: Optional[RiskPolicy] = None,
        config: Optional[Mapping[str, Any]] = None,
    ):
        self.repository = repository
        self.ontology_guard = ontology_guard
        self.validators = list(validators)
        self.calibrator = calibrator or TripleCalibrator()
        self.risk_policy = risk_policy or RiskPolicy()
        self.config = dict(config or {})
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
            "prompt_version": context.get("prompt_version", "validator_v1"),
            "rubric_version": context.get("rubric_version", "triple_review_rubric_v1"),
            "committee_version": self.committee.version,
            "memory_snapshot_id": context.get("memory_snapshot_id", 0),
            "memory_bundle_hash": context.get("memory_bundle_hash"), "round_id": round_id,
        }
        return {
            **seed, "audit_id": audit_id, "proposal_digest": proposal_digest(seed),
            "candidate": dict(candidate), "ontology_check": dict(guard),
            "evidence_bundle": dict(bundle), "review_memory": context.get("review_memory", {}),
        }

    def audit_candidate(
        self, candidate_id: str, revision: int, document_snapshot: Mapping[str, Any],
        *, context: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        planner_config = dict(self.config.get("planner") or {})
        context = {
            "mode": self.config.get("mode", "shadow"),
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "planner_strategy": planner_config.get("strategy", "deterministic"),
            "orchestrator_kind": "bounded_state_machine",
            **dict(context or {}),
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
            retrieve_initial(
                dict(candidate), dict(document_snapshot),
                max_items=min(6, max_evidence_items),
            ),
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
            if self.validators and budget.allow_llm(reserved_calls):
                last_votes = judge_independently(
                    self.validators, proposal,
                    max_workers=int((self.config.get("budget") or {}).get("max_parallel_validators", 4)),
                    max_retries=effective_retries,
                )
                actual_calls = sum(len(vote.get("attempts") or [vote]) for vote in last_votes)
                budget.refund_llm(reserved_calls - actual_calls)
            else:
                last_votes = []
            preliminary = aggregate_votes(last_votes, self.committee, proposal_digest=proposal["proposal_digest"])
            material_counter = any(
                vote.get("response_status") == "ok" and vote.get("verdict") == "invalid"
                and vote.get("counter_evidence_ids") for vote in last_votes
            ) and preliminary.get("valid_votes", 0) >= self.committee.required_votes
            material_reason = any(
                set(vote.get("reason_codes") or []) & MATERIAL_GAP_CODES for vote in last_votes
                if vote.get("response_status") == "ok"
            )
            consensus = aggregate_votes(
                last_votes, self.committee, proposal_digest=proposal["proposal_digest"],
                unresolved_counterevidence=material_counter,
            )
            gaps = {
                "need_more_evidence": bool(material_counter or material_reason or not bundle.get("items")),
                "unresolved_counterevidence": material_counter,
                "material_reason": material_reason,
                "budget_exhausted": False,
            }
            self.repository.append_round(audit_id, proposal, last_votes, consensus, gaps)
            if not gaps["need_more_evidence"] or round_id >= budget.max_rounds:
                break
            if not budget.allow_tool():
                gaps["budget_exhausted"] = True
                break
            request = next((vote.get("requested_context") for vote in last_votes if vote.get("requested_context")), None)
            request = dict(request or {"scope": "same_section", "query": "补充限定条件与反证", "limit": 4})
            extra = retrieve_more(candidate, dict(document_snapshot), request,
                                  [item["evidence_id"] for item in bundle.get("items", [])])
            if not extra.get("items"):
                break
            bundle = bounded_evidence_bundle(
                make_evidence_bundle(
                    [*bundle.get("items", []), *extra.get("items", [])],
                    [*bundle.get("searched_levels", []), *extra.get("searched_levels", [])],
                ),
                max_items=max_evidence_items, max_chars=max_evidence_chars,
            )

        features = TripleFeatureExtractor().extract_v3(
            candidate, guard, bundle, last_votes, consensus,
            round_count=final_round,
            retrieval_depth=len(bundle.get("searched_levels", [])),
        )
        calibration = self.calibrator.predict_if_compatible(features, {
            "domain": candidate.get("domain"), "committee_version": self.committee.version,
            "prompt_version": context.get("prompt_version", "validator_v1"),
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
            "features": features, "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "explanation": explanation, "budget": {"llm_calls": budget.llm_calls, "tool_calls": budget.tool_calls},
        }
        result["committed"] = self.repository.commit_audit_if_current(
            candidate_id, revision, audit_id, result
        )
        return result
