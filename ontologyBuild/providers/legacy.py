"""Import a reviewed project JSON without rerunning the legacy builder."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from ontologyBuild.ontology_adapter import iter_classes
from .base import OntologyArtifact, OntologyImportRequest


def definition_conflicts(schema):
    """Inspect raw definitions before the compatibility adapter can merge them."""
    found = {}
    errors = []

    def definitions(value):
        if isinstance(value, dict):
            return [dict(item, id=item.get("id") or key) if isinstance(item, dict)
                    else {"id": key, "label": item} for key, item in value.items()]
        return value or []

    def add(kind, value):
        for item in definitions(value):
            if not isinstance(item, dict):
                continue
            identity = item.get("id") or item.get("field") or item.get("name") or item.get("label")
            if not identity:
                continue
            previous = found.setdefault(str(identity), (kind, {}))
            if previous[0] != kind:
                errors.append({"code": "PROPERTY_KIND_CONFLICT", "id": identity})
            for key in ("domain", "range", "datatype", "definition", "domain_semantics", "range_semantics"):
                if key not in item:
                    continue
                value = item[key]
                if key in {"domain", "range"}:
                    value = sorted(value if isinstance(value, list) else [value])
                if key in previous[1] and previous[1][key] != value:
                    errors.append({"code": "PROPERTY_DEFINITION_CONFLICT", "id": identity, "field": key,
                                   "previous": previous[1][key], "observed": value})
                previous[1][key] = value

    for owner in [schema, *iter_classes(schema)]:
        add("object", owner.get("object_properties"))
        for key in ("data_properties", "datatype_properties"):
            add("data", owner.get(key))
    return errors


def unexecuted_constraints(schema):
    semantic_fields = {"enum", "required", "minimum", "maximum", "min", "max", "pattern",
                       "restrictions", "disjoint_with", "equivalent_to", "inverse_of", "transitive",
                       "symmetric", "functional", "cardinality", "min_cardinality", "max_cardinality"}
    issues = []
    for owner in [schema, *iter_classes(schema)]:
        elements = [owner]
        for key in ("object_properties", "data_properties", "datatype_properties"):
            value = owner.get(key) or []
            elements.extend(value.values() if isinstance(value, dict) else value)
        for item in elements:
            if not isinstance(item, dict):
                continue
            for field in sorted(semantic_fields & item.keys()):
                if item[field] is not None and item[field] is not False:
                    issues.append({"subject": item.get("id") or item.get("field") or item.get("name"),
                                   "field": field, "value": item[field], "status": "preserved_not_executed",
                                   "blocks_auto_review": True})
    return issues


class LegacyOntologyProvider:
    def build_or_import(self, request: OntologyImportRequest) -> OntologyArtifact:
        raw = request.source.read_bytes()
        schema = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(schema, dict):
            raise ValueError("project ontology must be a JSON object")
        source = deepcopy(schema)
        unsupported = unexecuted_constraints(source)
        profile = schema.get("application_profile") or {
            "profile_version": "project_json_v1", "constraint_semantics": "allowed_any",
            "interpretation": "project application constraints; not OWL entailment",
            "auto_review_blocked": False, "blocked_relation_ids": [],
            "reasoner": "not_run",
        }
        profile = deepcopy(profile)
        if unsupported:
            profile["auto_review_blocked"] = True
            profile["unsupported_constraint_count"] = len(unsupported)
        excluded_examples = 0
        for owner in [schema, *([item for item in schema.get("classes", {}).values() if isinstance(item, dict)]
                                 if isinstance(schema.get("classes"), dict) else schema.get("classes") or [])]:
            if isinstance(owner, dict):
                for field in ("instances", "examples"):
                    if field in owner:
                        excluded_examples += len(owner.pop(field) or [])
        schema["application_profile"] = deepcopy(profile)
        errors = definition_conflicts(source)
        return OntologyArtifact(
            project_schema=schema, source_json=source, source_json_text=raw.decode("utf-8"), application_profile=profile,
            method_manifest={"method": "legacy_project_json", "execution_status": "import_only",
                             "upstream": request.method_manifest,
                             "source_sha256": hashlib.sha256(raw).hexdigest()},
            validation_report={"external_validation": request.validation_report or {"status": "not_run"}},
            mapping_report={"format": "project_json", "errors": errors, "unsupported_axioms": unsupported,
                            "excluded_example_count": excluded_examples, "example_instances_imported_as_facts": False},
            requirements=request.requirements, competency_questions=request.competency_questions,
        )
