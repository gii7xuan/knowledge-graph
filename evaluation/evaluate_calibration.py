"""Evaluate calibrated triple probabilities."""

import argparse
import json
import math
from pathlib import Path
from .fact_matching import identity_summary


ROOT = Path(__file__).resolve().parents[1]


def load_rows(path):
    with Path(path).open("r", encoding="utf-8") as file:
        return [item for item in json.load(file) if isinstance(item, dict)]


def probability(item):
    value = item.get("calibrated_probability")
    if value is None:
        return None
    try:
        p=float(value)
        return p if math.isfinite(p) and 0<=p<=1 else None
    except (TypeError, ValueError):
        return None


def ece(rows, bins=10):
    total = len(rows)
    if total == 0:
        return 0.0
    error = 0.0
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        bucket = [
            item for item in rows
            if probability(item) is not None and (
                low <= probability(item) < high or (index == bins - 1 and probability(item) == 1.0)
            )
        ]
        if not bucket:
            continue
        accuracy = sum(int(item.get("label", 0)) for item in bucket) / len(bucket)
        confidence = sum(probability(item) for item in bucket) / len(bucket)
        error += len(bucket) / total * abs(accuracy - confidence)
    return error


def brier(rows):
    rows = [item for item in rows if probability(item) is not None]
    if not rows:
        return None
    return sum((probability(item) - int(item.get("label", 0))) ** 2 for item in rows) / len(rows)


def coverage_at_precision(rows, target_precision):
    best = 0.0
    rows = [item for item in rows if probability(item) is not None]
    for threshold in sorted({probability(item) for item in rows}):
        selected = [item for item in rows if probability(item) >= threshold]
        if not selected:
            continue
        precision = sum(int(item.get("label", 0)) for item in selected) / len(selected)
        if precision >= target_precision:
            best = max(best, len(selected) / len(rows))
    return best


def duplicate_rate(rows):
    from .fact_matching import identity_summary
    return identity_summary(rows)['duplicate_rate']


def main():
    parser = argparse.ArgumentParser(description="Evaluate calibration quality.")
    parser.add_argument("--dataset", default=str(ROOT / "runs/v2/metrics/label_dataset.json"))
    parser.add_argument("--target-precision", type=float, default=0.95)
    args = parser.parse_args()

    rows = load_rows(args.dataset)
    probability_rows = [item for item in rows if probability(item) is not None]
    positives = sum(int(item.get("label", 0)) for item in rows)
    metrics = {
        "total": len(rows),
        "precision_at_0_5": (
            sum(int(item.get("label", 0)) for item in probability_rows if probability(item) >= 0.5)
            / max(1, sum(1 for item in probability_rows if probability(item) >= 0.5))
        ),
        "positive_rate": positives / len(rows) if rows else 0.0,
        "probability_sample_count": len(probability_rows),
        "ece": ece(probability_rows),
        "brier": brier(probability_rows),
        "coverage_at_target_precision": coverage_at_precision(probability_rows, args.target_precision),
        "duplicate_rate": duplicate_rate(rows),
        'fact_identity':identity_summary(rows),
    }
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
