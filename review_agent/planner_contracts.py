"""Planner actions are requests, never votes, observations or admission decisions."""

from .response_contract import CONTEXT_SCOPES
from pathlib import Path
import hashlib

PLANNER_CONTRACT_VERSION = "planner_action_v2"
PLANNER_PROMPT_VERSION = "triple_planner_v2"
AGENT_STRATEGY_VERSION = "bounded_react_v2"
PURPOSES = ("check_qualifiers", "find_counterevidence", "find_definition", "resolve_reference", "check_support")
TOOL_SCHEMAS = {
    "inspect_context": {"evidence_id": "current evidence ID", "before": "integer 0..5", "after": "integer 0..5"},
    "search_evidence": {"scope": list(CONTEXT_SCOPES), "query": "nonempty string <=600", "purpose": list(PURPOSES), "limit": "integer 1..6"},
    "inspect_ontology": {"relation_id": "string <=200 or null", "endpoint_types": "optional object with head/tail string or null", "issue": "string <=400",
                         "surface_relation": "optional string <=400", "query": "optional string <=600"},
    "lookup_review_memory": {"relation_id": "string <=200", "issue_type": "string <=200", "limit": "integer 1..3"},
    "run_validation": {},
    "propose_revision": {"changes": "nonempty object containing triple fields only", "evidence_ids": "current evidence IDs", "reason": "string <=400"},
    "finish_review": {"stop_reason": "nonempty string <=200", "unresolved_issues": "array of strings <=400, max 10", "evidence_ids": "current evidence IDs"},
    "request_ontology_extension": {"change_kind": "add_class/add_object_property/add_data_property/add_alias/revise_definition/revise_constraint/merge_terms",
                                   "description": "nonempty string <=800", "proposed_changes": "bounded draft object", "evidence_ids": "current evidence IDs"},
    "get_ontology_extension_status": {"extension_id": "subscribed task ID"},
}
SYSTEM_PROMPT = (Path(__file__).resolve().parents[1] / "prompts" / "triple_planner_v2.txt").read_text(encoding="utf-8")
PLANNER_PROMPT_HASH = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


class ActionError(ValueError):
    pass


def _text(value, maximum, *, nonempty=False):
    return isinstance(value, str) and len(value) <= maximum and (not nonempty or bool(value.strip()))


def validate_action(value):
    if not isinstance(value, dict) or set(value) != {"action", "arguments", "target_issue", "rationale_summary"}:
        raise ActionError("expected exactly action, arguments, target_issue, rationale_summary")
    name, args = value["action"], value["arguments"]
    if not isinstance(name, str) or name not in TOOL_SCHEMAS or not isinstance(args, dict):
        raise ActionError("unknown tool or invalid arguments object")
    if not _text(value["target_issue"], 400) or not _text(value["rationale_summary"], 600):
        raise ActionError("issue/rationale must be bounded strings")
    required = set(TOOL_SCHEMAS[name]) - ({"endpoint_types", "surface_relation", "query"} if name == "inspect_ontology" else set())
    if not required <= set(args) or set(args) - set(TOOL_SCHEMAS[name]):
        raise ActionError("missing or forbidden tool arguments")
    for key, item in args.items():
        if name == "inspect_ontology" and key == "relation_id" and item is None:
            continue
        if key == "change_kind":
            if item not in {"add_class", "add_object_property", "add_data_property", "add_alias", "revise_definition", "revise_constraint", "merge_terms"}:
                raise ActionError("invalid ontology change kind")
        elif key == "proposed_changes":
            import json
            if not isinstance(item, dict) or not item or len(json.dumps(item, ensure_ascii=False)) > 6000:
                raise ActionError("invalid bounded ontology draft")
        elif key in {"before", "after", "limit"}:
            low, high = (0, 5) if key != "limit" else (1, 3 if name == "lookup_review_memory" else 6)
            if type(item) is not int or not low <= item <= high:
                raise ActionError(f"{key} outside allowed integer range")
        elif key == "scope":
            if not isinstance(item, str) or item not in CONTEXT_SCOPES:
                raise ActionError("unsupported evidence scope")
        elif key == "purpose":
            if not isinstance(item, str) or item not in PURPOSES:
                raise ActionError("unsupported search purpose")
        elif key in {"evidence_ids", "unresolved_issues"}:
            maximum = 10 if key == "unresolved_issues" else 24
            if not isinstance(item, list) or len(item) > maximum or any(not _text(x, 400, nonempty=True) for x in item):
                raise ActionError("invalid bounded string array")
        elif key == "endpoint_types":
            if not isinstance(item, dict) or set(item) - {"head", "tail"} or any(v is not None and not _text(v, 200) for v in item.values()):
                raise ActionError("invalid endpoint types")
        elif key == "changes":
            from .repository import EDITABLE_TRIPLE_FIELDS
            if not isinstance(item, dict) or not item or set(item) - EDITABLE_TRIPLE_FIELDS:
                raise ActionError("revision changes must be triple fields only")
            import json
            if len(json.dumps(item, ensure_ascii=False)) > 3000:
                raise ActionError("revision draft too large")
            for field, content in item.items():
                if field in {"qualifiers", "qualifier_status"}:
                    if not isinstance(content, dict) or set(content) - {"polarity", "condition", "time_scope", "applicability", "modality", "quantifier"}:
                        raise ActionError("invalid qualifiers")
                    if any(v is not None and not _text(v, 600) for v in content.values()):
                        raise ActionError("qualifiers must be bounded strings")
                elif content is not None and not _text(content, 600):
                    raise ActionError("revision fields must be bounded strings")
        elif not _text(item, 800 if key == "description" else 600 if key == "query" else 400,
                       nonempty=key in {"query", "evidence_id", "stop_reason", "reason", "description", "extension_id"}):
            raise ActionError(f"invalid {key}")
    return value
