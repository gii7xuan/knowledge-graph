"""Normalize documented aliases once; conflicting configurations fail explicitly."""
from copy import deepcopy


def normalize_config(source):
    config = deepcopy(source)
    routing = config.setdefault("routing", {})
    if "mode" in routing:
        if "mode" in config and config["mode"] != routing["mode"]: raise ValueError("conflicting top-level and routing mode")
        config["mode"] = routing.pop("mode")
    agents = config.pop("agents", {})
    for alias, target in (("triple_review", "planner"), ("ontology_extension", "ontology_extension")):
        incoming = agents.get(alias) or {}
        current = config.setdefault(target, {})
        for key, value in incoming.items():
            if key in current and current[key] != value: raise ValueError("conflicting agent configuration: " + alias + "." + key)
            current[key] = value
    committees = config.pop("committees", {})
    for alias, target in (("triple", config.setdefault("consensus", {})), ("ontology", config.setdefault("ontology_extension", {}).setdefault("consensus", {}))):
        incoming = committees.get(alias) or {}
        required = {incoming[k] for k in ("required_votes", "source_support_required_votes", "admission_required_votes") if k in incoming}
        if len(required) > 1: raise ValueError("different source/admission thresholds require an explicit supported experiment configuration")
        updates = {key: value for key, value in incoming.items() if key in {"required_votes", "minimum_distinct_families", "version"}}
        if "size" in incoming: updates["committee_size"] = incoming["size"]
        if required: updates["required_votes"] = next(iter(required))
        for key, value in updates.items():
            if key in target and target[key] != value: raise ValueError("conflicting committee configuration: " + alias + "." + key)
            target[key] = value
    if not config["ontology_extension"].get("consensus"): config["ontology_extension"].pop("consensus", None)
    if "enabled" in (config.get("ontology_evolution") or {}):
        enabled = config["ontology_evolution"]["enabled"]
        if "enabled" in config["ontology_extension"] and config["ontology_extension"]["enabled"] != enabled: raise ValueError("conflicting ontology evolution and agent enablement")
        config["ontology_extension"]["enabled"] = enabled
    return config
