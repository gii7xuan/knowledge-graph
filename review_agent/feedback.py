"""Human feedback semantics and optimistic-concurrency submission."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .repository import ReviewRepository, UnprocessableEntityError, ValidationError


NON_FACTUAL_REJECTIONS = {"DUPLICATE", "OUT_OF_SCOPE", "NOT_RELEVANT_TO_TASK", "SCHEMA_GAP", "SCHEMA_UNRESOLVED", "REJECT_FOR_SCHEMA", "SCHEMA_INCOMPATIBLE"}
FACTUAL_REJECTIONS = {
    "FACT_INCORRECT", "NEGATION_IGNORED", "DIRECTION_MISMATCH", "CONDITION_MISSING",
    "RELATION_MISMATCH", "ENTITY_MISMATCH", "SOURCE_CONTRADICTS",
}


class FeedbackService:
    def __init__(self, repository: ReviewRepository, *, ontology_guard: Any = None):
        self.repository = repository
        self.ontology_guard = ontology_guard

    def _labels(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        action = str(payload.get("action") or "")
        reason = str(payload.get("reason_code") or "")
        submitted = dict(payload.get("labels") or {})
        if action in {"accept", "accept_corrected"}:
            if submitted.get("valid_for_kg") not in (None, 1):
                raise ValidationError("accept cannot carry a negative valid_for_kg label")
            submitted["valid_for_kg"] = 1
            if submitted.get("source_support") not in {None, "supported"}: raise UnprocessableEntityError("accept requires human-confirmed source support")
            submitted.setdefault("source_support", "supported")
        elif action == "reject":
            if not reason:
                raise ValidationError("reject requires a reason_code")
            expected = 0 if reason in FACTUAL_REJECTIONS else None
            if submitted.get("valid_for_kg") not in (None, expected):
                raise ValidationError("reject label conflicts with reason_code")
            submitted["valid_for_kg"] = expected
            if expected == 0 and reason in FACTUAL_REJECTIONS:
                submitted.setdefault("source_support", "contradicted")
        elif action == "defer":
            if submitted.get("valid_for_kg") is not None:
                raise ValidationError("defer must not create a truth label")
            submitted = {key: None for key in submitted}
            submitted["valid_for_kg"] = None
        elif action == "modify":
            original_label = 0 if reason in FACTUAL_REJECTIONS else None
            if submitted.get("valid_for_kg") not in (None, original_label):
                raise ValidationError("modify label conflicts with the original-version reason")
            submitted["valid_for_kg"] = original_label
            submitted["new_revision_valid_for_kg"] = None
        else:
            raise ValidationError("unsupported feedback action")
        return submitted

    def submit(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        request = dict(payload)
        action = str(request.get("action") or request.get("decision") or "")
        request["action"] = action
        request["labels"] = self._labels(request)
        # Repository resolves idempotency/CAS before the shared gate loads the
        # actual immutable release. A caller-supplied guard cannot grant admission.
        event = self.repository.apply_human_feedback(request)
        return {"ok": True, "event": event, "candidate": self.repository.get_candidate(request["candidate_id"])}


def submit_feedback(repository: ReviewRepository, payload: Mapping[str, Any], *, ontology_guard: Any = None) -> Dict[str, Any]:
    return FeedbackService(repository, ontology_guard=ontology_guard).submit(payload)
