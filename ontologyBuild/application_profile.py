"""Build a conservative executable subset without inventing missing schema."""

from copy import deepcopy

from ontologyBuild.ontology_adapter import iter_classes, iter_object_properties, iter_datatype_properties
from review_agent.ontology_guard import validate_ontology
from .providers.legacy import definition_conflicts


def quarantine_legacy_artifact(artifact):
    """Keep all rejected definitions in source/report, not in the active whitelist.

    This is an explicit profile operation. It never chooses a winner among
    conflicting definitions or creates placeholder classes to make validation pass.
    """
    source = artifact.source_json
    if source is None:
        raise ValueError("quarantine profile requires a legacy JSON source")
    original = validate_ontology(source)
    conflicts = definition_conflicts(source)
    blocked = {row.get("id") for row in conflicts}
    blocked.update(row.get("relation_id") for row in original["errors"] if row.get("relation_id"))
    structural = [row for row in original["errors"] if not row.get("relation_id")]
    if structural:
        raise ValueError("class structure must be reviewed before a quarantine profile can be created")
    result = deepcopy(source)
    differences = []
    class_ids = {item["id"] for item in iter_classes(source)}
    result["classes"] = {}
    for original_class in iter_classes(source):
        item = deepcopy(original_class)
        for field in ("object_properties", "data_properties", "datatype_properties", "instances", "examples"):
            if item.pop(field, None):
                differences.append({"kind": "class_local_view_removed", "class_id": item["id"], "field": field,
                                    "reason": "flatten nonconflicting definitions into the canonical property registry"})
        if "subclasses" in item:
            missing = [child for child in item["subclasses"] if child not in class_ids]
            item["subclasses"] = [child for child in item["subclasses"] if child in class_ids]
            if missing:
                differences.append({"kind": "undeclared_subclasses_quarantined", "class_id": item["id"],
                                    "references": missing})
        result["classes"][item["id"]] = item
    result.pop("datatype_properties", None)
    for field, iterator in (("object_properties", iter_object_properties(source)),
                            ("data_properties", iter_datatype_properties(source))):
        result[field] = {}
        for prop in iterator:
            if prop["id"] in blocked:
                continue
            result[field][prop["id"]] = deepcopy(prop)
    for field in ("instances", "examples"):
        if field in result:
            result.pop(field)
            differences.append({"kind": "source_examples_excluded", "field": field})
    differences.append({"kind": "ambiguous_or_dangling_properties_quarantined", "ids": sorted(blocked)})
    profile = {
        "profile_version": "legacy_quarantine_v1", "constraint_semantics": "allowed_any",
        "interpretation": "legacy application subset; unresolved definitions remain in the immutable source",
        "auto_review_blocked": False, "blocked_relation_ids": sorted(blocked),
        "requires_difference_confirmation": True, "reasoner": "not_run",
        "publication_mode": "shadow", "source_semantics_verified": False,
        "differences": differences,
    }
    # A structurally valid subset is useful for investigation, but does not certify
    # the old ontology's domain semantics or authorize automatic admission.
    profile["auto_review_blocked"] = True
    result["application_profile"] = profile
    artifact.project_schema = result
    artifact.application_profile = profile
    artifact.mapping_report = {
        "format": "project_json", "errors": [],
        "unsupported_axioms": artifact.mapping_report.get("unsupported_axioms", []),
        "source_validation": original, "source_definition_conflicts": conflicts,
        "quarantined_property_ids": sorted(blocked), "profile_differences": differences,
        "source_preserved": True, "semantic_fidelity": "restricted_legacy_application_profile",
    }
    return artifact
