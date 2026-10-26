"""Fail-closed routing for reviewed triples."""

import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


DEFAULT_POLICY = {
    "enabled": False, "target_precision": 0.95, "accept_threshold": None,
    "reject_threshold": None, "min_policy_selected": 100,
    "validation_document_ids": [], "policy_version": "disabled_cold_start_v3",
    "auto_accept_enabled": False, "semantic_auto_reject_enabled": False, "validated": False,
}


def _wilson_lower(positive: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = positive / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - margin) / denominator)


def find_accept_threshold(
    labels: Iterable[int], probabilities: Iterable[float], target_precision: float = 0.95,
    *, min_selected: int = 100, data_version: Optional[str] = None,
) -> Dict[str, Any]:
    pairs = [(int(y), float(p)) for y, p in zip(labels, probabilities)]
    candidates: List[Dict[str, Any]] = []
    for threshold in sorted({p for _, p in pairs}):
        selected = [y for y, p in pairs if p >= threshold]
        if len(selected) < min_selected:
            continue
        precision = sum(selected) / len(selected)
        lower = _wilson_lower(sum(selected), len(selected))
        if precision >= target_precision and lower >= target_precision:
            candidates.append({"threshold": threshold, "selected_count": len(selected),
                               "observed_precision": precision, "wilson_lower": lower})
    if not candidates:
        return {"enabled": False, "threshold": None, "selected_count": 0,
                "observed_precision": None, "wilson_lower": None, "data_version": data_version}
    return {"enabled": True, **min(candidates, key=lambda item: item["threshold"]),
            "data_version": data_version}


class RiskPolicy:
    def __init__(self, policy: Optional[Mapping[str, Any]] = None):
        self.policy = dict(DEFAULT_POLICY)
        if policy:
            self.policy.update(policy)

    @property
    def version(self) -> str:
        return str(self.policy.get("policy_version", "unknown"))

    @property
    def ready(self) -> bool:
        return bool(self.policy.get("enabled") and self.policy.get("validated"))

    def route(self, probability: Optional[float]) -> str:
        if probability is None or not self.ready or not self.policy.get("auto_accept_enabled"):
            return "manual_review"
        threshold = self.policy.get("accept_threshold")
        if threshold is not None and float(probability) >= float(threshold):
            return "auto_accept"
        reject = self.policy.get("reject_threshold")
        if self.policy.get("semantic_auto_reject_enabled") and reject is not None and float(probability) <= float(reject):
            return "auto_reject"
        return "manual_review"

    def route_v3(
        self, *, guard: Mapping[str, Any], evidence: Mapping[str, Any],
        consensus: Mapping[str, Any], probability: Any, gaps: Mapping[str, Any],
        frozen: Optional[Mapping[str, Any]] = None, malformed_required_fields: bool = False,
    ) -> Dict[str, Any]:
        if malformed_required_fields:
            return {"decision": "auto_reject", "reason_codes": ["MALFORMED_REQUIRED_FIELDS"]}
        reasons: List[str] = []
        source_rejected = consensus.get("source_support_consensus", {}).get("result") == "contradicted"
        if gaps.get("identity_unresolved") and not source_rejected:
            reasons.append("CANDIDATE_IDENTITY_UNRESOLVED")
        for flag, code in (("validation_missing", "CURRENT_VALIDATION_MISSING"),
                           ("ontology_not_checked", "ONTOLOGY_CHECK_REQUIRED"),
                           ("critical_issues_unresolved", "CRITICAL_ISSUES_UNRESOLVED"),
                           ("protected_evidence_omitted", "PROTECTED_EVIDENCE_OUTSIDE_PROMPT"),
                           ("unreviewed_discovered_evidence", "DISCOVERED_EVIDENCE_OUTSIDE_PROMPT")):
            if gaps.get(flag):
                reasons.append(code)
        if guard.get("status") != "pass" and not source_rejected:
            reasons.extend(guard.get("reason_codes") or ["ONTOLOGY_NOT_CONFIRMED"])
        if not evidence.get("items"):
            reasons.append("EVIDENCE_MISSING")
        if not evidence.get("anchor_validation_complete") or any(
            item.get("anchor_valid") is not True for item in evidence.get("items") or []
        ):
            reasons.append("EVIDENCE_ANCHOR_UNVERIFIED")
        if gaps.get("unresolved_counterevidence"):
            reasons.append("UNRESOLVED_COUNTEREVIDENCE")
        if gaps.get("unresolved_support_conflict"):
            reasons.append("UNRESOLVED_SUPPORT_CONFLICT")
        if gaps.get("ontology_disagreement"):
            reasons.append("ONTOLOGY_DISAGREEMENT")
        if gaps.get("need_more_evidence"):
            reasons.append("EVIDENCE_GAP_UNRESOLVED")
        if gaps.get("version_stale"):
            reasons.append("VERSION_STALE")
        if reasons:
            return {"decision": "manual_review", "reason_codes": list(dict.fromkeys(reasons))}
        result = consensus.get("result")
        if result not in {"accept_consensus", "reject_consensus"}:
            return {"decision": "manual_review", "reason_codes": ["CONSENSUS_UNRESOLVED"]}
        if not consensus.get("diversity_ready", False):
            return {"decision": "manual_review", "reason_codes": ["MODEL_DIVERSITY_INSUFFICIENT"]}
        value = getattr(probability, "probability", probability)
        compatible = getattr(probability, "compatibility", value is not None)
        if value is None or not compatible:
            return {"decision": "manual_review", "reason_codes": ["CALIBRATOR_NOT_READY"]}
        if not self.ready:
            return {"decision": "manual_review", "reason_codes": ["RISK_POLICY_NOT_READY"]}
        if result == "accept_consensus" and self.policy.get("auto_accept_enabled"):
            threshold = self.policy.get("accept_threshold")
            if threshold is not None and float(value) >= float(threshold):
                return {"decision": "auto_accept", "reason_codes": []}
        if result == "reject_consensus" and self.policy.get("semantic_auto_reject_enabled"):
            threshold = self.policy.get("reject_threshold")
            if threshold is not None and float(value) <= float(threshold):
                return {"decision": "auto_reject", "reason_codes": []}
        return {"decision": "manual_review", "reason_codes": ["POLICY_THRESHOLD_NOT_MET"]}


def load_policy(path: str = "confidence/risk_policy.json") -> RiskPolicy:
    file_path = Path(path)
    return RiskPolicy() if not file_path.exists() else RiskPolicy(json.loads(file_path.read_text(encoding="utf-8")))
