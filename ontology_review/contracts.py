"""Independent ontology action/response contracts and immutable vote bindings."""
from pathlib import Path
from review_agent.contracts import sha256_json
from review_agent.planner_contracts import ActionError

TASK_TYPE = "ontology_extension"
CONTRACT_VERSION = "ontology_review_response_v2"
PLANNER_VERSION = "ontology_extension_planner_v1"
PROMPT_VERSION = "ontology_extension_validator_v1"
ROOT = Path(__file__).resolve().parents[1]
PLANNER_PROMPT = (ROOT / "prompts/ontology_extension_planner_v1.txt").read_text(encoding="utf-8")
VALIDATOR_PROMPT = (ROOT / "prompts/ontology_extension_validator_v1.txt").read_text(encoding="utf-8")
CHECKS = ("necessity", "reuse", "definition", "hierarchy_direction", "constraint_strength", "structure", "impact")
EVIDENCE_SCOPES = {"definition", "classification", "scope_rule", "mixed", "instance_only", "unknown"}
KINDS = {"add_class", "add_object_property", "add_data_property", "add_alias", "revise_definition", "revise_constraint", "merge_terms"}
TOOLS = {
    "search_existing_schema": {"query": "string <=600"},
    "retrieve_definition_evidence": {"query": "string <=600", "scope": "current_chunk/same_section/parent_section/other_section", "limit": "integer 1..6"},
    "inspect_schema_neighborhood": {"schema_id": "existing formal ID"},
    "propose_schema_delta": {"operations": "1..16 complete operation objects: kind,target,changes,evidence_ids"},
    "validate_schema_structure": {}, "run_reasoner_checks": {}, "analyze_schema_impact": {}, "run_competency_checks": {},
    "run_ontology_validation": {}, "finish_extension_review": {"stop_reason": "string <=400"},
}
RESPONSE_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["decision", "necessity", "semantic_status", "structure_status", "impact_status", "supporting_evidence_ids", "counter_evidence_ids", "checks", "reason_codes", "suggested_changes", "requested_context"],
    "properties": {
        "decision": {"enum": ["approve", "revise", "reject", "uncertain"]},
        "necessity": {"enum": ["confirmed_gap", "reuse_existing", "not_needed", "unknown"]},
        **{k: {"enum": ["pass", "fail", "unknown", "not_applicable"]} for k in ("semantic_status", "structure_status", "impact_status")},
        **{k: {"type": "array", "items": {"type": "string"}} for k in ("supporting_evidence_ids", "counter_evidence_ids", "reason_codes")},
        "checks": {"type": "array", "minItems": len(CHECKS), "maxItems": len(CHECKS), "items": {
            "type": "object", "additionalProperties": False, "required": ["check", "status", "evidence_ids", "note"],
            "properties": {"check": {"enum": list(CHECKS)}, "status": {"enum": ["pass", "fail", "unknown", "not_applicable"]},
                           "evidence_ids": {"type": "array", "items": {"type": "string"}}, "note": {"type": "string"},
                           "evidence_scope": {"enum": sorted(EVIDENCE_SCOPES)}}}},
        "suggested_changes": {"type": ["object", "null"]}, "requested_context": {"type": ["object", "null"]}}}


def strings(value):
    return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)


def validate_response(value):
    errors = []
    if not isinstance(value, dict): return ["expected JSON object"]
    if set(value) != set(RESPONSE_SCHEMA["required"]): errors.append("missing or unknown/server-owned fields")
    for key in ("decision", "necessity", "semantic_status", "structure_status", "impact_status"):
        if value.get(key) not in RESPONSE_SCHEMA["properties"][key]["enum"]: errors.append(key + ": invalid")
    for key in ("supporting_evidence_ids", "counter_evidence_ids", "reason_codes"):
        if not strings(value.get(key)): errors.append(key + ": invalid string array")
    checks = value.get("checks")
    if not isinstance(checks, list) or len(checks) != len(CHECKS): errors.append("complete checks required")
    else:
        names = []
        for check in checks:
            if not isinstance(check, dict): errors.append("invalid check"); continue
            names.append(check.get("check"))
            if (not {"check", "status", "evidence_ids", "note"} <= set(check) or set(check)-{"check", "status", "evidence_ids", "note", "evidence_scope"}
                    or ('evidence_scope' in check and (not isinstance(check['evidence_scope'],str) or check['evidence_scope'] not in EVIDENCE_SCOPES))
                    or not isinstance(check.get("status"), str) or check.get("status") not in {"pass", "fail", "unknown", "not_applicable"}
                    or not strings(check.get("evidence_ids")) or not isinstance(check.get("note"), str)):
                errors.append("invalid check fields")
        if any(names.count(name) != 1 for name in CHECKS): errors.append("all check names required once")
    if value.get("suggested_changes") is not None and not isinstance(value["suggested_changes"], dict): errors.append("invalid suggested_changes")
    request = value.get("requested_context")
    if request is not None and (not isinstance(request, dict) or set(request) != {"query", "scope", "limit"}): errors.append("invalid requested_context")
    if errors: return errors
    if set(value["supporting_evidence_ids"]) & set(value["counter_evidence_ids"]): errors.append("support/counter overlap")
    if value["decision"] == "approve" and (value["necessity"] != "confirmed_gap" or value["semantic_status"] != "pass"
            or value["structure_status"] != "pass" or value["impact_status"] != "pass" or not value["supporting_evidence_ids"]
            or value["counter_evidence_ids"] or any(c["status"] not in {"pass", "not_applicable"} for c in checks)):
        errors.append("approve requires complete supported modeling checks, structure and impact")
    return errors


def validate_action(value):
    if not isinstance(value, dict) or set(value) != {"action", "arguments", "target_issue", "rationale_summary"}:
        raise ActionError("expected action/arguments/target_issue/rationale_summary")
    name, args = value["action"], value["arguments"]
    if not isinstance(name, str) or name not in TOOLS or not isinstance(args, dict) or set(args) != set(TOOLS[name]):
        raise ActionError("unknown ontology tool or incorrect arguments")
    for field in ("target_issue", "rationale_summary"):
        if not isinstance(value[field], str) or not value[field].strip() or len(value[field]) > 600: raise ActionError("invalid concise rationale")
    for key, item in args.items():
        if key == "operations":
            if not isinstance(item, list) or not 1 <= len(item) <= 16 or len(str(item)) > 16000: raise ActionError("bounded operations required")
        elif key == "limit":
            if type(item) is not int or not 1 <= item <= 6: raise ActionError("limit outside 1..6")
        elif key == "scope":
            if item not in {"current_chunk", "same_section", "parent_section", "other_section"}: raise ActionError("invalid source scope")
        elif not isinstance(item, str) or not item.strip() or len(item) > 600: raise ActionError("invalid ontology tool text")
    return value


def versions():
    return {"task_type": TASK_TYPE, "contract_version": CONTRACT_VERSION, "planner_version": PLANNER_VERSION,
            "prompt_version": PROMPT_VERSION, "planner_prompt_hash": sha256_json(PLANNER_PROMPT),
            "prompt_hash": sha256_json(VALIDATOR_PROMPT), "contract_hash": sha256_json(RESPONSE_SCHEMA)}


def proposal_digest(proposal):
    return sha256_json({key: value for key, value in proposal.items() if key not in {"proposal_digest", "audit_id", "round_id"}})


def vote_binding(voter, proposal):
    return {**{k: proposal[k] for k in ("task_type", "task_id", "object_revision", "audit_id", "round_id", "proposal_digest",
            "base_release_id", "base_ontology_version", "base_profile_hash", "delta_hash", "evidence_hash", "committee_hash")},
            "contract_version": CONTRACT_VERSION, "prompt_version": PROMPT_VERSION,
            "voter_id": voter["id"], "model_id": voter["model_id"], "model_family": voter["model_family"],
            "model_identity": str(voter.get("model_identity") or voter["model_id"]).strip().casefold()}
