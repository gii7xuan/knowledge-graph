"""Freeze a fail-closed auto-accept policy from held-out labeled probabilities."""

import argparse
import hashlib
import json
from pathlib import Path

from confidence.risk_policy import find_accept_threshold
from review_agent.contracts import canonical_json


def main():
    parser = argparse.ArgumentParser(description="Build a validated v3 risk policy.")
    parser.add_argument("--dataset", required=True, help="policy-validation JSON with label and calibrated_probability")
    parser.add_argument("--output", required=True)
    parser.add_argument("--target-precision", type=float, default=0.95)
    parser.add_argument("--min-selected", type=int, default=100)
    parser.add_argument("--data-version", required=True)
    args = parser.parse_args()

    rows = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    usable = [
        item for item in rows if isinstance(item, dict) and item.get("label") in (0, 1)
        and item.get("calibrated_probability") is not None
    ]
    report = find_accept_threshold(
        [int(item["label"]) for item in usable],
        [float(item["calibrated_probability"]) for item in usable],
        target_precision=args.target_precision, min_selected=args.min_selected,
        data_version=args.data_version,
    )
    validation_document_ids = sorted({
        str(item["document_id"]) for item in usable if item.get("document_id")
    })
    version_seed = {
        "data_version": args.data_version, "target_precision": args.target_precision,
        "min_selected": args.min_selected, "report": report,
        "validation_document_ids": validation_document_ids,
    }
    policy_version = "risk_policy_v3_" + hashlib.sha256(
        canonical_json(version_seed).encode("utf-8")
    ).hexdigest()[:12]
    policy = {
        "enabled": bool(report["enabled"]), "validated": bool(report["enabled"]),
        "auto_accept_enabled": bool(report["enabled"]),
        "semantic_auto_reject_enabled": False,
        "target_precision": args.target_precision,
        "accept_threshold": report.get("threshold"), "reject_threshold": None,
        "min_policy_selected": args.min_selected,
        "validation_document_ids": validation_document_ids,
        "policy_version": policy_version, "data_version": args.data_version,
        "selection_report": report,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({"output": str(output), **policy}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
