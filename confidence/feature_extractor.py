"""Feature extraction for calibrated triple confidence."""

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
            "evidence_entailment": _float(best_evidence.get("entailment_score")),
            "evidence_exact_match": 1.0 if best_evidence.get("match_type") == "exact_match" else 0.0,
            "head_in_evidence": 1.0 if head and head in evidence_text else 0.0,
            "tail_in_evidence": 1.0 if tail and tail in evidence_text else 0.0,
            "support_count": _float(evidence_result.get("support_count")),
            "contradiction_score": _float(best_evidence.get("contradiction_score")),
            "evidence_level": float(EVIDENCE_LEVEL_CODE.get(best_evidence.get("level"), 4)),
            "relation_mapping_score": _float(mapping_result.get("score")),
            "relation_mapping_margin": _float(mapping_result.get("margin")),
            "domain_ok": 1.0 if ontology_result.get("domain_ok") else 0.0,
            "range_ok": 1.0 if ontology_result.get("range_ok") else 0.0,
            "head_type_confidence": _float((type_result.get("head") or {}).get("confidence")),
            "tail_type_confidence": _float((type_result.get("tail") or {}).get("confidence")),
            "extraction_agreement": _float(confidence_result.get("S_evidence")),
        }
