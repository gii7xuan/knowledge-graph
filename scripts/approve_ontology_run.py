"""Bulk-approve every ontology candidate in an immutable run review file."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ontologyBuild.candidate_quality import approval_errors
from ontologyBuild.review_module import get_candidate_display_name, summarize_review_result


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def backup_once(source: Path, destination: Path) -> None:
    if source.exists() and not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="批量通过指定本体运行中的全部候选")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--reviewer", default="human")
    parser.add_argument("--comment", default="用户明确要求批量审核通过全部候选")
    parser.add_argument(
        "--force-quality-gate",
        action="store_true",
        help="允许人工决定覆盖缺字段或候选质量门禁",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    run_dir = ROOT / "runs" / "ontology" / args.run_id
    result_path = run_dir / "review_result.json"
    log_path = run_dir / "review_log.jsonl"
    manifest_path = run_dir / "manifest.json"
    if not result_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError(f"本体运行缺少审核结果或 manifest: {run_dir}")

    result = json.loads(result_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if result.get("run_id") and result["run_id"] != args.run_id:
        raise ValueError("review_result.run_id 与目标运行不一致")

    list_names = (
        "accepted_candidates",
        "rejected_candidates",
        "pending_review_candidates",
        "deferred_candidates",
    )
    candidates = [
        copy.deepcopy(candidate)
        for list_name in list_names
        for candidate in result.get(list_name, [])
        if isinstance(candidate, dict)
    ]
    ids = [candidate.get("candidate_id") for candidate in candidates]
    if any(not candidate_id for candidate_id in ids):
        raise ValueError("存在缺少 candidate_id 的候选，不能安全批量审核")
    duplicate_ids = [candidate_id for candidate_id, count in Counter(ids).items() if count > 1]
    if duplicate_ids:
        raise ValueError(f"存在重复 candidate_id，不能安全批量审核: {duplicate_ids[:5]}")

    blocked = [(candidate, approval_errors(candidate)) for candidate in candidates]
    blocked = [(candidate, errors) for candidate, errors in blocked if errors]
    if blocked and not args.force_quality_gate:
        raise RuntimeError(
            f"{len(blocked)}/{len(candidates)} 个候选未通过质量门禁；"
            "如需人工覆盖，请显式使用 --force-quality-gate"
        )

    backup_once(result_path, run_dir / "review_result.before_bulk_accept.json")
    backup_once(log_path, run_dir / "review_log.before_bulk_accept.jsonl")
    backup_once(manifest_path, run_dir / "manifest.before_bulk_accept.json")

    compatibility_result = ROOT / "ontologyBuild" / "ontology_review_result.json"
    compatibility_log = ROOT / "ontologyBuild" / "ontology_review_log.json"
    current_pointer = ROOT / "runs" / "ontology" / "current.json"
    backup_dir = run_dir / "compatibility_backups"
    backup_once(compatibility_result, backup_dir / "ontology_review_result.before_bulk_accept.json")
    backup_once(compatibility_log, backup_dir / "ontology_review_log.before_bulk_accept.json")
    backup_once(current_pointer, backup_dir / "current.before_bulk_accept.json")

    approval_time = now_iso()
    approval_id = "bulk_accept_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    blocked_by_id = {candidate["candidate_id"]: errors for candidate, errors in blocked}
    new_logs = []
    accepted = []
    for index, candidate in enumerate(candidates, start=1):
        original_decision = candidate.get("final_decision")
        candidate.pop("original_candidate", None)
        candidate["final_decision"] = "accepted"
        candidate["decision_source"] = "user"
        candidate["reviewer"] = args.reviewer
        candidate["review_comment"] = args.comment
        candidate["review_time"] = approval_time
        candidate["bulk_approval_id"] = approval_id
        errors = blocked_by_id.get(candidate["candidate_id"], [])
        if errors:
            candidate["quality_gate_overridden"] = True
            candidate["quality_gate_override_reasons"] = errors
        accepted.append(candidate)
        new_logs.append(
            {
                "log_id": f"{approval_id}_{index:06d}",
                "candidate_id": candidate["candidate_id"],
                "candidate_type": candidate.get("candidate_type"),
                "name": get_candidate_display_name(candidate),
                "system_recommendation": candidate.get("system_recommendation"),
                "original_decision": original_decision,
                "final_decision": "accepted",
                "decision_source": "user",
                "reviewer": args.reviewer,
                "review_comment": args.comment,
                "reasons": ["用户明确要求批量通过全部候选"],
                "user_decision": "accept",
                "quality_gate_overridden": bool(errors),
                "quality_gate_override_reasons": errors,
                "timestamp": approval_time,
                "bulk_approval_id": approval_id,
            }
        )

    result["run_id"] = args.run_id
    result["accepted_candidates"] = accepted
    result["rejected_candidates"] = []
    result["pending_review_candidates"] = []
    result["deferred_candidates"] = []
    result["review_log"] = list(result.get("review_log") or []) + new_logs
    result["summary"] = summarize_review_result(result)
    result["passed"] = True
    result["updated_at"] = approval_time
    result["bulk_approval"] = {
        "approval_id": approval_id,
        "reviewer": args.reviewer,
        "comment": args.comment,
        "approved_at": approval_time,
        "approved_count": len(accepted),
        "quality_gate_overridden_count": len(blocked),
        "forced": bool(blocked),
    }
    result["note"] = (
        "全部候选已按用户明确指令人工通过；质量门禁未通过的候选保留覆盖原因。"
        "此操作不等于把候选发布到正式 ontology/ontology.json。"
    )

    manifest["review_approval"] = result["bulk_approval"]
    manifest.setdefault("outputs", {})["review_result"] = str(result_path)
    write_json(result_path, result)
    log_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in result["review_log"]),
        encoding="utf-8",
    )
    write_json(manifest_path, manifest)
    write_json(compatibility_result, result)
    write_json(compatibility_log, result["review_log"])
    write_json(
        current_pointer,
        {
            "run_id": args.run_id,
            "run_dir": str(run_dir),
            "manifest": str(manifest_path),
        },
    )

    report = {
        "run_id": args.run_id,
        "approval_id": approval_id,
        "approved": len(accepted),
        "quality_gate_overridden": len(blocked),
        "summary": result["summary"],
        "review_result": str(result_path),
        "backup": str(run_dir / "review_result.before_bulk_accept.json"),
        "formal_ontology_updated": False,
    }
    write_json(run_dir / "bulk_approval_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
