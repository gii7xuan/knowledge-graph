"""Trainable probability calibrator for triples."""

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


FEATURE_ORDER = [
    "evidence_entailment",
    "evidence_exact_match",
    "head_in_evidence",
    "tail_in_evidence",
    "support_count",
    "contradiction_score",
    "evidence_level",
    "relation_mapping_score",
    "relation_mapping_margin",
    "domain_ok",
    "range_ok",
    "head_type_confidence",
    "tail_type_confidence",
    "extraction_agreement",
]


def feature_vector(features: Dict[str, Any]) -> List[float]:
    values = []
    for key in FEATURE_ORDER:
        try:
            values.append(float(features.get(key, 0.0)))
        except (TypeError, ValueError):
            values.append(0.0)
    return values


class TripleCalibrator:
    """A small logistic calibrator with a deterministic fallback."""

    def __init__(self, model: Any = None, version: str = "heuristic_v1"):
        self.model = model
        self.version = version

    def fit(self, features: Iterable[Dict[str, Any]], labels: Iterable[int]) -> None:
        from sklearn.linear_model import LogisticRegression

        x = [feature_vector(item) for item in features]
        y = list(labels)
        model = LogisticRegression(class_weight="balanced", max_iter=1000)
        model.fit(x, y)
        self.model = model
        self.version = "logistic_regression_v1"

    def predict_proba(self, features: Dict[str, Any], raw_score: Optional[float] = None) -> float:
        if self.model is not None:
            return float(self.model.predict_proba([feature_vector(features)])[0, 1])
        if raw_score is not None:
            try:
                return max(0.0, min(1.0, float(raw_score)))
            except (TypeError, ValueError):
                pass
        signal = (
            0.30 * float(features.get("evidence_entailment", 0.0))
            + 0.18 * float(features.get("relation_mapping_score", 0.0))
            + 0.14 * float(features.get("domain_ok", 0.0))
            + 0.14 * float(features.get("range_ok", 0.0))
            + 0.12 * float(features.get("head_type_confidence", 0.0))
            + 0.12 * float(features.get("tail_type_confidence", 0.0))
        )
        penalty = 0.08 * float(features.get("contradiction_score", 0.0))
        return max(0.0, min(1.0, signal - penalty))

    def save(self, path: str) -> None:
        import joblib

        joblib.dump({"version": self.version, "model": self.model}, path)

    @classmethod
    def load(cls, path: str) -> "TripleCalibrator":
        import joblib

        payload = joblib.load(path)
        return cls(model=payload.get("model"), version=payload.get("version", "loaded"))


def load_calibrator(path: str = "confidence/calibrator.joblib") -> TripleCalibrator:
    file_path = Path(path)
    if not file_path.exists():
        return TripleCalibrator()
    return TripleCalibrator.load(str(file_path))
