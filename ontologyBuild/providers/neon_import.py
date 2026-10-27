"""Import Turtle and provenance from Extended NeOn-GPT, without invoking it."""

import hashlib

from ontologyBuild.owl_to_project_schema import convert_turtle
from .base import OntologyArtifact, OntologyImportRequest


class NeonImportProvider:
    def build_or_import(self, request: OntologyImportRequest) -> OntologyArtifact:
        raw = request.source.read_bytes()
        source = raw.decode("utf-8")
        converted = convert_turtle(source.lstrip("\ufeff"),base_iri=request.source.resolve().as_uri())
        return OntologyArtifact(
            **converted, source_ttl=source,
            method_manifest={"method": request.method_manifest.get("method", "unspecified_turtle_source"),
                             "provider": "extended_neon_gpt_artifact_import", "execution_status": "import_only",
                             "provenance_status": "supplied_unverified" if request.method_manifest else "not_provided",
                             "repository": "https://github.com/NadeenAhmad/neon-gpt-extended",
                             "upstream": request.method_manifest,
                             "source_sha256": hashlib.sha256(raw).hexdigest(),'source_base_iri':request.source.resolve().as_uri(),'rdf_parse_version':'lexical_turtle_v1'},
            validation_report={"external_validation": request.validation_report or {"status": "not_run"},
                               "owl_reasoner": {"status": "not_run"}},
            requirements=request.requirements, competency_questions=request.competency_questions,
        )
