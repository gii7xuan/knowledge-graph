"""Confidence helpers without model-heavy legacy initialization on import."""

__all__ = ["ConfidenceScorer"]


def __getattr__(name):
    if name == "ConfidenceScorer":
        from .confidence_scorer import ConfidenceScorer
        return ConfidenceScorer
    raise AttributeError(name)
