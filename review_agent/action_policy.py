"""Permissions and minimum obligations remain outside the planner's authority."""

from .contracts import sha256_json
from .planner_contracts import ActionError, TOOL_SCHEMAS, validate_action


class ActionPolicy:
    def __init__(self, config):
        self.config = config
        self.allowed = set(config.get("allowed_tools", TOOL_SCHEMAS))
        if self.allowed - set(TOOL_SCHEMAS):
            raise ValueError("planner whitelist contains unregistered tools")

    def definitions(self):
        return {k: v for k, v in TOOL_SCHEMAS.items() if k in self.allowed}

    def check(self, action, state):
        validate_action(action)
        if action["action"] not in self.allowed:
            raise ActionError("tool not permitted by execution policy")
        args = action["arguments"]
        available = {x["evidence_id"] for x in state.archive if x.get("anchor_valid") is True}
        cited = args.get("evidence_ids", []) + ([args["evidence_id"]] if "evidence_id" in args else [])
        if set(cited) - available:
            raise ActionError("evidence reference is outside current verified source")
        fingerprint = sha256_json({"tool": action["action"], "arguments": args,
            "document_version": state.candidate["document_version"],
            "query_state": state.validation_key() if action["action"] in {"search_evidence", "run_validation"} else None})
        repeats = state.fingerprints.get(fingerprint, 0)
        if repeats >= int(self.config.get("max_repeated_actions", 2)):
            raise ActionError("repeated action without information gain")
        state.fingerprints[fingerprint] = repeats + 1
        return fingerprint

    def final_gaps(self, state):
        gaps = dict(state.gaps)
        if not state.ontology_checked:
            gaps["ontology_not_checked"] = True
        validation = state.current_validation()
        gaps["validation_missing"] = validation is None
        gaps["version_stale"] = bool(state.validations and validation is None)
        unresolved = [i for i in state.issues if i.get("status", "open") != "resolved"]
        gaps["critical_issues_unresolved"] = any(i.get("critical") for i in unresolved)
        selected = {i["evidence_id"] for i in state.bundle.get("items", [])}
        gaps["protected_evidence_omitted"] = bool(set(state.protected_ids) - selected - set(state.evidence_exclusions))
        material = {x["evidence_id"] for x in state.archive if x.get("review_obligation")}
        material.update(state.protected_ids)
        gaps["unreviewed_discovered_evidence"] = bool(material - selected - set(state.evidence_exclusions))
        gaps["unresolved_counterevidence"] = any(i["code"] in {"source_conflict", "counterevidence"} for i in unresolved)
        gaps["unresolved_support_conflict"] = gaps["unresolved_counterevidence"]
        gaps["need_more_evidence"] = any(i["code"] in {"EVIDENCE_INSUFFICIENT", "source_conflict", "counterevidence"} for i in unresolved)
        return gaps
