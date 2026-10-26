"""One response contract shared by prompts, validation and audit metadata."""

from typing import Any, Mapping
from pathlib import Path
import hashlib

PROMPT_VERSION = "triple_validator_v3"
CONTRACT_VERSION = "validator_response_v3"
SCHEMA_ISSUES = {"none", "not_covered", "candidate_mapping_error", "suspected_schema_error", "incompatible", "unknown"}
CHECK_NAMES = ("triple_support", "qualifiers", "relation_direction", "ontology", "counterevidence")
VALID_VERDICTS = {"valid", "invalid", "uncertain"}
VALID_SUPPORT = {"supported", "contradicted", "insufficient"}
VALID_SCHEMA = {"pass", "fail", "unknown", "not_applicable"}
CONTEXT_SCOPES = ("current_chunk", "same_section", "parent_section", "other_section")
MAX_CONTEXT_LIMIT = 6
REASON_CODES = (
    "SOURCE_CONTRADICTS", "NEGATION_IGNORED", "DIRECTION_MISMATCH", "CONDITION_MISSING",
    "QUALIFIER_MISMATCH", "EVIDENCE_INSUFFICIENT", "SCHEMA_GAP", "CONSTRAINT_VIOLATION",
    "RELATION_MISMATCH", "ENTITY_MISMATCH", "ONTOLOGY_DISAGREEMENT",
)
STRING_ARRAY = {"type": "array", "items": {"type": "string", "minLength": 1}}
RESPONSE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "source_support", "schema_status", "supporting_evidence_ids",
                 "counter_evidence_ids", "reason_codes", "checks", "requested_context", "suggested_revision", "schema_issue", "schema_change_hint"],
    "properties": {
        "verdict": {"enum": sorted(VALID_VERDICTS)},
        "source_support": {"enum": sorted(VALID_SUPPORT)},
        "schema_status": {"enum": sorted(VALID_SCHEMA)},
        "schema_issue": {"enum": sorted(SCHEMA_ISSUES)},
        "schema_change_hint": {"type": ["object", "null"]},
        "evidence_assessments": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["evidence_id", "role", "note"], "properties": {"evidence_id": {"type": "string"},
            "role": {"enum": ["support", "counter", "context", "irrelevant"]}, "note": {"type": "string"}}}},
        "supporting_evidence_ids": STRING_ARRAY, "counter_evidence_ids": STRING_ARRAY,
        "reason_codes": STRING_ARRAY,
        "checks": {"type": "array", "minItems": 5, "maxItems": 5, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["check", "status", "evidence_ids", "note"],
            "properties": {"check": {"enum": list(CHECK_NAMES)}, "status": {"enum": sorted(VALID_SCHEMA)},
                           "evidence_ids": STRING_ARRAY, "note": {"type": "string"}},
        }},
        "requested_context": {"anyOf": [{"type": "null"}, {
            "type": "object", "additionalProperties": False, "required": ["scope", "query", "limit"],
            "properties": {"scope": {"enum": list(CONTEXT_SCOPES)}, "query": {"type": "string"},
                           "limit": {"type": "integer", "minimum": 1, "maximum": MAX_CONTEXT_LIMIT}},
        }]},
        "suggested_revision": {"type": ["object", "null"]},
    },
}

SYSTEM_PROMPT = (Path(__file__).resolve().parents[1] / "prompts" / "triple_validator_v3.txt").read_text(encoding="utf-8")
PROMPT_HASH = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def validate_response(value: Mapping[str, Any]) -> list[str]:
    """Validate structure before touching nested values, then cross-field semantics."""
    errors = []
    for field in RESPONSE_SCHEMA["required"]:
        if field not in value:
            errors.append(f"{field}: required")
    for field in set(value) - set(RESPONSE_SCHEMA["properties"]):
        errors.append(f"{field}: unknown/server-owned field")
    for field, allowed in (("verdict", VALID_VERDICTS), ("source_support", VALID_SUPPORT),
                           ("schema_status", VALID_SCHEMA), ("schema_issue", SCHEMA_ISSUES)):
        if not isinstance(value.get(field), str) or value[field] not in allowed:
            errors.append(f"{field}: expected one of {sorted(allowed)}")
    for field in ("supporting_evidence_ids", "counter_evidence_ids", "reason_codes"):
        if not _strings(value.get(field)):
            errors.append(f"{field}: expected array of nonempty strings")
    checks = value.get("checks")
    if not isinstance(checks, list):
        errors.append("checks: expected array")
    else:
        names = []
        for index, check in enumerate(checks):
            if not isinstance(check, dict):
                errors.append(f"checks[{index}]: expected object")
                continue
            names.append(check.get("check"))
            if set(check) != {"check", "status", "evidence_ids", "note"}:
                errors.append(f"checks[{index}]: requires check/status/evidence_ids/note only")
            if not isinstance(check.get("status"), str) or check["status"] not in VALID_SCHEMA:
                errors.append(f"checks[{index}].status: invalid")
            if not _strings(check.get("evidence_ids")) or not isinstance(check.get("note"), str):
                errors.append(f"checks[{index}]: invalid evidence_ids/note types")
        if len(names) != 5 or any(names.count(name) != 1 for name in CHECK_NAMES):
            errors.append("checks: each of the five check names must appear exactly once")
    request = value.get("requested_context")
    if request is not None:
        if not isinstance(request, dict):
            errors.append("requested_context: expected object or null")
        elif (set(request) != {"scope", "query", "limit"}
              or request.get("scope") not in CONTEXT_SCOPES
              or not isinstance(request.get("query"), str)
              or type(request.get("limit")) is not int
              or not 1 <= request["limit"] <= MAX_CONTEXT_LIMIT):
            errors.append("requested_context: invalid scope/query/limit (allowed limit 1..6)")
    if value.get("suggested_revision") is not None and not isinstance(value["suggested_revision"], dict):
        errors.append("suggested_revision: expected object or null")
    if value.get("schema_change_hint") is not None and not isinstance(value["schema_change_hint"], dict):
        errors.append("schema_change_hint: expected object or null")
    assessments = value.get("evidence_assessments", [])
    if not isinstance(assessments, list) or any(not isinstance(a, dict) or set(a) != {"evidence_id", "role", "note"}
            or not isinstance(a.get("evidence_id"), str) or not a["evidence_id"]
            or a.get("role") not in {"support", "counter", "context", "irrelevant"}
            or not isinstance(a.get("note"), str) or not a["note"].strip() for a in assessments):
        errors.append("evidence_assessments: invalid evidence role assessment")
    if errors:
        return errors
    support, schema, verdict = value["source_support"], value["schema_status"], value["verdict"]
    if support == "supported" and not value["supporting_evidence_ids"]:
        errors.append("supported requires supporting_evidence_ids")
    if support == "contradicted" and (not value["counter_evidence_ids"] or verdict != "invalid"):
        errors.append("contradicted requires counter_evidence_ids and verdict=invalid")
    if support == "insufficient" and verdict != "uncertain":
        errors.append("insufficient evidence requires verdict=uncertain")
    if verdict == "valid" and (support != "supported" or schema not in {"pass", "not_applicable"}
                               or value["counter_evidence_ids"]
                               or any(c["status"] not in {"pass", "not_applicable"} for c in checks)):
        errors.append("valid requires support, confirmed schema, all checks resolved and no counterevidence")
    if verdict == "invalid" and support != "contradicted":
        errors.append("invalid requires source contradiction, not a schema-only failure")
    if support == "contradicted" and not value["reason_codes"]:
        errors.append("source contradiction requires a factual reason_code")
    if set(value["supporting_evidence_ids"]) & set(value["counter_evidence_ids"]):
        errors.append("the same evidence ID cannot be labelled both support and counterevidence")
    ontology_check = next(c for c in checks if c["check"] == "ontology")
    if ontology_check["status"] != schema:
        errors.append("ontology check status must agree with schema_status")
    if schema == "fail" and "CONSTRAINT_VIOLATION" not in value["reason_codes"]:
        errors.append("schema failure requires CONSTRAINT_VIOLATION")
    if support == "supported" and schema in {"unknown", "fail"} and verdict != "uncertain":
        errors.append("unresolved schema with source support requires uncertain")
    if schema in {"pass", "not_applicable"} and value["schema_issue"] != "none":
        errors.append("passing schema requires schema_issue=none")
    if schema in {"fail", "unknown"} and value["schema_issue"] == "none":
        errors.append("unresolved schema requires an explicit schema_issue")
    if schema == "fail" and value["schema_issue"] == "not_covered":
        errors.append("constraint failure is not a missing-schema diagnosis")
    return errors


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)
