"""Versioned feature extraction for triple review calibration."""

from typing import Any, Dict


EVIDENCE_LEVEL_CODE = {
    "local_sentence": 0,
    "adjacent_sentence": 0,
    "current_chunk": 1,
    "same_section": 2,
    "parent_section": 3,
    "other_section": 4,
}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class TripleFeatureExtractor:
    feature_schema_version = "review_features_v3"

    def extract(
        self,
        triplet: Dict[str, Any],
        evidence_result: Dict[str, Any],
        mapping_result: Dict[str, Any],
        type_result: Dict[str, Any],
        ontology_result: Dict[str, Any],
        confidence_result: Dict[str, Any],
    ) -> Dict[str, float]:
        best_evidence = evidence_result.get("best_evidence") or {}
        head = str(triplet.get("head", ""))
        tail = str(triplet.get("tail", ""))
        evidence_text = str(best_evidence.get("text", ""))
        return {
            "quote_located": 1.0 if best_evidence else 0.0,
            "evidence_exact_match": 1.0 if best_evidence.get("match_status") in {"exact", "exact_match"} else 0.0,
            "head_in_evidence": 1.0 if head and head in evidence_text else 0.0,
            "tail_in_evidence": 1.0 if tail and tail in evidence_text else 0.0,
            "unique_span_count": _float(evidence_result.get("retrieved_unique_span_count")),
            "evidence_level": float(EVIDENCE_LEVEL_CODE.get(best_evidence.get("level"), 4)),
            "relation_mapping_score": _float(mapping_result.get("score")),
            "relation_mapping_margin": _float(mapping_result.get("margin")),
            "domain_ok": 1.0 if ontology_result.get("domain_ok") else 0.0,
            "range_ok": 1.0 if ontology_result.get("range_ok") else 0.0,
            "head_type_confidence": _float((type_result.get("head") or {}).get("confidence")),
            "tail_type_confidence": _float((type_result.get("tail") or {}).get("confidence")),
            "legacy_evidence_score": _float(confidence_result.get("S_evidence")),
        }

    def extract_v3(
        self,
        candidate: Dict[str, Any],
        guard: Dict[str, Any],
        evidence: Dict[str, Any],
        votes: Any,
        consensus: Dict[str, Any],
        *,
        round_count: int = 1,
        retrieval_depth: int = 0,
        historical_weighted_support: Any = None,
    ) -> Dict[str, float]:
        votes = list(votes or [])
        committee_size = max(1, int(consensus.get("committee_size") or len(votes) or 1))
        reason_codes = {
            code for vote in votes if vote.get("response_status") == "ok"
            for code in (vote.get("reason_codes") or [])
        }
        return {
            "quote_located": 1.0 if evidence.get("items") else 0.0,
            "evidence_missing": 0.0 if evidence.get("items") else 1.0,
            "unique_span_count": _float(evidence.get("retrieved_unique_span_count", len(evidence.get("items", [])))),
            "source_group_count": _float(evidence.get("independent_document_count")),
            "relation_known": 1.0 if guard.get("relation_known") else 0.0,
            "domain_pass": 1.0 if guard.get("domain_status") == "pass" else 0.0,
            "range_pass": 1.0 if guard.get("range_status") == "pass" else 0.0,
            "type_unknown": 1.0 if guard.get("type_unknown") else 0.0,
            "mapping_ambiguous": 1.0 if guard.get("mapping_ambiguous") else 0.0,
            "supporting_vote_count": _float(consensus.get("valid_votes")),
            "contradicting_vote_count": _float(consensus.get("invalid_votes")),
            "qualifier_issue": 1.0 if "CONDITION_MISSING" in reason_codes or "QUALIFIER_MISMATCH" in reason_codes else 0.0,
            "direction_issue": 1.0 if "DIRECTION_MISMATCH" in reason_codes else 0.0,
            "valid_vote_fraction": _float(consensus.get("valid_votes")) / committee_size,
            "invalid_vote_fraction": _float(consensus.get("invalid_votes")) / committee_size,
            "uncertain_fraction": _float(consensus.get("uncertain_votes")) / committee_size,
            "error_fraction": (_float(consensus.get("error_votes")) + _float(consensus.get("missing_votes"))) / committee_size,
            "distinct_family_count": _float(consensus.get("distinct_family_count")),
            "disagreement": 1.0 if consensus.get("valid_votes") and consensus.get("invalid_votes") else 0.0,
            "unresolved_counterevidence": 1.0 if consensus.get("unresolved_counterevidence") else 0.0,
            "retrieval_depth": float(retrieval_depth),
            "round_count": float(round_count),
            "historical_weighted_support": _float(historical_weighted_support),
            "reliability_available": 0.0 if historical_weighted_support is None else 1.0,
        }
