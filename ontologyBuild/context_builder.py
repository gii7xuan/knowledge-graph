"""Build bounded ontology context for LLM candidate extraction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

try:
    from ontologyBuild.ontology_adapter import (
        build_real_class_hierarchy,
        iter_classes,
        iter_datatype_properties,
        iter_object_properties,
    )
    from schema.subontology_retriever import SubOntologyRetriever
except ImportError:
    from ontology_adapter import build_real_class_hierarchy, iter_classes, iter_datatype_properties, iter_object_properties
    from schema.subontology_retriever import SubOntologyRetriever


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ONTOLOGY_PATH = ROOT / "ontology" / "ontology.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_ontology(path: str | Path = DEFAULT_ONTOLOGY_PATH) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def compact_class(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "label": item.get("label") or item.get("name") or item.get("id"),
        "definition": item.get("definition") or item.get("description") or "",
        "parent": item.get("parent"),
    }


def compact_object_property(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "label": item.get("label") or item.get("name") or item.get("id"),
        "definition": item.get("definition") or item.get("description") or "",
        "domain": item.get("domain") or [],
        "range": item.get("range") or [],
    }


def compact_datatype_property(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id") or item.get("field"),
        "label": item.get("label") or item.get("name") or item.get("field") or item.get("id"),
        "definition": item.get("definition") or item.get("description") or "",
        "domain": item.get("domain") or [],
        "datatype": item.get("datatype"),
        "unit": item.get("unit"),
    }


def domain_range_rows(ontology: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for prop in iter_object_properties(ontology):
        rows.append(
            {
                "property": prop.get("label") or prop.get("name") or prop.get("id"),
                "property_id": prop.get("id"),
                "property_type": "object_property",
                "domain": prop.get("domain") or [],
                "range": prop.get("range") or [],
            }
        )
    for prop in iter_datatype_properties(ontology):
        rows.append(
            {
                "property": prop.get("label") or prop.get("name") or prop.get("field") or prop.get("id"),
                "property_id": prop.get("id") or prop.get("field"),
                "property_type": "datatype_property",
                "domain": prop.get("domain") or [],
                "range_datatype": prop.get("datatype"),
            }
        )
    return rows


def build_full_context(
    ontology: dict[str, Any],
    *,
    ontology_path: str | Path = DEFAULT_ONTOLOGY_PATH,
    document_id: str = "",
) -> dict[str, Any]:
    path = Path(ontology_path)
    return {
        "ontology_version": f"sha256:{sha256_file(path)}" if path.exists() else "unknown",
        "document_id": document_id,
        "class_list": [compact_class(item) for item in iter_classes(ontology)],
        "object_property_list": [compact_object_property(item) for item in iter_object_properties(ontology)],
        "datatype_property_list": [compact_datatype_property(item) for item in iter_datatype_properties(ontology)],
        "domain_range_constraints": domain_range_rows(ontology),
        "class_hierarchy": [
            {"child": child, "parent": parent}
            for child, parent in build_real_class_hierarchy(ontology).items()
            if child and parent
        ],
        "synonym_mapping": ontology.get("synonym_mapping", {}),
    }


def json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


class OntologyContextBuilder:
    def __init__(
        self,
        ontology: dict[str, Any],
        *,
        ontology_path: str | Path = DEFAULT_ONTOLOGY_PATH,
        max_chars: int = 16000,
        document_id: str = "",
    ):
        self.ontology = ontology
        self.ontology_path = Path(ontology_path)
        self.max_chars = max_chars
        self.document_id = document_id
        self.full_context = build_full_context(ontology, ontology_path=self.ontology_path, document_id=document_id)
        self.retriever = SubOntologyRetriever(ontology)

    def for_chunk(self, chunk: dict[str, Any]) -> dict[str, Any]:
        if json_size(self.full_context) <= self.max_chars:
            return self.full_context
        sub_schema = self.retriever.retrieve(chunk.get("breadcrumb_path", ""), chunk.get("content", ""))
        sub_schema["ontology_version"] = self.full_context["ontology_version"]
        sub_schema["document_id"] = self.document_id
        while json_size(sub_schema) > self.max_chars and len(sub_schema.get("class_list", [])) > 3:
            sub_schema["class_list"] = sub_schema["class_list"][:-1]
            sub_schema["class_hierarchy"] = sub_schema.get("class_hierarchy", [])[:-1]
        return sub_schema

    def summary(self) -> dict[str, Any]:
        return {
            "ontology_version": self.full_context["ontology_version"],
            "class_count": len(self.full_context.get("class_list", [])),
            "object_property_count": len(self.full_context.get("object_property_list", [])),
            "datatype_property_count": len(self.full_context.get("datatype_property_list", [])),
            "constraint_count": len(self.full_context.get("domain_range_constraints", [])),
            "max_chars": self.max_chars,
        }
