"""Stable v3 contracts and identity helpers.

The dataclasses accept plain dictionaries at the boundaries so legacy JSON can
be migrated without making the rest of the application depend on Pydantic.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional


SCHEMA_VERSION = "triple_review_v3"
FEATURE_SCHEMA_VERSION = "review_features_v3"
EVIDENCE_BUNDLE_VERSION = "evidence_bundle_v2"
REVIEW_VERSION_FIELDS = ("prompt_version", "response_contract_version", "evidence_bundle_version", "committee_version")
AGENT_VERSION_FIELDS = ("planner_prompt_version", "planner_contract_version", "planner_strategy_version",
                       "planner_model_id", "planner_model_family", "configured_strategy", "executed_strategy")


class StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class TaskStatus(StringEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class DocumentSupport(StringEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT = "insufficient"


class OntologyStatus(StringEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class ValidatorVerdict(StringEnum):
    VALID = "valid"
    INVALID = "invalid"
    UNCERTAIN = "uncertain"


class ConsensusResult(StringEnum):
    ACCEPT = "accept_consensus"
    REJECT = "reject_consensus"
    UNRESOLVED = "unresolved"


class FinalRoute(StringEnum):
    AUTO_ACCEPT = "auto_accept"
    AUTO_REJECT = "auto_reject"
    MANUAL_REVIEW = "manual_review"


class CandidateWorkflowState(StringEnum):
    READY = "ready_for_review"
    MANUAL_REVIEW = "manual_review"
    AWAITING_ONTOLOGY_REVIEW = "awaiting_ontology_review"
    AWAITING_ONTOLOGY_RELEASE = "awaiting_ontology_release"
    REAUDIT_REQUIRED = "reaudit_required"
    SCHEMA_UNRESOLVED = "schema_unresolved"
    REVISION_REQUIRED = "revision_required"
    COMPLETED = "review_completed"


class HumanStatus(StringEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    SUPERSEDED = "superseded"


class ImportStatus(StringEnum):
    NOT_QUEUED = "not_queued"
    PENDING = "pending"
    IMPORTED = "imported"
    RETRACTED = "retracted"
    FAILED = "failed"


class ResponseStatus(StringEnum):
    OK = "ok"
    TIMEOUT = "timeout"
    API_ERROR = "api_error"
    INVALID_JSON = "invalid_json"
    INVALID_CONTRACT = "invalid_contract"
    INVALID_REFERENCE = "invalid_reference"
    CANCELLED = "cancelled"


def canonical_json(value: Any) -> str:
    """Return the one canonical representation used for hashes and idempotency."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_fact_part(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return re.sub(r"\s+", "", text)


def normalized_qualifiers(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    source = dict(value or {})
    result: Dict[str, Any] = {
        "polarity": str(source.get("polarity") or "unknown").strip().lower(),
        "condition": source.get("condition"),
        "time_scope": source.get("time_scope"),
        "applicability": source.get("applicability"),
        "modality": source.get("modality"),
        "quantifier": source.get("quantifier"),
    }
    for key in sorted(source):
        if key not in result:
            result[key] = source[key]
    return result


def canonical_triple(candidate_or_triple: Mapping[str, Any]) -> Dict[str, Any]:
    triple = candidate_or_triple.get("triple")
    if not isinstance(triple, Mapping):
        triple = candidate_or_triple
    relation_id = triple.get("relation_id") or triple.get("relation")
    return {
        "head": normalize_fact_part(triple.get("head") or triple.get("subject")),
        "head_entity_id": triple.get("head_entity_id"),
        "relation_id": str(relation_id or "").strip(),
        "tail": normalize_fact_part(triple.get("tail") or triple.get("object")),
        "tail_kind": str(triple.get("tail_kind") or "entity").strip().lower(),
        "tail_entity_id": triple.get("tail_entity_id"),
        "datatype": triple.get("datatype"),
        "value": triple.get("value"),
        "unit": triple.get("unit"),
        "language": triple.get("language"),
        "qualifiers": normalized_qualifiers(triple.get("qualifiers")),
        "qualifier_status": dict(triple.get("qualifier_status") or {}),
    }


def fact_key_for(candidate_or_triple: Mapping[str, Any], namespace: Optional[str] = None) -> str:
    """Compute semantic fact identity, including polarity and other qualifiers."""
    if candidate_or_triple.get("contract_version") == "extraction_candidate_v4":
        if candidate_or_triple.get("fact_key_state") != "canonical":
            return f"provisional:{candidate_or_triple['candidate_id']}:{candidate_or_triple.get('revision', 1)}"
        triple = candidate_or_triple["triple"]
        payload = {"fact_identity_version": candidate_or_triple["fact_identity_version"],
                   "namespace": namespace or candidate_or_triple.get("fact_namespace") or candidate_or_triple.get("domain", "default"),
                   **{key: triple.get(key) for key in ("head_entity_id", "relation_id", "tail_kind", "tail_entity_id", "value", "datatype", "unit", "language")},
                   "qualifiers": normalized_qualifiers(triple.get("qualifiers"))}
        if candidate_or_triple["fact_identity_version"] == "fact_identity_v2":
            from schema.fact_identity import literal_identity
            payload["relation_semantic_hash"] = triple.get("relation_semantic_hash")
            if triple.get("tail_kind") == "literal":
                payload.update(literal_identity(triple))
        return sha256_json(payload)
    namespace = str(
        namespace or candidate_or_triple.get("fact_namespace")
        or candidate_or_triple.get("domain") or "default"
    )
    payload = {"namespace": namespace, "triple": canonical_triple(candidate_or_triple)}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def candidate_id_for(run_id: str, document_id: str, source_chunk_id: str, ordinal: int) -> str:
    raw = f"{run_id}\n{document_id}\n{source_chunk_id}\n{ordinal}"
    return "cand_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    document_id: str
    document_version: str
    chunk_id: str
    section_id: Optional[str]
    sentence_id: Optional[str]
    quote: str
    start: Optional[int]
    end: Optional[int]
    offset_basis: str = "chunk"
    chunk_start: Optional[int] = None
    chunk_end: Optional[int] = None
    breadcrumb_path: str = ""
    match_status: str = "exact"
    retrieval_level: str = "local_sentence"
    retrieval_score: float = 0.0
    semantic_status: str = "unknown"
    source_role: str = "source_document"
    retrieved_via_levels: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidatorVote:
    audit_id: str
    round_id: int
    proposal_digest: str
    voter_id: str
    model_id: str
    model_family: str
    response_status: str
    verdict: Optional[str] = None
    source_support: Optional[str] = None
    schema_status: Optional[str] = None
    schema_issue: Optional[str] = None
    schema_change_hint: Optional[Dict[str, Any]] = None
    evidence_assessments: List[Dict[str, Any]] = field(default_factory=list)
    reason_codes: List[str] = field(default_factory=list)
    checks: List[Dict[str, Any]] = field(default_factory=list)
    supporting_evidence_ids: List[str] = field(default_factory=list)
    counter_evidence_ids: List[str] = field(default_factory=list)
    requested_context: Optional[Dict[str, Any]] = None
    suggested_revision: Optional[Dict[str, Any]] = None
    latency_ms: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CalibrationResult:
    probability: Optional[float]
    status: str
    model_version: Optional[str]
    feature_schema_version: str
    compatibility: bool
    reason_codes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def proposal_digest(payload: Mapping[str, Any]) -> str:
    fields = {
        key: payload.get(key)
        for key in (
            "candidate_id", "revision", "canonical_triple", "document_version",
            "ontology_version", "evidence_bundle_hash", "prompt_version",
            "rubric_version", "committee_version", "memory_snapshot_id",
            "memory_bundle_hash", "memory_scope_hash", "memory_policy_hash", "round_id", "response_contract_version", "evidence_bundle_version",
            "contract_version", "source_index_hash", "ontology_release_id", "profile_hash",
            "committee_members", "agent_versions", "prompt_hash", "response_contract_hash",
            "candidate_review_hash", "ontology_check_hash",
            "admission_policy_hash",
            "committee_policy_hash",
            "committee_policy",
        )
    }
    return sha256_json(fields)
