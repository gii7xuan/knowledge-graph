"""Evaluation adapters expose one prediction interface per implemented method."""

from .baselines import simple_majority, single_judge

__all__ = ["simple_majority", "single_judge"]
