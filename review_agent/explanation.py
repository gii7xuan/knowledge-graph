"""Traceable page summaries built only from verified records."""

from typing import Any, Dict, Iterable, Mapping


def explain_from_verified_records(
    candidate: Mapping[str, Any], guard: Mapping[str, Any], evidence: Mapping[str, Any],
    votes: Iterable[Mapping[str, Any]], route: Mapping[str, Any],
) -> Dict[str, Any]:
    votes = list(votes)
    result = route.get("decision") or route.get("route") or "manual_review"
    reason_codes = list(route.get("reason_codes") or [])
    summaries = {
        "auto_accept": "原文证据、审核共识和已验证风险策略均满足自动接纳条件。",
        "auto_reject": "候选存在确定结构错误，或满足已单独验证的拒绝策略。",
        "manual_review": "候选仍需人工核验；系统未把不确定性当作事实结论。",
    }
    support_ids = []
    counter_ids = []
    for vote in votes:
        if vote.get("response_status") != "ok":
            continue
        support_ids.extend(vote.get("supporting_evidence_ids") or [])
        counter_ids.extend(vote.get("counter_evidence_ids") or [])
    return {
        "summary": summaries.get(result, summaries["manual_review"]),
        "reason_codes": reason_codes,
        "checks": [
            {"check": "ontology", "status": guard.get("status", "unknown"),
             "constraint_ids": [guard.get("relation_id")] if guard.get("relation_id") else []},
            {"check": "quote_location", "status": "pass" if evidence.get("items") else "unknown",
             "evidence_ids": [item.get("evidence_id") for item in evidence.get("items", [])[:3]]},
        ],
        "supporting_evidence_ids": list(dict.fromkeys(support_ids)),
        "counter_evidence_ids": list(dict.fromkeys(counter_ids)),
        "open_questions": list(route.get("open_questions") or []),
    }
