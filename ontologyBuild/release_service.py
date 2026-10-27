"""Validate, publish and verify immutable, content-addressed ontology bundles."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from review_agent.ontology_guard import validate_ontology
from .providers.base import OntologyArtifact
from .providers.legacy import definition_conflicts


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def validation_report(artifact: OntologyArtifact):
    report = validate_ontology(artifact.project_schema)
    errors = [*report["errors"], *definition_conflicts(artifact.project_schema),
              *(artifact.mapping_report.get("errors") or [])]
    errors = list({canonical_bytes(item): item for item in errors}.values())
    if not report["class_count"]:
        errors.append({"code": "EMPTY_ONTOLOGY"})
    if artifact.source_json is not None and artifact.source_json_text is not None:
        if json.loads(artifact.source_json_text.lstrip("\ufeff")) != artifact.source_json:
            errors.append({"code": "SOURCE_JSON_MISMATCH"})
    if artifact.project_schema.get("application_profile") != artifact.application_profile:
        errors.append({"code": "PROFILE_MISMATCH"})
    if any(item.get("blocks_auto_review") for item in artifact.mapping_report.get("unsupported_axioms", [])):
        if not artifact.application_profile.get("auto_review_blocked"):
            errors.append({"code": "UNSUPPORTED_AXIOMS_NOT_BLOCKED"})
    external = artifact.validation_report.get("external_validation") or {}
    if artifact.method_manifest.get('rdf_parse_version') not in (None,'lexical_turtle_v1'):
        errors.append({'code':'UNSUPPORTED_RDF_PARSE_VERSION'})
    if artifact.method_manifest.get('owl_patch_hash') and artifact.owl_patch is None:
        errors.append({'code':'MISSING_OWL_PATCH_PROOF'})
    if artifact.source_ttl is not None:
        from .rdf_artifact import validate_owl_view, validate_patch_bindings
        try:
            validate_owl_view(artifact.source_ttl,artifact.project_schema,artifact.application_profile,artifact.iri_id_map,artifact.mapping_report,base_iri=artifact.method_manifest.get('source_base_iri'),preserve_lexical=artifact.method_manifest.get('rdf_parse_version')=='lexical_turtle_v1')
            if artifact.owl_patch is not None: validate_patch_bindings(artifact.owl_patch,artifact.source_ttl,artifact.iri_id_map,artifact.method_manifest)
        except (ValueError,KeyError,TypeError) as exc: errors.append({'code':'OWL_SOURCE_VIEW_MISMATCH','detail':str(exc)})
    elif artifact.owl_patch is not None: errors.append({'code':'OWL_PATCH_WITHOUT_SOURCE'})
    if external.get("status") in {"failed", "fail", "invalid"} or external.get("valid") is False:
        errors.append({"code": "EXTERNAL_VALIDATION_FAILED"})
    return {**deepcopy(artifact.validation_report), "project_validation": {**report, "errors": errors,
                                                                         "valid": not errors},
            "competency_checks": artifact.validation_report.get("competency_checks", {"status": "not_run"}),
            "domain_semantic_review": artifact.validation_report.get("domain_semantic_review", {"status": "not_run"}),
            "auto_review_allowed": not artifact.application_profile.get("auto_review_blocked", False)}


def artifact_files(artifact: OntologyArtifact, approval):
    report = validation_report(artifact)
    documents = {
        "ontology_snapshot.json": artifact.project_schema,
        "application_profile.json": artifact.application_profile,
        "iri_id_map.json": artifact.iri_id_map,
        "mapping_report.json": artifact.mapping_report,
        "requirements.json": artifact.requirements,
        "competency_questions.json": artifact.competency_questions,
        "method_manifest.json": artifact.method_manifest,
        "validation_report.json": report,
        "approval.json": approval,
    }
    if artifact.source_json is not None:
        documents["source_ontology.json"] = artifact.source_json
    if artifact.owl_patch is not None: documents['owl_patch.json']=artifact.owl_patch
    files = {name: canonical_bytes(value) + b"\n" for name, value in documents.items()}
    if artifact.source_json_text is not None:
        files["source_ontology.json"] = artifact.source_json_text.encode("utf-8")
    if artifact.source_ttl is not None:
        files["ontology.ttl"] = artifact.source_ttl.encode("utf-8")
    return files


@dataclass(frozen=True)
class OntologyRelease:
    path: Path
    manifest: dict
    ontology: dict
    application_profile: dict

    @property
    def snapshot_path(self):
        return self.path / "ontology_snapshot.json"

    def binding(self):
        return {"ontology_release_id": self.manifest["release_id"],
                "ontology_version": self.manifest["ontology_version"],
                "profile_hash": self.manifest["profile_hash"],
                "ontology_release_path": str(self.path)}


def publish_release(artifact: OntologyArtifact, root: str | Path, *, approval: dict) -> OntologyRelease:
    """An explicit operator action; generation/import alone never publishes."""
    if (approval.get("status") != "approved" or not str(approval.get("approved_by") or "").strip()
            or not str(approval.get("reason") or "").strip()):
        raise ValueError("release requires approved status, approved_by and reason")
    if approval.get("ontology_version") and approval["ontology_version"] != digest(artifact.project_schema):
        raise ValueError("approval bound to a different ontology version")
    if artifact.application_profile.get("requires_difference_confirmation"):
        if (approval.get("confirmed_profile_hash") != digest(artifact.application_profile)
                or approval.get("confirmed_mapping_report_hash") != digest(artifact.mapping_report)):
            raise ValueError("application profile differences require explicit hash-bound confirmation")
    if not validation_report(artifact)["project_validation"]["valid"]:
        raise ValueError("ontology failed project/conversion validation; inspect the import report")
    files = artifact_files(artifact, approval)
    payload = {"manifest_version": "ontology_release_v1", "status": "published",
               "ontology_version": digest(artifact.project_schema),
               "profile_hash": digest(artifact.application_profile),
               "artifacts": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}}
    release_id = "release_" + digest(payload)
    manifest = {**payload, "release_id": release_id}
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / release_id
    if target.exists():
        return load_release(target)
    staging = root / (".staging_" + uuid4().hex)
    staging.mkdir()
    try:
        for name, content in files.items():
            (staging / name).write_bytes(content)
        (staging / "release_manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
        load_release(staging)
        try:
            os.rename(staging, target)
        except OSError:
            if not target.exists():
                raise
            existing = load_release(target)
            if existing.manifest != manifest:
                raise ValueError("immutable release collision")
        return load_release(target)
    finally:
        if staging.exists():
            # Only this function's freshly created staging directory can be removed.
            if staging.parent != root or not staging.name.startswith(".staging_"):
                raise RuntimeError("invalid staging cleanup path")
            shutil.rmtree(staging)


def load_release(path: str | Path) -> OntologyRelease:
    root = Path(path).resolve()
    if root.is_file():
        if root.name != "release_manifest.json":
            raise ValueError("expected a release directory or release_manifest.json")
        root = root.parent
    manifest = json.loads((root / "release_manifest.json").read_text(encoding="utf-8"))
    payload = {key: value for key, value in manifest.items() if key != "release_id"}
    if (manifest.get("manifest_version") != "ontology_release_v1" or manifest.get("status") != "published"
            or manifest.get("release_id") != "release_" + digest(payload)):
        raise ValueError("invalid ontology release manifest")
    if root.name.startswith("release_") and root.name != manifest["release_id"]:
        raise ValueError("release directory identity does not match its manifest")
    required = {"ontology_snapshot.json", "application_profile.json", "iri_id_map.json", "mapping_report.json",
                "requirements.json", "competency_questions.json", "method_manifest.json",
                "validation_report.json", "approval.json"}
    artifacts = manifest.get("artifacts") or {}
    if not required.issubset(artifacts) or not ({"ontology.ttl", "source_ontology.json"} & artifacts.keys()):
        raise ValueError("incomplete ontology release")
    for name, expected in artifacts.items():
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("invalid release artifact path")
        artifact_path = root / name
        if artifact_path.resolve().parent != root or artifact_path.is_symlink():
            raise ValueError("release artifacts must be local regular files")
        if hashlib.sha256(artifact_path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"ontology release artifact hash mismatch: {name}")

    def read(name):
        return json.loads((root / name).read_text(encoding="utf-8"))

    ontology, profile = read("ontology_snapshot.json"), read("application_profile.json")
    method = read("method_manifest.json")
    if method.get('rdf_parse_version') not in (None,'lexical_turtle_v1'): raise ValueError('unsupported RDF parse version')
    source_file = "ontology.ttl" if "ontology.ttl" in artifacts else "source_ontology.json"
    if method.get("source_sha256") and hashlib.sha256((root / source_file).read_bytes()).hexdigest() != method["source_sha256"]:
        raise ValueError("source provenance hash mismatch")
    if digest(ontology) != manifest["ontology_version"] or digest(profile) != manifest["profile_hash"]:
        raise ValueError("ontology/profile version mismatch")
    if ontology.get("application_profile") != profile:
        raise ValueError("ontology application profile mismatch")
    approval = read("approval.json")
    mapping = read("mapping_report.json")
    if source_file=='ontology.ttl':
        from .rdf_artifact import validate_owl_view, validate_patch_bindings
        source=(root/source_file).read_text(encoding='utf-8')
        validate_owl_view(source,ontology,profile,read('iri_id_map.json'),mapping,base_iri=method.get('source_base_iri'),preserve_lexical=method.get('rdf_parse_version')=='lexical_turtle_v1')
        if 'owl_patch.json' in artifacts or method.get('owl_patch_hash'):
            if 'owl_patch.json' not in artifacts: raise ValueError('missing OWL patch artifact')
            validate_patch_bindings(read('owl_patch.json'),source,read('iri_id_map.json'),method)
    if profile.get("requires_difference_confirmation"):
        if (approval.get("confirmed_profile_hash") != digest(profile)
                or approval.get("confirmed_mapping_report_hash") != digest(mapping)):
            raise ValueError("unconfirmed application profile differences")
    if mapping.get("errors"):
        raise ValueError("unresolved mapping errors in release")
    if any(item.get("blocks_auto_review") for item in mapping.get("unsupported_axioms", [])):
        if not profile.get("auto_review_blocked"):
            raise ValueError("unsupported axioms bypassed application profile")
    if (approval.get("status") != "approved" or not approval.get("approved_by") or not approval.get("reason")
            or (approval.get("ontology_version") and approval["ontology_version"] != manifest["ontology_version"])
            or not read("validation_report.json").get("project_validation", {}).get("valid")
            or not validate_ontology(ontology)["valid"] or definition_conflicts(ontology)):
        raise ValueError("unapproved or invalid ontology release")
    return OntologyRelease(root, manifest, ontology, profile)
