"""Reviewable frozen import drafts shared by the workbench publication flow."""

import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from .application_profile import quarantine_legacy_artifact
from .providers import LegacyOntologyProvider, NeonImportProvider
from .providers.base import OntologyArtifact
from .release_service import artifact_files, canonical_bytes, digest, validation_report
from .release_catalog import ReleaseCatalog, ReleaseConflictError


def create_draft(request, *, provider="auto", quarantine=False, draft_root, release_root):
    if provider not in {"auto", "legacy", "neon"}:
        raise ValueError("unknown ontology provider")
    if provider == "auto":
        provider = "legacy" if request.source.suffix.lower() == ".json" else "neon"
    artifact = (LegacyOntologyProvider() if provider == "legacy" else NeonImportProvider()).build_or_import(request)
    if quarantine:
        artifact = quarantine_legacy_artifact(artifact)
    current = ReleaseCatalog(release_root).current()
    path = Path(draft_root).resolve() / ("import_" + uuid4().hex)
    path.mkdir(parents=True, exist_ok=False)
    for name, content in artifact_files(artifact, {"status": "pending"}).items():
        (path / name).write_bytes(content)
    payload = asdict(artifact)
    (path / "import_artifact.json").write_bytes(canonical_bytes(payload) + b"\n")
    receipt = {
        "draft_id": path.name, "artifact_digest": digest(payload),
        "base_release_id": current.manifest["release_id"] if current else None,
        "ontology_version": digest(artifact.project_schema), "profile_hash": digest(artifact.application_profile),
        "mapping_report_hash": digest(artifact.mapping_report),
    }
    (path / "draft_receipt.json").write_bytes(canonical_bytes(receipt) + b"\n")
    report = validation_report(artifact)
    return {**receipt, "draft_path": str(path), "validation": report,
            "application_profile": artifact.application_profile, "mapping_report": artifact.mapping_report,
            "requires_difference_confirmation": bool(artifact.application_profile.get("requires_difference_confirmation")),
            "status": "pending_approval"}


def publish_draft(path, *, expected_artifact_digest, expected_base_release_id, approved_by, reason,
                  confirm_differences=False, release_root):
    path = Path(path).resolve()
    receipt = json.loads((path / "draft_receipt.json").read_text(encoding="utf-8"))
    payload = json.loads((path / "import_artifact.json").read_text(encoding="utf-8"))
    if not expected_artifact_digest or digest(payload) != expected_artifact_digest:
        raise ValueError("import draft changed since preview; inspect it again")
    if receipt["artifact_digest"] != expected_artifact_digest:
        raise ValueError("import receipt does not match the preview")
    if receipt["base_release_id"] != expected_base_release_id:
        raise ReleaseConflictError("publication base differs from the reviewed import; create a new draft")
    artifact = OntologyArtifact(**payload)
    if (receipt["ontology_version"] != digest(artifact.project_schema)
            or receipt["profile_hash"] != digest(artifact.application_profile)
            or receipt["mapping_report_hash"] != digest(artifact.mapping_report)):
        raise ValueError("draft receipt binding mismatch")
    approval = {"status": "approved", "approved_by": approved_by, "reason": reason,
                "ontology_version": receipt["ontology_version"], "import_artifact_digest": expected_artifact_digest}
    if confirm_differences:
        approval.update({"confirmed_profile_hash": receipt["profile_hash"],
                         "confirmed_mapping_report_hash": receipt["mapping_report_hash"]})
    return ReleaseCatalog(release_root).publish(
        artifact, approval=approval, expected_base_release_id=expected_base_release_id)
