"""Risk routing policy for calibrated triples."""

import json
from pathlib import Path
from typing import Iterable, List, Optional


DEFAULT_POLICY = {
    "target_precision": 0.95,
    "accept_threshold": 0.87,
    "reject_threshold": 0.0,
    "validation_document_ids": [],
    "policy_version": "precision95_heuristic_v1",
}


def find_accept_threshold(
    labels: Iterable[int],
    probabilities: Iterable[float],
    target_precision: float = 0.95,
) -> float:
    labels = list(labels)
    probabilities = list(probabilities)
    valid: List[float] = []
    for threshold in sorted(set(probabilities)):
        selected = [y for y, p in zip(labels, probabilities) if p >= threshold]
        if selected and sum(selected) / len(selected) >= target_precision:
            valid.append(threshold)
    return min(valid) if valid else 1.0


class RiskPolicy:
    def __init__(self, policy: Optional[dict] = None):
        self.policy = dict(DEFAULT_POLICY)
        if policy:
            self.policy.update(policy)

    @property
    def version(self) -> str:
        return self.policy.get("policy_version", "unknown")

    def route(self, probability: float) -> str:
        probability = max(0.0, min(1.0, float(probability)))
        if probability >= float(self.policy.get("accept_threshold", 1.0)):
            return "auto_accept"
        reject_threshold = float(self.policy.get("reject_threshold", 0.0))
        if reject_threshold > 0 and probability <= reject_threshold:
            return "auto_reject"
        return "manual_review"


def load_policy(path: str = "confidence/risk_policy.json") -> RiskPolicy:
    file_path = Path(path)
    if not file_path.exists():
        return RiskPolicy()
    with file_path.open("r", encoding="utf-8") as file:
        return RiskPolicy(json.load(file))
