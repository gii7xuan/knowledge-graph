"""Train the triple probability calibrator from labeled review data."""

import argparse
import json
from pathlib import Path

from confidence.calibrator import TripleCalibrator


ROOT = Path(__file__).resolve().parents[1]


def load_dataset(path):
    with Path(path).open("r", encoding="utf-8") as file:
        data = json.load(file)
    return [item for item in data if isinstance(item, dict) and item.get("features")]


def main():
    parser = argparse.ArgumentParser(description="Train Logistic Regression calibrator.")
    parser.add_argument("--dataset", default=str(ROOT / "runs/v2/metrics/label_dataset.json"))
    parser.add_argument("--output", default=str(ROOT / "confidence/calibrator.joblib"))
    args = parser.parse_args()

    rows = load_dataset(args.dataset)
    if not rows:
        raise SystemExit("no labeled rows with features found")
    labels = [int(item["label"]) for item in rows]
    if len(set(labels)) < 2:
        raise SystemExit("training needs both positive and negative labels")

    calibrator = TripleCalibrator()
    calibrator.fit([item["features"] for item in rows], labels)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    calibrator.save(args.output)
    print(f"trained calibrator on {len(rows)} rows and saved {args.output}")


if __name__ == "__main__":
    main()
