"""Evaluate review predictions without treating missing probabilities as zero."""

import argparse
import json
from collections import Counter
from pathlib import Path


def evaluate(rows):
    rows = [item for item in rows if isinstance(item, dict) and item.get("label") in (0, 1)]
    accepted = [item for item in rows if item.get("decision") in {"auto_accept", "human_accept"}]
    auto = [item for item in rows if item.get("decision") == "auto_accept"]
    valid = [item for item in rows if item["label"] == 1]
    probability_rows = [item for item in rows if item.get("calibrated_probability") is not None]
    def precision(items):
        return None if not items else sum(item["label"] for item in items) / len(items)
    def brier(items):
        return None if not items else sum(
            (float(item["calibrated_probability"]) - item["label"]) ** 2 for item in items
        ) / len(items)
    return {
        "labeled_count": len(rows), "accepted_precision": precision(accepted),
        "auto_accept_precision": precision(auto),
        "valid_candidate_recall": None if not valid else sum(item in accepted for item in valid) / len(valid),
        "auto_accept_coverage": None if not rows else len(auto) / len(rows),
        "manual_review_rate": None if not rows else sum(item.get("decision") == "manual_review" for item in rows) / len(rows),
        "defer_rate": None if not rows else sum(item.get("human_status") == "deferred" for item in rows) / len(rows),
        "system_error_rate": None if not rows else sum(item.get("system_error") is True for item in rows) / len(rows),
        "probability_sample_count": len(probability_rows), "brier": brier(probability_rows),
        "routes": Counter(item.get("decision", "missing") for item in rows),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    rows = json.loads(Path(args.input).read_text(encoding="utf-8"))
    print(json.dumps(evaluate(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
