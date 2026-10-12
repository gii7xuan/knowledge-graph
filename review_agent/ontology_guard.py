"""Frozen ontology loading and deterministic constraint checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

from ontologyBuild.ontology_adapter import (
    class_reference_index,
    iter_classes,
    iter_datatype_properties,
    iter_object_properties,
    normalize_name,
    resolve_class_ref,
)


@dataclass(frozen=True)
class OntologySnapshot:
    ontology: Dict[str, Any]
    ontology_version: str
    source_path: Optional[str] = None
    constraint_semantics: str = "allowed_any"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_snapshot(path_or_data: Any, expected_version: Optional[str] = None) -> OntologySnapshot:
    source_path = None
    if isinstance(path_or_data, (str, Path)):
        path = Path(path_or_data)
        source_path = str(path)
        ontology = json.loads(path.read_text(encoding="utf-8"))
    elif isinstance(path_or_data, Mapping):
        ontology = dict(path_or_data)
    else:
        raise TypeError("ontology snapshot must be a path or mapping")
    version = hashlib.sha256(_canonical_bytes(ontology)).hexdigest()
    if expected_version and version != expected_version:
        raise ValueError("ontology snapshot hash mismatch")
    return OntologySnapshot(ontology, version, source_path)


def validate_ontology(ontology: Mapping[str, Any]) -> Dict[str, Any]:
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    class_ids: Set[str] = set()
    aliases: Dict[str, str] = {}
    parent_edges: Dict[str, Set[str]] = {}
    for item in iter_classes(dict(ontology)):
        class_id = str(item.get("id") or "").strip()
        if not class_id:
            errors.append({"code": "CLASS_ID_MISSING"})
            continue
        if class_id in class_ids:
            errors.append({"code": "DUPLICATE_CLASS_ID", "id": class_id})
        class_ids.add(class_id)
        for value in [class_id, item.get("label"), item.get("name"), *(item.get("aliases") or [])]:
            if not value:
                continue
            key = normalize_name(value)
            if key in aliases and aliases[key] != class_id:
                errors.append({"code": "CLASS_ALIAS_CONFLICT", "alias": str(value), "ids": [aliases[key], class_id]})
            aliases[key] = class_id
        parents = item.get("parents") or ([item.get("parent")] if item.get("parent") else [])
        parent_edges[class_id] = {str(value) for value in parents if value}

    ref_index = class_reference_index(dict(ontology))
    relation_ids: Set[str] = set()
    for kind, iterator in (
        ("object_property", iter_object_properties(dict(ontology))),
        ("datatype_property", iter_datatype_properties(dict(ontology))),
    ):
        for relation in iterator:
            relation_id = str(relation.get("id") or "").strip()
            if not relation_id:
                errors.append({"code": "RELATION_ID_MISSING", "kind": kind})
                continue
            if relation_id in relation_ids:
                warnings.append({"code": "DUPLICATE_RELATION_MERGED", "id": relation_id})
            relation_ids.add(relation_id)
            for field in ("domain", "range"):
                for value in relation.get(field) or []:
                    if normalize_name(value) not in ref_index:
                        errors.append({
                            "code": "DANGLING_CLASS_REFERENCE", "relation_id": relation_id,
                            "field": field, "value": value,
                        })

    normalized_parents = {
        child: {ref_index.get(normalize_name(parent), parent) for parent in parents}
        for child, parents in parent_edges.items()
    }
    visiting: Set[str] = set()
    visited: Set[str] = set()
    def visit(node: str) -> None:
        if node in visiting:
            errors.append({"code": "CLASS_HIERARCHY_CYCLE", "class_id": node})
            return
        if node in visited:
            return
        visiting.add(node)
        for parent in normalized_parents.get(node, set()):
            if parent in class_ids:
                visit(parent)
        visiting.remove(node)
        visited.add(node)
    for class_id in class_ids:
        visit(class_id)
    return {"valid": not errors, "errors": errors, "warnings": warnings,
            "class_count": len(class_ids), "relation_count": len(relation_ids)}


class OntologyGuard:
    def __init__(self, snapshot: OntologySnapshot):
        self.snapshot = snapshot
        self.ontology = snapshot.ontology
        self.class_index = class_reference_index(self.ontology)
        self.classes = {str(item.get("id")): item for item in iter_classes(self.ontology)}
        self.relations: Dict[str, Dict[str, Any]] = {}
        self.alias_to_relation: Dict[str, str] = {}
        for kind, iterator in (
            ("object_property", iter_object_properties(self.ontology)),
            ("datatype_property", iter_datatype_properties(self.ontology)),
        ):
            for item in iterator:
                relation = dict(item)
                relation["kind"] = kind
                relation_id = str(relation.get("id") or "")
                self.relations[relation_id] = relation
                for value in [relation_id, relation.get("label"), relation.get("name"), *(relation.get("aliases") or [])]:
                    if value:
                        self.alias_to_relation[normalize_name(value)] = relation_id
        self.parents: Dict[str, Set[str]] = {}
        for item in self.classes.values():
            values = item.get("parents") or ([item.get("parent")] if item.get("parent") else [])
            self.parents[str(item["id"])] = {
                self.class_index.get(normalize_name(value), str(value)) for value in values if value
            }

    def resolve_relation(self, value: Any) -> Optional[str]:
        return self.alias_to_relation.get(normalize_name(value))

    def is_subclass_or_same(self, class_ref: Any, allowed_ref: Any) -> Optional[bool]:
        class_id = self.class_index.get(normalize_name(class_ref))
        allowed_id = self.class_index.get(normalize_name(allowed_ref))
        if not class_id or not allowed_id:
            return None
        stack = [class_id]
        seen: Set[str] = set()
        while stack:
            current = stack.pop()
            if current == allowed_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(self.parents.get(current, ()))
        return False

    def _endpoint_status(self, actual: Any, allowed: Iterable[Any]) -> str:
        allowed = list(allowed or [])
        if not allowed or not actual or str(actual) in {"Unknown", "unknown"}:
            return "unknown"
        checks = [self.is_subclass_or_same(actual, value) for value in allowed]
        if any(value is True for value in checks):
            return "pass"
        if any(value is None for value in checks):
            return "unknown"
        return "fail"

    def check(self, candidate: Mapping[str, Any]) -> Dict[str, Any]:
        triple = candidate.get("triple") if isinstance(candidate.get("triple"), Mapping) else candidate
        requested_relation_id = triple.get("relation_id")
        surface = triple.get("surface_relation") or triple.get("relation")
        relation_id = (
            str(requested_relation_id) if requested_relation_id in self.relations
            else self.resolve_relation(requested_relation_id or surface)
        )
        if not relation_id:
            return {
                "status": "unknown", "relation_known": False, "relation_id": None,
                "domain_status": "unknown", "range_status": "unknown", "type_unknown": True,
                "mapping_ambiguous": False, "hard_failure": False,
                "reason_codes": ["SCHEMA_GAP"], "constraint_semantics": self.snapshot.constraint_semantics,
            }
        relation = self.relations[relation_id]
        head_type = triple.get("head_type") or candidate.get("head_type")
        tail_type = triple.get("tail_type") or candidate.get("tail_type")
        domain_status = self._endpoint_status(head_type, relation.get("domain") or [])
        range_status = self._endpoint_status(tail_type, relation.get("range") or [])
        tail_kind = str(triple.get("tail_kind") or "entity")
        kind_failure = (
            relation.get("kind") == "datatype_property" and tail_kind != "literal"
        ) or (
            relation.get("kind") == "object_property" and tail_kind == "literal"
        )
        reason_codes: List[str] = []
        if domain_status == "fail":
            reason_codes.append("DOMAIN_CONSTRAINT_FAILED")
        if range_status == "fail":
            reason_codes.append("RANGE_CONSTRAINT_FAILED")
        if kind_failure:
            reason_codes.append("PROPERTY_KIND_MISMATCH")
        type_unknown = domain_status == "unknown" or range_status == "unknown"
        if type_unknown:
            reason_codes.append("ENTITY_TYPE_UNKNOWN")
        hard_failure = kind_failure or "fail" in {domain_status, range_status}
        status = "fail" if hard_failure else ("unknown" if type_unknown else "pass")
        return {
            "status": status, "relation_known": True, "relation_id": relation_id,
            "relation": relation, "domain_status": domain_status, "range_status": range_status,
            "type_unknown": type_unknown, "mapping_ambiguous": False,
            "hard_failure": hard_failure, "reason_codes": reason_codes,
            "constraint_semantics": self.snapshot.constraint_semantics,
            "ontology_version": self.snapshot.ontology_version,
        }
