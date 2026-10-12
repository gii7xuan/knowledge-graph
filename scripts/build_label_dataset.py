"""Build a label dataset from human review logs."""

import argparse
import json
from pathlib import Path

from review_agent.repository import ReviewRepository


ROOT = Path(__file__).resolve().parents[1]


def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def iter_log_items(review_logs_dir):
    for path in sorted(Path(review_logs_dir).glob("*.json")):
        data = load_json(path, [])
        if not isinstance(data, list):
            continue
        for item in data:
            if isinstance(item, dict):
                yield path, item


def build_dataset(review_logs_dir, output_path):
    rows = []
    conflicts = []
    for path, log_item in iter_log_items(review_logs_dir):
        annotation = log_item.get("annotation") or {}
        current = log_item.get("current") or {}
        original = log_item.get("original") or {}
        decision = log_item.get("decision")
        # A legacy label describes the object shown before the click, never the
        # in-place modified/current copy.
        source = original or current
        label = annotation.get("valid_for_kg", annotation.get("triple_correct"))
        if decision == "defer" or label not in (0, 1):
            continue
        if (decision == "reject" and label == 1) or (
            decision in {"modify", "reject"} and not original
        ):
            conflicts.append({"review_log": str(path), "review_id": log_item.get("review_id"),
                              "reason": "legacy_label_conflict"})
            continue
        if decision == "modify" and not log_item.get("reason_code"):
            conflicts.append({"review_log": str(path), "review_id": log_item.get("review_id"),
                              "reason": "ambiguous_legacy_modify"})
            continue
        rows.append(
            {
                "review_log": str(path.relative_to(ROOT)).replace("\\", "/"),
                "document_id": source.get("document_id"),
                "source_id": source.get("source_id"),
                "fact_key": source.get("fact_key"),
                "head": source.get("head"),
                "surface_relation": source.get("surface_relation"),
                "relation": source.get("relation"),
                "tail": source.get("tail"),
                "features": source.get("confidence_features") or {},
                "raw_score": source.get("raw_score"),
                "calibrated_probability": source.get("calibrated_probability"),
                "candidate_id": source.get("candidate_id") or source.get("v3_candidate_id") or log_item.get("review_id"),
                "revision": int(source.get("revision") or 1),
                "audit_id": source.get("audit_id"),
                "feature_schema_version": source.get("feature_schema_version"),
                "label": int(label),
                "label_source": "human_confirmed_legacy",
                "evidence_support": annotation.get("evidence_support"),
                "relation_mapping_correct": annotation.get("relation_mapping_correct"),
                "corrected_triple": annotation.get("corrected_triple"),
            }
        )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(rows, file, ensure_ascii=False, indent=2)
    report_path = output_path.with_name(output_path.stem + "_quality_report.json")
    with report_path.open("w", encoding="utf-8") as file:
        json.dump({
            "valid_labels": len(rows), "conflicts": len(conflicts), "conflict_items": conflicts,
            "missing_feature_snapshots": sum(not item.get("features") for item in rows),
            "positive": sum(item["label"] == 1 for item in rows),
            "negative": sum(item["label"] == 0 for item in rows),
        }, file, ensure_ascii=False, indent=2)
    return rows


def build_v3_dataset(database_path, output_path):
    repository = ReviewRepository(database_path)
    repository.initialize()
    rows = repository.feedback_rows()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "valid_labels": len(rows), "conflicts": 0,
        "missing_feature_snapshots": sum(not item.get("features") for item in rows),
        "positive": sum(item["label"] == 1 for item in rows),
        "negative": sum(item["label"] == 0 for item in rows),
    }
    output_path.with_name(output_path.stem + "_quality_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return rows


def main():
    parser = argparse.ArgumentParser(description="Build calibration labels from review logs.")
    parser.add_argument("--review-logs-dir", default=str(ROOT / "review_logs"))
    parser.add_argument("--database", default=str(ROOT / "runs/v3/review.sqlite3"))
    parser.add_argument("--legacy", action="store_true", help="read legacy review_logs instead of SQLite")
    parser.add_argument("--output", default=str(ROOT / "runs/v3/datasets/labels.json"))
    args = parser.parse_args()
    rows = build_dataset(args.review_logs_dir, args.output) if args.legacy else build_v3_dataset(args.database, args.output)
    print(f"wrote {len(rows)} labeled rows to {args.output}")


if __name__ == "__main__":
    main()
