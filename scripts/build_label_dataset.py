"""Build a label dataset from human review logs."""

import argparse
import json
from pathlib import Path


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
    for path, log_item in iter_log_items(review_logs_dir):
        annotation = log_item.get("annotation") or {}
        current = log_item.get("current") or {}
        original = log_item.get("original") or {}
        source = current or original
        if "triple_correct" not in annotation:
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
                "label": int(annotation.get("triple_correct")),
                "evidence_support": annotation.get("evidence_support"),
                "relation_mapping_correct": annotation.get("relation_mapping_correct"),
                "corrected_triple": annotation.get("corrected_triple"),
            }
        )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(rows, file, ensure_ascii=False, indent=2)
    return rows


def main():
    parser = argparse.ArgumentParser(description="Build calibration labels from review logs.")
    parser.add_argument("--review-logs-dir", default=str(ROOT / "review_logs"))
    parser.add_argument("--output", default=str(ROOT / "runs/v2/metrics/label_dataset.json"))
    args = parser.parse_args()
    rows = build_dataset(args.review_logs_dir, args.output)
    print(f"wrote {len(rows)} labeled rows to {args.output}")


if __name__ == "__main__":
    main()
