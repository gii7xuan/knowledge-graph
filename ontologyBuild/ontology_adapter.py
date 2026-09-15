"""Unified adapter for project ontology structures.

The project ontology is stored as a rich JSON object where classes are usually
keyed by class id and object/data properties can appear both at top level and
inside class definitions. Older modules expected a flatter list-based shape.
This module is the single compatibility layer between those worlds.
"""

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def normalize_name(value: Optional[Any]) -> str:
    if value is None:
        return ""
    return "".join(str(value).split()).casefold()


def _list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        return value.values()
    return _list(value)


def _add_unique(target: List[str], seen: Set[str], value: Any) -> None:
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    key = normalize_name(text)
    if key not in seen:
        seen.add(key)
        target.append(text)


def _named_values(element_id: Any, element: Any) -> List[str]:
    result: List[str] = []
    seen: Set[str] = set()
    _add_unique(result, seen, element_id)
    if isinstance(element, dict):
        for field in ("id", "label", "name", "field"):
            _add_unique(result, seen, element.get(field))
    elif isinstance(element, str):
        _add_unique(result, seen, element)
    return result


def iter_classes(ontology: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    classes = ontology.get("classes", {})
    if isinstance(classes, dict):
        for class_id, class_data in classes.items():
            if isinstance(class_data, dict):
                item = deepcopy(class_data)
                item.setdefault("id", class_id)
                item.setdefault("label", class_id)
                yield item
            else:
                yield {"id": str(class_id), "label": str(class_data or class_id)}
    else:
        for class_data in _list(classes):
            if isinstance(class_data, dict):
                item = deepcopy(class_data)
                item.setdefault("id", item.get("label") or item.get("name"))
                item.setdefault("label", item.get("id"))
                yield item
            elif class_data:
                yield {"id": str(class_data), "label": str(class_data)}


def iter_object_properties(ontology: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}

    def add(prop: Any, owner_class_id: Optional[str] = None) -> None:
        if isinstance(prop, str):
            prop = {"id": prop, "label": prop}
        if not isinstance(prop, dict):
            return
        item = deepcopy(prop)
        label = item.get("label") or item.get("name") or item.get("field") or item.get("id")
        prop_id = item.get("id") or label
        if not label and not prop_id:
            return
        key = normalize_name(prop_id or label)
        current = merged.setdefault(
            key,
            {
                "id": prop_id,
                "label": label or prop_id,
                "description": item.get("description") or item.get("definition") or "",
                "domain": [],
                "range": [],
            },
        )
        for field in ("id", "label", "name", "field"):
            if item.get(field) and not current.get(field):
                current[field] = item.get(field)
        if item.get("description") and not current.get("description"):
            current["description"] = item.get("description")
        if item.get("definition") and not current.get("definition"):
            current["definition"] = item.get("definition")
        domains = _list(item.get("domain"))
        if owner_class_id and not domains:
            domains = [owner_class_id]
        for value in domains:
            _add_unique(current["domain"], set(map(normalize_name, current["domain"])), value)
        for value in _list(item.get("range")):
            _add_unique(current["range"], set(map(normalize_name, current["range"])), value)

    for prop in _values(ontology.get("object_properties", {})):
        add(prop)
    for class_item in iter_classes(ontology):
        for prop in _list(class_item.get("object_properties")):
            add(prop, class_item.get("id"))

    return merged.values()


def iter_datatype_properties(ontology: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}

    def add(prop: Any, owner_class_id: Optional[str] = None) -> None:
        if isinstance(prop, str):
            prop = {"id": prop, "label": prop, "name": prop}
        if not isinstance(prop, dict):
            return
        label = prop.get("label") or prop.get("name") or prop.get("field") or prop.get("id")
        prop_id = prop.get("id") or prop.get("field") or label
        if not label and not prop_id:
            return
        key = normalize_name(prop_id or label)
        current = merged.setdefault(
            key,
            {
                "id": prop_id,
                "label": label or prop_id,
                "name": prop.get("name") or label or prop_id,
                "field": prop.get("field") or prop_id,
                "datatype": prop.get("datatype"),
                "domain": [],
            },
        )
        for field in ("id", "label", "name", "field", "datatype", "unit"):
            if prop.get(field) and not current.get(field):
                current[field] = prop.get(field)
        domains = _list(prop.get("domain"))
        if owner_class_id and not domains:
            domains = [owner_class_id]
        for value in domains:
            _add_unique(current["domain"], set(map(normalize_name, current["domain"])), value)

    for field in ("data_properties", "datatype_properties"):
        for prop in _values(ontology.get(field, {})):
            add(prop)
    for class_item in iter_classes(ontology):
        for prop in _list(class_item.get("data_properties")):
            add(prop, class_item.get("id"))
        for prop in _list(class_item.get("datatype_properties")):
            add(prop, class_item.get("id"))

    return merged.values()


def class_reference_index(ontology: Dict[str, Any]) -> Dict[str, str]:
    index: Dict[str, str] = {}
    for class_item in iter_classes(ontology):
        class_id = class_item.get("id")
        if not class_id:
            continue
        for value in _named_values(class_id, class_item):
            index[normalize_name(value)] = str(class_id)
    return index


def resolve_class_ref(value: Any, ontology: Dict[str, Any]) -> Optional[str]:
    if value is None:
        return None
    return class_reference_index(ontology).get(normalize_name(value))


def build_class_hierarchy(ontology: Dict[str, Any]) -> Dict[str, str]:
    hierarchy: Dict[str, str] = {}
    ref_index = class_reference_index(ontology)
    for class_item in iter_classes(ontology):
        class_id = class_item.get("id")
        if not class_id:
            continue
        class_id = str(class_id)
        for alias in _named_values(class_id, class_item):
            alias_text = str(alias).strip()
            if alias_text and alias_text != class_id:
                hierarchy.setdefault(alias_text, class_id)
            normalized_alias = normalize_name(alias_text)
            if normalized_alias and normalized_alias != normalize_name(class_id):
                hierarchy.setdefault(normalized_alias, class_id)
        parent = class_item.get("parent")
        resolved_parent = ref_index.get(normalize_name(parent)) if parent else None
        if resolved_parent:
            hierarchy[class_id] = resolved_parent
        for subclass in _list(class_item.get("subclasses")):
            resolved_child = ref_index.get(normalize_name(subclass)) or str(subclass)
            hierarchy.setdefault(resolved_child, class_id)
    return hierarchy


def build_element_indexes(ontology: Dict[str, Any]) -> Dict[str, Set[str]]:
    indexes = {
        "classes": set(),
        "object_properties": set(),
        "datatype_properties": set(),
    }
    for class_item in iter_classes(ontology):
        indexes["classes"].update(_named_values(class_item.get("id"), class_item))
    for prop in iter_object_properties(ontology):
        indexes["object_properties"].update(_named_values(prop.get("id"), prop))
    for prop in iter_datatype_properties(ontology):
        indexes["datatype_properties"].update(_named_values(prop.get("id"), prop))
    for values in indexes.values():
        values.update(normalize_name(value) for value in list(values))
    return indexes


def build_relation_constraints(ontology: Dict[str, Any]) -> Dict[str, Dict[str, List[str]]]:
    ref_index = class_reference_index(ontology)
    constraints: Dict[str, Dict[str, Set[str]]] = {}
    for prop in iter_object_properties(ontology):
        aliases = _named_values(prop.get("id"), prop)
        if not aliases:
            continue
        resolved_values = {"domain": set(), "range": set()}
        for field in ("domain", "range"):
            for value in _list(prop.get(field)):
                resolved = ref_index.get(normalize_name(value)) or str(value)
                if resolved:
                    resolved_values[field].add(resolved)
        for alias in aliases:
            item = constraints.setdefault(str(alias), {"domain": set(), "range": set()})
            item["domain"].update(resolved_values["domain"])
            item["range"].update(resolved_values["range"])
            normalized_alias = normalize_name(alias)
            if normalized_alias and normalized_alias != str(alias):
                item = constraints.setdefault(normalized_alias, {"domain": set(), "range": set()})
                item["domain"].update(resolved_values["domain"])
                item["range"].update(resolved_values["range"])
    return {
        name: {"domain": sorted(value["domain"]), "range": sorted(value["range"])}
        for name, value in constraints.items()
    }


def _constraint_rows(ontology: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    ref_index = class_reference_index(ontology)
    relation_constraints = build_relation_constraints(ontology)
    for property_name, data in relation_constraints.items():
        domains = data.get("domain") or [""]
        ranges = data.get("range") or [""]
        for domain in domains:
            for range_value in ranges:
                rows.append(
                    {
                        "property_name": property_name,
                        "property_type": "object_property",
                        "domain": domain,
                        "range": range_value,
                    }
                )
    for prop in iter_datatype_properties(ontology):
        datatype = prop.get("datatype")
        if datatype == "quantity":
            datatype = "float"
        if datatype in {"string_or_date", "array"}:
            datatype = "string"
        for domain in prop.get("domain") or [""]:
            resolved_domain = ref_index.get(normalize_name(domain)) or domain
            rows.append(
                {
                    "property_name": prop.get("label") or prop.get("name") or prop.get("field") or prop.get("id"),
                    "property_type": "datatype_property",
                    "domain": resolved_domain,
                    "range_datatype": datatype or "string",
                }
            )
    return rows


def to_legacy_ontology(ontology: Dict[str, Any]) -> Dict[str, Any]:
    """Return the list-based ontology shape expected by older modules."""
    classes: List[str] = []
    object_properties: List[str] = []
    datatype_properties: List[str] = []
    seen_classes: Set[str] = set()
    seen_object_properties: Set[str] = set()
    seen_datatype_properties: Set[str] = set()

    for class_item in iter_classes(ontology):
        for value in _named_values(class_item.get("id"), class_item):
            _add_unique(classes, seen_classes, value)
            _add_unique(classes, seen_classes, normalize_name(value))
    for prop in iter_object_properties(ontology):
        for value in _named_values(prop.get("id"), prop):
            _add_unique(object_properties, seen_object_properties, value)
            _add_unique(object_properties, seen_object_properties, normalize_name(value))
    for prop in iter_datatype_properties(ontology):
        for value in _named_values(prop.get("id"), prop):
            _add_unique(datatype_properties, seen_datatype_properties, value)
            _add_unique(datatype_properties, seen_datatype_properties, normalize_name(value))

    result = deepcopy(ontology)
    result["classes"] = classes
    result["object_properties"] = object_properties
    result["datatype_properties"] = datatype_properties
    result["data_properties"] = datatype_properties
    result["class_hierarchy"] = build_class_hierarchy(ontology)
    result["domain_range_constraints"] = _constraint_rows(ontology)
    return result


def normalize_triples_for_consistency(triples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in triples:
        if not isinstance(item, dict):
            continue
        normalized = deepcopy(item)
        if "subject" not in normalized and normalized.get("head") is not None:
            normalized["subject"] = normalized.get("head")
        if "predicate" not in normalized and normalized.get("relation") is not None:
            normalized["predicate"] = normalized.get("relation")
        if "normalized_predicate" not in normalized and normalized.get("relation") is not None:
            normalized["normalized_predicate"] = normalized.get("relation")
        if "object" not in normalized and normalized.get("tail") is not None:
            normalized["object"] = normalized.get("tail")
        if "subject_categories" not in normalized and normalized.get("head_type"):
            normalized["subject_categories"] = [normalized.get("head_type")]
        if "object_categories" not in normalized and normalized.get("tail_type"):
            normalized["object_categories"] = [normalized.get("tail_type")]
        normalized.setdefault("triple_type", "object_property")
        normalized.setdefault("chunk_id", normalized.get("source_id"))
        result.append(normalized)
    return result
