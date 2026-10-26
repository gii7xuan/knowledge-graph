"""One response contract shared by prompts, validation and audit metadata."""

from typing import Any, Mapping

PROMPT_VERSION = "validator_v2_complete_qualifiers"
CONTRACT_VERSION = "validator_response_v2"
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
                 "counter_evidence_ids", "reason_codes", "checks", "requested_context", "suggested_revision"],
    "properties": {
        "verdict": {"enum": sorted(VALID_VERDICTS)},
        "source_support": {"enum": sorted(VALID_SUPPORT)},
        "schema_status": {"enum": sorted(VALID_SCHEMA)},
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

SYSTEM_PROMPT = """你是独立的三元组审核者。只输出一个合法 JSON object，不得输出 Markdown 或解释文字。
遵循 output_contract 的完整 JSON Schema，五项 checks 必须各出现一次。
顶层 evidence 是本轮唯一可引用证据目录；allowed_evidence_ids 是可选 ID。不得编造引用。
history_examples 仅用于理解标准，其中内容和 ID 不是本轮证据。
原文定位成功不表示事实受到支持。检查完整主谓宾、否定、条件、数量、时间、范围、能力/可能性、量词、指代及关系方向。
qualifier_status=unknown 表示尚未确认，not_stated 表示抽取者声称原文未陈述；两者都不是独立语义核验证明。
目录和标题只能定位主题，确认事实需要正文支持。证据不足必须 uncertain，不可因此判为事实错误。
supported 必须给出支持 ID，contradicted 必须给出反证 ID 并判 invalid。
valid 需要 supported、本体 pass 或 not_applicable、关键检查通过且无未解决反证。
原文支持但本体缺口时保留 source_support=supported，schema_status=unknown，verdict=uncertain。
明确违反本体时报告 CONSTRAINT_VIOLATION，区分本体错误与原文事实矛盾。
不能静默覆盖 ontology_check 已确定的约束失败。只给简短可核查结论，不输出隐藏思维链。
"""


def validate_response(value: Mapping[str, Any]) -> list[str]:
    """Validate structure before touching nested values, then cross-field semantics."""
    errors = []
    for field in RESPONSE_SCHEMA["required"]:
        if field not in value:
            errors.append(f"{field}: required")
    for field in set(value) - set(RESPONSE_SCHEMA["properties"]):
        errors.append(f"{field}: unknown/server-owned field")
    for field, allowed in (("verdict", VALID_VERDICTS), ("source_support", VALID_SUPPORT),
                           ("schema_status", VALID_SCHEMA)):
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
    if verdict == "invalid" and support != "contradicted" and schema != "fail":
        errors.append("invalid requires source contradiction or explicit schema failure")
    if support == "contradicted" and not value["reason_codes"]:
        errors.append("source contradiction requires a factual reason_code")
    if set(value["supporting_evidence_ids"]) & set(value["counter_evidence_ids"]):
        errors.append("the same evidence ID cannot be labelled both support and counterevidence")
    ontology_check = next(c for c in checks if c["check"] == "ontology")
    if ontology_check["status"] != schema:
        errors.append("ontology check status must agree with schema_status")
    if schema == "fail" and "CONSTRAINT_VIOLATION" not in value["reason_codes"]:
        errors.append("schema failure requires CONSTRAINT_VIOLATION")
    if support == "supported" and schema == "unknown" and verdict != "uncertain":
        errors.append("schema gap with source support requires uncertain")
    return errors


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)
