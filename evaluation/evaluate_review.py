"""Evaluate review predictions without treating missing probabilities as zero."""

import argparse
import json
import math
from collections import Counter
from pathlib import Path


def evaluate(rows, *, manifest=None, labels=None,revision_labels=None,gold_facts=None,extension_records=None,extension_labels=None,requests=None,prices=None):
    if manifest is not None:
        from .manifest import align_results
        rows = align_results(manifest,rows,labels)
    else:
        if labels is not None: raise ValueError('separate labels require a frozen manifest')
        if any(not isinstance(row,dict) for row in rows): raise ValueError('evaluation rows must be objects')
    from .metrics import current_truth,research_metrics,usage_metrics
    rows=current_truth(rows,revision_labels)
    labeled = [item for item in rows if type(item.get('label')) is int and item['label'] in (0,1)]
    accepted = [item for item in labeled if item.get("decision") in {"auto_accept", "human_accept"}]
    auto = [item for item in rows if item.get("decision") == "auto_accept"]
    labeled_auto = [item for item in labeled if item.get('decision') == 'auto_accept']
    valid = [item for item in labeled if item["label"] == 1]
    def probability_valid(item):
        p=item.get('calibrated_probability')
        return type(p) in (int,float) and math.isfinite(p) and 0 <= p <= 1
    probability_rows = [item for item in labeled if probability_valid(item)]
    def precision(items):
        return None if not items else sum(item["label"] for item in items) / len(items)
    def brier(items):
        return None if not items else sum(
            (float(item["calibrated_probability"]) - item["label"]) ** 2 for item in items
        ) / len(items)
    return {
        "population_count":len(rows), "labeled_count": len(labeled), "unlabeled_count":len(rows)-len(labeled),
        "coverage_denominator":"frozen_population" if manifest is not None else "all_input_rows",
        "manifest_hash":manifest.get('manifest_hash') if manifest else None,
        "accepted_precision": precision(accepted),
        "auto_accept_precision": precision(labeled_auto),
        "labeled_auto_accept_count":len(labeled_auto), "unlabeled_auto_accept_count":len(auto)-len(labeled_auto),
        "valid_candidate_recall": None if not valid else sum(item in accepted for item in valid) / len(valid),
        "auto_accept_coverage": None if not rows else len(auto) / len(rows),
        "manual_review_rate": None if not rows else sum(item.get("decision") == "manual_review" for item in rows) / len(rows),
        "defer_rate": None if not rows else sum(item.get("human_status") == "deferred" for item in rows) / len(rows),
        "system_error_rate": None if not rows else sum(item.get("system_error") is True or
            item.get('status') in {'timeout','failed','error','interrupted','cancelled'} or
            item.get('response_status') in {'timeout','api_error','invalid_contract','invalid_json'} for item in rows) / len(rows),
        "missing_prediction_count":sum(item.get('prediction_missing') is True for item in rows),
        "missing_probability_count":sum(item.get('calibrated_probability') is None for item in rows),
        "invalid_probability_count":sum(item.get('calibrated_probability') is not None and not probability_valid(item) for item in rows),
        "status_counts":Counter('missing' if item.get('prediction_missing') else item.get('status') or item.get('response_status') or 'unspecified' for item in rows),
        "probability_sample_count": len(probability_rows), "brier": brier(probability_rows),
        "routes": Counter(item.get("decision", "missing") for item in rows),
        'research':research_metrics(rows,gold_facts=gold_facts,extension_records=extension_records,extension_labels=extension_labels),
        'usage':usage_metrics(requests,prices) if requests is not None else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument('--manifest'); parser.add_argument('--labels'); parser.add_argument('--output')
    args = parser.parse_args()
    rows = json.loads(Path(args.input).read_text(encoding="utf-8"))
    manifest=json.loads(Path(args.manifest).read_text(encoding='utf-8')) if args.manifest else None
    labels=json.loads(Path(args.labels).read_text(encoding='utf-8')) if args.labels else None
    result=evaluate(rows,manifest=manifest,labels=labels)
    if args.output:
        output=Path(args.output); output.parent.mkdir(parents=True,exist_ok=True)
        from .manifest import align_results
        table=align_results(manifest,rows,labels) if manifest else rows
        output.write_text(json.dumps({'metrics':result,'population_rows':table},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
