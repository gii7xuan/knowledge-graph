"""Train the triple probability calibrator from labeled review data."""

import argparse
import json
from pathlib import Path

from confidence.calibrator import TripleCalibrator
from review_agent.contracts import REVIEW_VERSION_FIELDS, FEATURE_SCHEMA_VERSION, AGENT_VERSION_FIELDS


ROOT = Path(__file__).resolve().parents[1]


def load_dataset(path):
    with Path(path).open("r", encoding="utf-8") as file:
        data = json.load(file)
    return [item for item in data if isinstance(item, dict) and item.get("features")]


def training_versions(rows):
    fields = REVIEW_VERSION_FIELDS
    fields += tuple(key for key in ("prompt_hash", "response_contract_hash", "committee_policy_hash", "planner_prompt_hash")
                    if any(key in (row.get("review_versions") or {}) for row in rows))
    if any((row.get("review_versions") or {}).get("planner_contract_version") for row in rows):
        fields += AGENT_VERSION_FIELDS
    elif any((row.get("review_versions") or {}).get("executed_strategy") for row in rows):
        fields += ("configured_strategy", "executed_strategy")
    signatures = {tuple((row.get("review_versions") or {}).get(key) for key in fields)
                  for row in rows}
    if len(signatures) != 1 or not all(next(iter(signatures), ())):
        raise ValueError("training rows must have one complete prompt/contract/evidence/committee version set")
    if any(not row.get("audit_id") or row.get("audit_snapshot_status") != "bound" for row in rows):
        raise ValueError("training requires labels bound to completed audit snapshots")
    if any(row.get("feature_schema_version") != FEATURE_SCHEMA_VERSION for row in rows):
        raise ValueError("training feature schema is missing or incompatible")
    return dict(zip(fields, next(iter(signatures))))


def main():
    parser = argparse.ArgumentParser(description="Train Logistic Regression calibrator.")
    parser.add_argument("--dataset", default=str(ROOT / "runs/v3/datasets/labels.json"))
    parser.add_argument("--split-manifest")
    parser.add_argument("--training-data-version", default="unspecified")
    parser.add_argument("--output", default=str(ROOT / "confidence/calibrator.joblib"))
    args = parser.parse_args()

    rows = load_dataset(args.dataset)
    if not rows:
        raise SystemExit("no labeled rows with features found")
    labels = [int(item["label"]) for item in rows]
    if len(set(labels)) < 2:
        raise SystemExit("training needs both positive and negative labels")

    calibrator = TripleCalibrator()
    if args.split_manifest:
        split_manifest = json.loads(Path(args.split_manifest).read_text(encoding="utf-8"))
        train_groups = set(split_manifest.get("train_document_ids") or [])
        held_out = set(split_manifest.get("test_document_ids") or []) | set(split_manifest.get("calibration_document_ids") or [])
        if train_groups & held_out:
            raise SystemExit("split manifest leaks document groups across train and held-out data")
        rows = [item for item in rows if not train_groups or item.get("document_id") in train_groups]
        labels = [int(item["label"]) for item in rows]
    versions = training_versions(rows)
    calibrator.fit(
        [item["features"] for item in rows], labels,
        training_data_version=args.training_data_version,
        metadata={"split_manifest": args.split_manifest, **versions},
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    calibrator.save(args.output)
    print(f"trained calibrator on {len(rows)} rows and saved {args.output}")


if __name__ == "__main__":
    main()
