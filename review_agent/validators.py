"""Independent validator invocation and response contract checks."""

from __future__ import annotations

import json
import re
import time
import queue
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Mapping

from evidence.verification import verify_vote_references
from .execution import AuditPaused, api_error_fields
from .response_contract import (CONTRACT_VERSION, RESPONSE_SCHEMA, SYSTEM_PROMPT,
                                REASON_CODES, VALID_VERDICTS, VALID_SUPPORT, VALID_SCHEMA,
                                validate_response)


def validator_payload(proposal: Mapping[str, Any]) -> Dict[str, Any]:
    """Expose only the frozen candidate, checks, and selectable evidence ids."""
    candidate = proposal["candidate"]
    triple = candidate.get("triple") or candidate
    safe_candidate = {key: candidate.get(key) for key in (
        "candidate_id", "revision", "document_id", "document_version", "ontology_version",
        "source_chunk_id", "section_id", "ontology_release_id", "profile_hash", "contract_version",
    )}
    safe_candidate["triple"] = {key: triple.get(key) for key in (
        "head", "head_type", "head_entity_id", "surface_relation", "relation_id", "relation_label",
        "tail", "tail_type", "tail_kind", "tail_entity_id", "datatype", "value", "unit", "language", "mapping_status",
    )}
    safe_candidate["triple"]["qualifiers"] = {key: (triple.get("qualifiers") or {}).get(key)
                                                for key in ("polarity", "condition", "time_scope", "applicability", "modality", "quantifier")}
    safe_candidate["triple"]["qualifier_status"] = dict(triple.get("qualifier_status") or {})
    evidence = proposal.get("evidence_bundle", {}).get("items", [])
    return {
        "proposal_digest": proposal["proposal_digest"],
        "candidate": safe_candidate,
        "ontology_check": proposal.get("ontology_check", {}),
        "evidence": evidence,
        "allowed_evidence_ids": [item["evidence_id"] for item in evidence],
        "history_examples": {"purpose": "审核标准示例，禁止引用其中证据 ID",
                             "cases": proposal.get("review_memory", {})},
        "rubric": [
            "完整主谓宾是否被原文支持", "否定、条件、数量、时间和指代是否保留",
            "关系语义及方向是否一致", "类型和本体约束是否满足", "是否存在反证",
        ],
        "response_contract_version": CONTRACT_VERSION,
        "output_contract": RESPONSE_SCHEMA,
        "reason_codes": list(REASON_CODES),
    }


def _parse_content(content: Any) -> Dict[str, Any]:
    if isinstance(content, Mapping):
        return dict(content)
    if isinstance(content, str):
        fenced = re.fullmatch(r"\s*```(?:json)?\s*\n([\s\S]*?)\n```\s*", content, re.IGNORECASE)
        if fenced:
            content = fenced.group(1)
        value = json.loads(content)
        if isinstance(value, Mapping):
            return dict(value)
    raise ValueError("validator response must be a JSON object")


def vote_binding(voter, proposal):
    client = voter["client"]
    return {
        "audit_id": proposal["audit_id"], "round_id": proposal["round_id"],
        "proposal_digest": proposal["proposal_digest"], "voter_id": str(voter["id"]),
        "model_id": str(voter.get("model_id") or getattr(client, "model_id", "")),
        "model_family": str(voter.get("model_family") or getattr(client, "model_family", "")),
    }


def model_request_count(vote):
    count = vote.get("actual_calls")
    return max(0, count) if type(count) is int else len(vote.get("attempts") or [vote])


def _judge_validator_once(voter: Mapping[str, Any], proposal: Mapping[str, Any],
                          feedback: Any = None) -> Dict[str, Any]:
    client = voter["client"]
    base = vote_binding(voter, proposal)
    started = time.monotonic()
    try:
        payload = validator_payload(proposal)
        if feedback:
            payload["correction_feedback"] = feedback
        response = client.complete_json(payload, system_prompt=SYSTEM_PROMPT)
        parsed = _parse_content(response.content)
    except TimeoutError as exc:
        return {**base, "response_status": "timeout", "verdict": None, "error": str(exc),
                "retryable": False, "outcome_unknown": True,
                "latency_ms": int((time.monotonic() - started) * 1000)}
    except json.JSONDecodeError as exc:
        return {**base, "response_status": "invalid_json", "verdict": None, "error": str(exc),
                "latency_ms": int((time.monotonic() - started) * 1000)}
    except ValueError as exc:
        return {**base, "response_status": "invalid_contract", "verdict": None, "error": str(exc),
                "latency_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:
        status = "timeout" if "timeout" in type(exc).__name__.lower() else "api_error"
        return {**base, "response_status": status, "verdict": None, "error": str(exc),
                **api_error_fields(exc),
                **({"retryable": False, "outcome_unknown": True} if status == "timeout" else {}),
                "latency_ms": int((time.monotonic() - started) * 1000)}

    errors = validate_response(parsed)
    if errors:
        return {**base, "response_status": "invalid_contract", "verdict": None,
                "error": "; ".join(errors), "validation_errors": errors,
                "raw_response": response.content, "latency_ms": getattr(response, "latency_ms", None),
                "input_tokens": getattr(response, "input_tokens", None),
                "output_tokens": getattr(response, "output_tokens", None)}
    parsed.update(base)
    parsed["response_status"] = "ok"
    parsed.setdefault("reason_codes", [])
    parsed.setdefault("checks", [])
    parsed.setdefault("supporting_evidence_ids", [])
    parsed.setdefault("counter_evidence_ids", [])
    parsed["latency_ms"] = getattr(response, "latency_ms", None)
    parsed["input_tokens"] = getattr(response, "input_tokens", None)
    parsed["output_tokens"] = getattr(response, "output_tokens", None)
    if proposal.get("ontology_check", {}).get("status") == "fail" and parsed["schema_status"] != "fail":
        parsed["ontology_disagreement"] = True
        parsed["reason_codes"].append("ONTOLOGY_DISAGREEMENT")
    return verify_vote_references(parsed, proposal.get("evidence_bundle", {}).get("items", []))


def judge_validator(
    voter: Mapping[str, Any], proposal: Mapping[str, Any], *, max_retries: int = 0,
    per_call_timeout_seconds: float = 45.0,
    repository=None,
) -> Dict[str, Any]:
    if repository is not None:
        from .request_cache import cached_validation
        return cached_validation(repository, voter, proposal,
            lambda: judge_validator(voter, proposal, max_retries=max_retries,
                                    per_call_timeout_seconds=per_call_timeout_seconds),
            per_call_timeout_seconds * (max(0, int(max_retries)) + 1))
    attempts = []
    final: Dict[str, Any] = {}
    retryable = {"timeout", "api_error", "invalid_json", "invalid_contract", "invalid_reference"}
    feedback = None
    for attempt_no in range(1, max(0, int(max_retries)) + 2):
        final = _bounded_attempt(voter, proposal, feedback, per_call_timeout_seconds)
        attempts.append({**final, "attempt_no": attempt_no})
        if final.get("response_status") not in retryable or final.get("retryable") is False:
            break
        feedback = {"response_status": final.get("response_status"), "error": final.get("error"),
                    "validation_errors": final.get("validation_errors", []),
                    "invalid_evidence_ids": final.get("invalid_evidence_ids", []),
                    "instruction": "修复上述错误，依据同一 evidence 目录重新输出完整 JSON；不得猜测或替换 ID。"}
    return {**final, "attempt_no": len(attempts), "attempts": attempts, "actual_calls": len(attempts)}


def _bounded_attempt(voter, proposal, feedback, timeout_seconds):
    """Drop late HTTP results. The enclosing candidate process owns/cleans sockets."""
    if timeout_seconds <= 0:
        raise ValueError("per-call timeout must be positive")
    output = queue.Queue(maxsize=1)
    started = time.monotonic()

    def invoke():
        try:
            output.put(_judge_validator_once(voter, proposal, feedback))
        except Exception as exc:
            output.put({"audit_id": proposal["audit_id"], "round_id": proposal["round_id"],
                        "proposal_digest": proposal["proposal_digest"], "voter_id": str(voter["id"]),
                        "model_id": str(voter.get("model_id") or ""),
                        "model_family": str(voter.get("model_family") or ""),
                        "response_status": "api_error", "verdict": None, "error": str(exc)})

    threading.Thread(target=invoke, daemon=True, name="bounded-model-request").start()
    while True:
        remaining = timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            client = voter["client"]
            return {"audit_id": proposal["audit_id"], "round_id": proposal["round_id"],
                    "proposal_digest": proposal["proposal_digest"], "voter_id": str(voter["id"]),
                    "model_id": str(voter.get("model_id") or getattr(client, "model_id", "")),
                    "model_family": str(voter.get("model_family") or getattr(client, "model_family", "")),
                    "response_status": "timeout", "verdict": None, "retryable": False, "outcome_unknown": True,
                    "error_code": "HARD_REQUEST_TIMEOUT", "error": "request exceeded hard deadline",
                    "latency_ms": int((time.monotonic() - started) * 1000)}
        try:
            result = output.get(timeout=min(.1, remaining))
            if time.monotonic() - started < timeout_seconds:
                return result
        except queue.Empty:
            pass


def judge_independently(
    voters: Iterable[Mapping[str, Any]], proposal: Mapping[str, Any], *, max_workers: int = 4,
    max_retries: int = 0,
    per_call_timeout_seconds: float = 45.0,
    on_vote=None,
    repository=None,
) -> List[Dict[str, Any]]:
    voters = list(voters)
    if not voters:
        return []
    results: Dict[str, Dict[str, Any]] = {}
    pool = ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(voters))))
    try:
        futures = {
            pool.submit(judge_validator, voter, proposal, max_retries=max_retries,
                        per_call_timeout_seconds=per_call_timeout_seconds,
                        repository=repository): str(voter["id"])
            for voter in voters
        }
        for future in as_completed(futures):
            vote = future.result()
            results[futures[future]] = vote
            if on_vote:
                on_vote(vote)
            if vote.get("pause_job"):
                raise AuditPaused(vote)
    finally:
        # Production candidates run in killable processes. Do not wait here for
        # unrelated sockets after a permanent billing failure has been detected.
        pool.shutdown(wait=False, cancel_futures=True)
    return [results[str(voter["id"])] for voter in voters]
