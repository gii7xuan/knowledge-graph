"""Independent validator invocation and response contract checks."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Mapping

from evidence.verification import verify_vote_references


VALID_VERDICTS = {"valid", "invalid", "uncertain"}
VALID_SUPPORT = {"supported", "contradicted", "insufficient"}
VALID_SCHEMA = {"pass", "fail", "unknown", "not_applicable"}


def validator_payload(proposal: Mapping[str, Any]) -> Dict[str, Any]:
    """Expose only the frozen candidate, checks, and selectable evidence ids."""
    return {
        "proposal_digest": proposal["proposal_digest"],
        "candidate": proposal["candidate"],
        "ontology_check": proposal.get("ontology_check", {}),
        "evidence": proposal.get("evidence_bundle", {}).get("items", []),
        "review_memory": proposal.get("review_memory", {}),
        "rubric": [
            "完整主谓宾是否被原文支持", "否定、条件、数量、时间和指代是否保留",
            "关系语义及方向是否一致", "类型和本体约束是否满足", "是否存在反证",
        ],
        "output_contract": {
            "verdict": sorted(VALID_VERDICTS), "source_support": sorted(VALID_SUPPORT),
            "schema_status": sorted(VALID_SCHEMA),
        },
    }


def _parse_content(content: Any) -> Dict[str, Any]:
    if isinstance(content, Mapping):
        return dict(content)
    if isinstance(content, str):
        value = json.loads(content)
        if isinstance(value, Mapping):
            return dict(value)
    raise ValueError("validator response must be a JSON object")


def _judge_validator_once(voter: Mapping[str, Any], proposal: Mapping[str, Any]) -> Dict[str, Any]:
    client = voter["client"]
    voter_id = str(voter["id"])
    base = {
        "audit_id": proposal["audit_id"], "round_id": proposal["round_id"],
        "proposal_digest": proposal["proposal_digest"], "voter_id": voter_id,
        "model_id": str(voter.get("model_id") or getattr(client, "model_id", "")),
        "model_family": str(voter.get("model_family") or getattr(client, "model_family", "")),
    }
    started = time.monotonic()
    try:
        response = client.complete_json(validator_payload(proposal), system_prompt=(
            "你是独立的三元组审核者。只能引用给定 evidence_id；证据不足必须 uncertain。"
            "不要输出隐藏思维链，只输出结构化检查摘要。"
        ))
        parsed = _parse_content(response.content)
    except TimeoutError as exc:
        return {**base, "response_status": "timeout", "verdict": None, "error": str(exc),
                "latency_ms": int((time.monotonic() - started) * 1000)}
    except json.JSONDecodeError as exc:
        return {**base, "response_status": "invalid_json", "verdict": None, "error": str(exc),
                "latency_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:
        return {**base, "response_status": "api_error", "verdict": None, "error": str(exc),
                "latency_ms": int((time.monotonic() - started) * 1000)}

    verdict = parsed.get("verdict")
    if verdict not in VALID_VERDICTS or parsed.get("source_support") not in VALID_SUPPORT or parsed.get("schema_status") not in VALID_SCHEMA:
        return {**base, "response_status": "invalid_contract", "verdict": None,
                "error": "invalid verdict/source_support/schema_status"}
    parsed.update(base)
    parsed["response_status"] = "ok"
    parsed.setdefault("reason_codes", [])
    parsed.setdefault("checks", [])
    parsed.setdefault("supporting_evidence_ids", [])
    parsed.setdefault("counter_evidence_ids", [])
    parsed["latency_ms"] = getattr(response, "latency_ms", None)
    parsed["input_tokens"] = getattr(response, "input_tokens", None)
    parsed["output_tokens"] = getattr(response, "output_tokens", None)
    if verdict == "valid" and not parsed["supporting_evidence_ids"]:
        parsed.update({"response_status": "invalid_contract", "verdict": None})
        parsed["reason_codes"].append("VALID_WITHOUT_SUPPORT")
    return verify_vote_references(parsed, proposal.get("evidence_bundle", {}).get("items", []))


def judge_validator(
    voter: Mapping[str, Any], proposal: Mapping[str, Any], *, max_retries: int = 0,
) -> Dict[str, Any]:
    attempts = []
    final: Dict[str, Any] = {}
    retryable = {"timeout", "api_error", "invalid_json"}
    for attempt_no in range(1, max(0, int(max_retries)) + 2):
        final = _judge_validator_once(voter, proposal)
        attempts.append({**final, "attempt_no": attempt_no})
        if final.get("response_status") not in retryable:
            break
    return {**final, "attempt_no": len(attempts), "attempts": attempts}


def judge_independently(
    voters: Iterable[Mapping[str, Any]], proposal: Mapping[str, Any], *, max_workers: int = 4,
    max_retries: int = 0,
) -> List[Dict[str, Any]]:
    voters = list(voters)
    if not voters:
        return []
    results: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(voters)))) as pool:
        futures = {
            pool.submit(judge_validator, voter, proposal, max_retries=max_retries): str(voter["id"])
            for voter in voters
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return [results[str(voter["id"])] for voter in voters]
