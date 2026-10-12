"""Shared candidate validation used by extraction and review entry points."""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from ontologyBuild.business_validate import (
        validate_and_process_business_data,
        validate_business_data,
    )
    from ontologyBuild.ontology_candidate_schema import validate_format_data
except ImportError:
    from business_validate import validate_and_process_business_data, validate_business_data
    from ontology_candidate_schema import validate_format_data


RESULT_FIELDS = (
    "candidate_classes",
    "candidate_object_properties",
    "candidate_datatype_properties",
    "candidate_constraints",
    "ontology_mapping",
    "uncertain_candidates",
)


def empty_candidates() -> dict[str, list[dict[str, Any]]]:
    return {field: [] for field in RESULT_FIELDS}


def _set_validation(item: dict[str, Any], status: str, issues: list[dict[str, Any]] | None = None) -> None:
    item["validation_status"] = status
    item["format_passed"] = status == "passed"
    item["evidence_passed"] = status == "passed"
    item["business_passed"] = status == "passed"
    item["semantic_support_status"] = "not_run"
    item["validation_issues"] = issues or []


def annotate_legacy_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    item = copy.deepcopy(candidate)
    if "validation_status" not in item:
        item["validation_status"] = "legacy_unverified"
        item.setdefault("format_passed", None)
        item.setdefault("evidence_passed", None)
        item.setdefault("business_passed", None)
        item.setdefault("semantic_support_status", "not_run")
        item.setdefault(
            "validation_issues",
            [{"rule": "legacy_unverified", "message": "历史候选未经过新流水线校验"}],
        )
    return item


def annotate_candidates(candidates: dict[str, Any], status: str, issues: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = copy.deepcopy(candidates)
    for section in RESULT_FIELDS:
        for item in result.get(section, []) or []:
            if isinstance(item, dict):
                item_status = "dependency_pending" if section == "uncertain_candidates" else status
                _set_validation(item, item_status, issues)
    return result


def validate_candidate_batch(
    candidates: dict[str, Any],
    chunks: list[dict[str, Any]],
    *,
    ontology_path: str | Path,
) -> dict[str, Any]:
    """Validate and conservatively process LLM candidates.

    The function keeps a rejected/discarded record so failed entries do not
    silently disappear from the run.
    """
    raw_candidates = copy.deepcopy(candidates or empty_candidates())
    format_report = validate_format_data(raw_candidates)
    if not format_report.valid:
        return {
            "passed": False,
            "validated_candidates": empty_candidates(),
            "manual_review": [],
            "rejected_candidates": [
                {
                    "reason": "format_validation_failed",
                    "issues": [issue.model_dump() for issue in format_report.issues],
                    "candidate": raw_candidates,
                }
            ],
            "quality_report": {
                "format_valid": False,
                "business_valid": False,
                "issue_counts": {"format_validation_failed": len(format_report.issues)},
            },
        }

    business_report = validate_business_data(raw_candidates, chunks, ontology_path=ontology_path)
    processing = validate_and_process_business_data(raw_candidates, chunks, ontology_path=ontology_path)
    processed = annotate_candidates(processing.candidates, "passed")

    for review_item in processing.manual_review:
        candidate = review_item.get("candidate")
        if isinstance(candidate, dict):
            _set_validation(
                candidate,
                "dependency_pending",
                [{"rule": review_item.get("review_type"), "message": review_item.get("reason")}],
            )
            source_section = review_item.get("source_section")
            if source_section in processed:
                processed[source_section].append(candidate)
    rejected = []
    for item in processing.discarded:
        rejected.append(
            {
                "reason": item.get("reason"),
                "source_section": item.get("source_section"),
                "candidate": item.get("candidate"),
            }
        )

    issue_counts = Counter(issue.rule for issue in business_report.issues)
    return {
        "passed": format_report.valid and business_report.valid,
        "validated_candidates": processed,
        "manual_review": processing.manual_review,
        "rejected_candidates": rejected,
        "quality_report": {
            "format_valid": format_report.valid,
            "business_valid": business_report.valid,
            "issue_counts": dict(issue_counts),
            "discarded_count": len(processing.discarded),
            "manual_review_count": len(processing.manual_review),
            "candidate_counts": {field: len(processed.get(field, [])) for field in RESULT_FIELDS},
        },
        "actions": processing.actions,
    }


def write_validation_artifacts(validation: dict[str, Any], run_dir: str | Path) -> None:
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)
    files = {
        "validated_candidates.json": validation.get("validated_candidates", empty_candidates()),
        "rejected_candidates.json": validation.get("rejected_candidates", []),
        "quality_report.json": validation.get("quality_report", {}),
    }
    for name, value in files.items():
        (run_path / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
