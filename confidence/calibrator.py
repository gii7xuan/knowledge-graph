"""Version-compatible probability calibration; cold start never invents a probability."""

from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from review_agent.contracts import CalibrationResult, FEATURE_SCHEMA_VERSION


FEATURE_ORDER = [
    "quote_located", "evidence_missing", "unique_span_count", "source_group_count",
    "relation_known", "domain_pass", "range_pass", "type_unknown", "mapping_ambiguous",
    "supporting_vote_count", "contradicting_vote_count", "qualifier_issue", "direction_issue",
    "valid_vote_fraction", "invalid_vote_fraction", "uncertain_fraction", "error_fraction",
    "distinct_family_count", "disagreement", "unresolved_counterevidence", "retrieval_depth",
    "round_count", "historical_weighted_support", "reliability_available",
]


def feature_vector(features: Mapping[str, Any], feature_order: Optional[Iterable[str]] = None) -> List[float]:
    values = []
    for key in feature_order or FEATURE_ORDER:
        value = features.get(key)
        try:
            values.append(float(value) if value is not None else 0.0)
        except (TypeError, ValueError):
            values.append(0.0)
    return values


class TripleCalibrator:
    def __init__(
        self, model: Any = None, version: str = "untrained", *,
        feature_schema_version: str = FEATURE_SCHEMA_VERSION,
        feature_order: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None,
    ):
        self.model = model
        self.version = version
        self.feature_schema_version = feature_schema_version
        self.feature_order = list(feature_order or FEATURE_ORDER)
        self.metadata = dict(metadata or {})

    @property
    def ready(self) -> bool:
        return self.model is not None and bool(self.metadata.get("trained_with_both_classes", True))

    def fit(
        self, features: Iterable[Mapping[str, Any]], labels: Iterable[int], *,
        training_data_version: str = "unspecified", metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        from sklearn.linear_model import LogisticRegression
        feature_rows = list(features)
        y = [int(value) for value in labels]
        if len(set(y)) < 2:
            raise ValueError("calibrator training requires both classes")
        model = LogisticRegression(max_iter=1000)
        model.fit([feature_vector(item, self.feature_order) for item in feature_rows], y)
        self.model = model
        self.version = "logistic_regression_v3"
        self.metadata = {
            **dict(metadata or {}), "training_data_version": training_data_version,
            "trained_with_both_classes": True,
            "missing_value_strategy": "zero_with_explicit_flags",
        }

    def predict_result(
        self, features: Mapping[str, Any], *,
        feature_schema_version: str = FEATURE_SCHEMA_VERSION,
        context: Optional[Mapping[str, Any]] = None,
    ) -> CalibrationResult:
        if not self.ready:
            return CalibrationResult(None, "unavailable", self.version, feature_schema_version, False,
                                     ["CALIBRATOR_NOT_READY"])
        if feature_schema_version != self.feature_schema_version:
            return CalibrationResult(None, "incompatible", self.version, feature_schema_version, False,
                                     ["FEATURE_SCHEMA_MISMATCH"])
        context = dict(context or {})
        for field in ("domain", "committee_version", "prompt_version"):
            allowed = self.metadata.get(field)
            actual = context.get(field)
            if allowed and actual and allowed != actual:
                return CalibrationResult(None, "incompatible", self.version, feature_schema_version, False,
                                         [f"{field.upper()}_MISMATCH"])
        probability = float(self.model.predict_proba([feature_vector(features, self.feature_order)])[0, 1])
        return CalibrationResult(max(0.0, min(1.0, probability)), "ok", self.version,
                                 feature_schema_version, True, [])

    def predict_if_compatible(
        self, features: Mapping[str, Any], context: Optional[Mapping[str, Any]] = None
    ) -> CalibrationResult:
        return self.predict_result(features, context=context)

    def predict_proba(
        self, features: Mapping[str, Any], raw_score: Optional[float] = None
    ) -> Optional[float]:
        """Compatibility method. raw_score is deliberately ignored at cold start."""
        return self.predict_result(features).probability

    def save(self, path: str) -> None:
        import joblib
        joblib.dump({
            "version": self.version, "model": self.model,
            "feature_schema_version": self.feature_schema_version,
            "feature_order": self.feature_order, "metadata": self.metadata,
        }, path)

    @classmethod
    def load(cls, path: str) -> "TripleCalibrator":
        import joblib
        payload = joblib.load(path)
        return cls(
            model=payload.get("model"), version=payload.get("version", "loaded"),
            feature_schema_version=payload.get("feature_schema_version", "legacy_incompatible"),
            feature_order=payload.get("feature_order") or FEATURE_ORDER,
            metadata=payload.get("metadata") or {},
        )


def load_calibrator(path: str = "confidence/calibrator.joblib") -> TripleCalibrator:
    file_path = Path(path)
    return TripleCalibrator() if not file_path.exists() else TripleCalibrator.load(str(file_path))
