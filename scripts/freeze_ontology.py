"""Validate and create an immutable formal ontology snapshot."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from review_agent.ontology_guard import load_snapshot, validate_ontology


ROOT = Path(__file__).resolve().parents[1]


def freeze_ontology(input_path, output_root=None, *, approved_event_id=None):
    snapshot = load_snapshot(input_path)
    report = validate_ontology(snapshot.ontology)
    report.update({
        "ontology_version": snapshot.ontology_version,
        "source_path": str(Path(input_path)),
        "approved_event_id": approved_event_id,
        "approval_status": "approved_release" if approved_event_id else "operator_selected_baseline",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    })
    root = Path(output_root or ROOT / "runs" / "v3" / "ontology")
    target = root / snapshot.ontology_version
    target.mkdir(parents=True, exist_ok=True)
    report_path = target / "validation_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report["valid"]:
        return {"ok": False, "report": str(report_path), **report}
    snapshot_path = target / "ontology_snapshot.json"
    if snapshot_path.exists():
        existing = load_snapshot(snapshot_path)
        if existing.ontology_version != snapshot.ontology_version:
            raise RuntimeError("immutable ontology snapshot collision")
    else:
        snapshot_path.write_text(json.dumps(snapshot.ontology, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "snapshot": str(snapshot_path), "report": str(report_path), **report}


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
