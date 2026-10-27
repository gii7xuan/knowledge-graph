"""Shared contracts for importing independently generated ontology artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class OntologyImportRequest:
    source: Path
    method_manifest: dict[str, Any] = field(default_factory=dict)
    validation_report: dict[str, Any] = field(default_factory=dict)
    requirements: Any = field(default_factory=list)
    competency_questions: Any = field(default_factory=list)


@dataclass
class OntologyArtifact:
    project_schema: dict[str, Any]
    method_manifest: dict[str, Any]
    validation_report: dict[str, Any]
    mapping_report: dict[str, Any]
    application_profile: dict[str, Any]
    iri_id_map: dict[str, str] = field(default_factory=dict)
    source_ttl: str | None = None
    source_json: dict[str, Any] | None = None
    source_json_text: str | None = None
    requirements: Any = field(default_factory=list)
    competency_questions: Any = field(default_factory=list)
    owl_patch: dict[str, Any] | None = None


class OntologyProvider(Protocol):
    def build_or_import(self, request: OntologyImportRequest) -> OntologyArtifact: ...
