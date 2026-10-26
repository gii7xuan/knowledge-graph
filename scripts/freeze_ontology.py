"""Validate and create an immutable formal ontology snapshot."""

import argparse
import json
from pathlib import Path

from ontologyBuild.providers import LegacyOntologyProvider, OntologyImportRequest
from ontologyBuild.release_service import artifact_files, digest, publish_release, validation_report


ROOT = Path(__file__).resolve().parents[1]


def freeze_ontology(input_path, output_root=None, *, approved_event_id=None):
    artifact = LegacyOntologyProvider().build_or_import(OntologyImportRequest(Path(input_path)))
    report = validation_report(artifact)
    root = Path(output_root or ROOT / "runs" / "ontology_releases")
    if not report["project_validation"]["valid"]:
        from uuid import uuid4
        draft = root / ("invalid_" + uuid4().hex)
        draft.mkdir(parents=True)
        for name, content in artifact_files(artifact, {"status": "pending"}).items():
            (draft / name).write_bytes(content)
        return {"ok": False, "report": str(draft / "validation_report.json"), **report["project_validation"]}
    release = publish_release(artifact, root, approval={
        "status": "approved", "approved_by": "freeze_ontology_operator",
        "reason": "approved_event" if approved_event_id else "operator_selected_baseline",
        "approved_event_id": approved_event_id, "ontology_version": digest(artifact.project_schema),
    })
    return {"ok": True, "snapshot": str(release.snapshot_path),
            "report": str(release.path / "validation_report.json"),
            **report["project_validation"], **release.binding()}


def main():
    parser = argparse.ArgumentParser(description="Validate and freeze an ontology snapshot.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-root")
    parser.add_argument("--approved-event-id")
    args = parser.parse_args()
    result = freeze_ontology(args.input, args.output_root, approved_event_id=args.approved_event_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
