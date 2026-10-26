"""Retrieve compact sub-ontology snippets for a document chunk."""

import json
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List

try:
    from ontologyBuild.ontology_adapter import (
        build_real_class_hierarchy,
        iter_classes,
        iter_datatype_properties,
        iter_object_properties as adapter_iter_object_properties,
    )
except ImportError:
    from ontology_adapter import (
        build_real_class_hierarchy,
        iter_classes,
        iter_datatype_properties,
        iter_object_properties as adapter_iter_object_properties,
    )


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values())
    return str(value)


def _score(query: str, candidate: str) -> float:
    query = query.lower()
    candidate = candidate.lower()
    if not query or not candidate:
        return 0.0
    overlap = sum(1 for token in set(query) if token.strip() and token in candidate)
    overlap_score = overlap / max(len(set(query)), 1)
    ratio = SequenceMatcher(None, query[:800], candidate[:800]).ratio()
    return 0.65 * overlap_score + 0.35 * ratio


def _iter_object_properties(ontology: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    yield from adapter_iter_object_properties(ontology)


class SubOntologyRetriever:
    def __init__(self, ontology: Dict[str, Any]):
        self.ontology = ontology or {}

    def retrieve(
        self,
        breadcrumb_path: str,
        text: str,
        top_k_classes: int = 8,
        top_k_relations: int = 12,
    ) -> Dict[str, Any]:
        query = f"{breadcrumb_path}\n{text}"
        classes = self._rank_classes(query, top_k_classes)
        relations = self._rank_relations(query, top_k_relations)
        class_ids = {item["id"] for item in classes if item.get("id")}

        for rel in relations:
            class_ids.update(item for item in rel.get("domain", []) or [] if isinstance(item, str))
            class_ids.update(item for item in rel.get("range", []) or [] if isinstance(item, str))

        classes = self._classes_for_ids(class_ids, classes)
        class_ids = {item["id"] for item in classes if item.get("id")}

        parent_sets = {child: {parent} for child, parent in build_real_class_hierarchy(self.ontology).items()}
        for item in iter_classes(self.ontology):
            parents = item.get("parents") or ([item["parent"]] if item.get("parent") else [])
            parent_sets.setdefault(item["id"], set()).update(parents)
        class_hierarchy = [{"child": child, "parent": parent}
                           for child, parents in sorted(parent_sets.items()) for parent in sorted(parents)
                           if child in class_ids or parent in class_ids]

        return {
            "class_list": classes,
            "object_property_list": relations,
            "datatype_property_list": self._datatype_properties_for(class_ids),
            "domain_range_constraints": [
                {
                    "property": item.get("label") or item.get("id"),
                    "domain": item.get("domain", []),
                    "range": item.get("range", []),
                }
                for item in relations
            ],
            "class_hierarchy": class_hierarchy,
            "synonym_mapping": self.ontology.get("synonym_mapping", {}),
            "application_profile": self.ontology.get("application_profile", {}),
        }

    def to_prompt_text(self, sub_schema: Dict[str, Any]) -> str:
        return json.dumps(sub_schema, ensure_ascii=False, indent=2)

    def _rank_classes(self, query: str, limit: int) -> List[Dict[str, Any]]:
        items = []
        for class_data in iter_classes(self.ontology):
            class_id = class_data.get("id")
            if not class_id:
                continue
            label = class_data.get("label") or class_id
            description = class_data.get("definition") or class_data.get("description", "")
            candidate_text = (
                f"{class_id} {label} {description} "
                f"{class_data.get('parent') or ''} {_text(class_data.get('subclasses'))}"
            )
            items.append(
                (
                    _score(query, candidate_text),
                    {
                        "id": class_id,
                        "label": label,
                        "description": description,
                        "parent": class_data.get("parent"),
                        "parents": class_data.get("parents") or ([class_data["parent"]] if class_data.get("parent") else []),
                    },
                )
            )
        return [item for _, item in sorted(items, key=lambda pair: pair[0], reverse=True)[:limit]]

    def _classes_for_ids(self, class_ids: set, ranked_classes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result = {item.get("id"): item for item in ranked_classes if item.get("id")}
        hierarchy = {child: {parent} for child, parent in build_real_class_hierarchy(self.ontology).items()}
        for item in iter_classes(self.ontology):
            hierarchy.setdefault(item["id"], set()).update(
                item.get("parents") or ([item["parent"]] if item.get("parent") else []))
        wanted = set(class_ids)
        stack = list(class_ids)
        while stack:
            for parent in hierarchy.get(stack.pop(), []):
                if parent not in wanted:
                    wanted.add(parent)
                    stack.append(parent)
        for class_data in iter_classes(self.ontology):
            class_id = class_data.get("id")
            if not class_id or class_id not in wanted or class_id in result:
                continue
            result[class_id] = {
                "id": class_id,
                "label": class_data.get("label") or class_id,
                "description": class_data.get("definition") or class_data.get("description", ""),
                "parent": class_data.get("parent"),
                "parents": class_data.get("parents") or ([class_data["parent"]] if class_data.get("parent") else []),
            }
        return list(result.values())

    def _rank_relations(self, query: str, limit: int) -> List[Dict[str, Any]]:
        items = []
        for prop in _iter_object_properties(self.ontology):
            label = prop.get("label") or prop.get("id") or ""
            description = prop.get("description") or prop.get("definition") or ""
            candidate_text = (
                f"{label} 定义:{description} "
                f"主体类型:{_text(prop.get('domain'))} 客体类型:{_text(prop.get('range'))}"
            )
            items.append(
                (
                    _score(query, candidate_text),
                    {
                        "id": prop.get("id"),
                        "label": label,
                        "description": description,
                        "domain": prop.get("domain", []),
                        "range": prop.get("range", []),
                        "domain_semantics": prop.get("domain_semantics", "any"),
                        "range_semantics": prop.get("range_semantics", "any"),
                    },
                )
            )
        return [item for _, item in sorted(items, key=lambda pair: pair[0], reverse=True)[:limit]]

    def _datatype_properties_for(self, class_ids: set) -> List[Dict[str, Any]]:
        result = []
        for prop in iter_datatype_properties(self.ontology):
            domains = prop.get("domain", [])
            if isinstance(domains, str):
                domains = [domains]
            if class_ids and not any(domain in class_ids for domain in domains):
                continue
            item = dict(prop)
            item["domain"] = domains
            result.append(item)
        return result[:20]
