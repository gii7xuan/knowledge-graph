"""Shared evidence span location helpers."""

try:
    from ontologyBuild.evidence_validator import find_evidence_span
except ImportError:  # pragma: no cover
    from ontologyBuild.evidence_validator import find_evidence_span

__all__ = ["find_evidence_span"]
