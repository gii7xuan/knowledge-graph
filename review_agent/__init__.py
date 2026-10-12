"""Versioned, evidence-grounded triple review.

Importing this package intentionally has no network, model, database, or file
system side effects.  Applications create repositories and clients explicitly.
"""

from .contracts import (
    ConsensusResult,
    DocumentSupport,
    FinalRoute,
    HumanStatus,
    OntologyStatus,
    ResponseStatus,
    TaskStatus,
    ValidatorVerdict,
    canonical_json,
    fact_key_for,
)

__all__ = [
    "ConsensusResult",
    "DocumentSupport",
    "FinalRoute",
    "HumanStatus",
    "OntologyStatus",
    "ResponseStatus",
    "TaskStatus",
    "ValidatorVerdict",
    "canonical_json",
    "fact_key_for",
]
