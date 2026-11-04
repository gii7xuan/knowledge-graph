"""Independent validator invocation and response contract checks."""

from __future__ import annotations

import json
import copy
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
        "source_chunk_id", "section_id", "ontology_release_id", "profile_hash", "contract_version", "source_snapshot_id", "source_index_hash",
    )}
    safe_candidate["triple"] = {key: triple.get(key) for key in (
        "head", "head_type", "head_entity_id", "surface_relation", "relation_id", "relation_label",
        "tail", "tail_type", "tail_kind", "tail_entity_id", "datatype", "value", "unit", "language", "mapping_status",
    )}
    safe_candidate["triple"]["qualifiers"] = {key: (triple.get("qualifiers") or {}).get(key)
                                                for key in ("polarity", "condition", "time_scope", "applicability", "modality", "quantifier")}
    safe_candidate["triple"]["qualifier_status"] = dict(triple.get("qualifier_status") or {})
    evidence = proposal.get("evidence_bundle", {}).get("items", [])
    from .validator_contracts import contract_for
    contract=contract_for(proposal.get('response_contract_version'))
    return {
        "task_type": "triple_review", "task_id": candidate.get("candidate_id"), "object_revision": candidate.get("revision"),
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
        "response_contract_version": contract.CONTRACT_VERSION,
        "output_contract": contract.RESPONSE_SCHEMA,
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
        "task_type": "triple_review", "task_id": (proposal.get("candidate") or {}).get("candidate_id"),
        "object_revision": (proposal.get("candidate") or {}).get("revision"),
        "source_snapshot_hash": (proposal.get("candidate") or {}).get("source_snapshot_id"),
        "audit_id": proposal["audit_id"], "round_id": proposal["round_id"],
        "proposal_digest": proposal["proposal_digest"], "voter_id": str(voter["id"]),
        "model_id": str(voter.get("model_id") or getattr(client, "model_id", "")),
        "model_family": str(voter.get("model_family") or getattr(client, "model_family", "")),
        "model_identity": str(voter.get("model_identity") or getattr(client, "canonical_model_id", None)
                              or voter.get("model_id") or getattr(client, "model_id", "")).strip().casefold(),
        **{key: proposal.get(key) for key in ("source_index_hash", "document_version", "ontology_version",
                                            "ontology_release_id", "profile_hash", "committee_version", "committee_policy_hash", "prompt_version")},
        "evidence_hash": (proposal.get("evidence_bundle") or {}).get("evidence_hash"),
        "response_contract_version": proposal.get('response_contract_version',CONTRACT_VERSION),
    }


def model_request_count(vote):
    count = vote.get("actual_calls")
    return max(0, count) if type(count) is int else len(vote.get("attempts") or [vote])


def _judge_validator_once(voter: Mapping[str, Any], proposal: Mapping[str, Any],
                          feedback: Any = None) -> Dict[str, Any]:
    client = voter["client"]
    base = vote_binding(voter, proposal)
    started = time.monotonic()
    response=None
    try:
        from .validator_contracts import contract_for, prompt_for
        contract=contract_for(proposal.get('response_contract_version'))
        payload = validator_payload(proposal)
        if feedback:
            payload["correction_feedback"] = feedback
        response = client.complete_json(payload, system_prompt=prompt_for(proposal),
            max_tokens=voter.get('max_output_tokens',4096),timeout_seconds=voter.get('http_timeout_seconds'))
        parsed = _parse_content(response.content)
    except TimeoutError as exc:
        return {**base, "response_status": "timeout", "verdict": None, "error": str(exc),
                "retryable": False, "outcome_unknown": True,
                "latency_ms": int((time.monotonic() - started) * 1000)}
    except json.JSONDecodeError as exc:
        return {**base, "response_status": "invalid_json", "verdict": None, "error": str(exc),
                'raw_response':getattr(response,'content',None),'response_id':getattr(response,'response_id',None),
                'returned_model_id':getattr(response,'returned_model_id',None),
                'input_tokens':getattr(response,'input_tokens',None),'output_tokens':getattr(response,'output_tokens',None),
                'finish_reason':getattr(response,'finish_reason',None),'reasoning_tokens':getattr(response,'reasoning_tokens',None),
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

    errors = contract.validate_response(parsed)
    if errors:
        return {**base, "response_status": "invalid_contract", "verdict": None,
                "error": "; ".join(errors), "validation_errors": errors,
                "raw_response": response.content, "latency_ms": getattr(response, "latency_ms", None),
                'response_id':getattr(response,'response_id',None),'returned_model_id':getattr(response,'returned_model_id',None),
                "input_tokens": getattr(response, "input_tokens", None),
                "output_tokens": getattr(response, "output_tokens", None)}
    if hasattr(contract,'normalize'):
        raw_assessment=parsed['schema_assessment']
        parsed=contract.normalize(parsed)
        parsed['schema_assessment']=raw_assessment
    parsed.update(base)
    parsed["response_status"] = "ok"
    parsed.setdefault("reason_codes", [])
    parsed.setdefault("checks", [])
    parsed.setdefault("supporting_evidence_ids", [])
    parsed.setdefault("counter_evidence_ids", [])
    parsed["latency_ms"] = getattr(response, "latency_ms", None)
    parsed["input_tokens"] = getattr(response, "input_tokens", None)
    parsed["output_tokens"] = getattr(response, "output_tokens", None)
    parsed["returned_model_id"] = getattr(response, "returned_model_id", None)
    parsed["response_id"] = getattr(response, "response_id", None)
    parsed['finish_reason']=getattr(response,'finish_reason',None)
    parsed['reasoning_tokens']=getattr(response,'reasoning_tokens',None)
    parsed["model_version_verification"] = "reported" if parsed["returned_model_id"] else "unavailable"
    if proposal.get("ontology_check", {}).get("status") == "fail" and parsed["schema_status"] != "fail":
        parsed["ontology_disagreement"] = True
        parsed["reason_codes"].append("ONTOLOGY_DISAGREEMENT")
    return verify_vote_references(parsed, proposal.get("evidence_bundle", {}).get("items", []))


def judge_validator(
    voter: Mapping[str, Any], proposal: Mapping[str, Any], *, max_retries: int = 0,
    per_call_timeout_seconds: float = 45.0,
    repository=None,
    deadline_epoch=None, _request_owner=None, _request_key=None,
    _initial_feedback=None,
) -> Dict[str, Any]:
    if repository is not None and _request_owner is None:
        from .request_cache import cached_validation
        return cached_validation(repository, voter, proposal,
            lambda owner,key: judge_validator(voter, proposal, max_retries=max_retries,
                per_call_timeout_seconds=per_call_timeout_seconds,repository=repository,
                deadline_epoch=deadline_epoch,_request_owner=owner,_request_key=key,
                _initial_feedback=_initial_feedback),
            min(per_call_timeout_seconds * (max(0, int(max_retries)) + 1),
                max(0.,deadline_epoch-time.time()) if deadline_epoch else float('inf')))
    attempts = []
    final: Dict[str, Any] = {**vote_binding(voter,proposal),'response_status':'api_error','error_code':'DEADLINE_EXHAUSTED','actual_calls':0}
    retryable = {"timeout", "api_error", "invalid_json", "invalid_contract", "invalid_reference"}
    feedback = copy.deepcopy(_initial_feedback)
    for attempt_no in range(1, max(0, int(max_retries)) + 2):
        timeout=min(float(voter.get('request_timeout_seconds',per_call_timeout_seconds)),
                    max(0.,deadline_epoch-time.time()) if deadline_epoch else float('inf'))
        if timeout<=0: break
        if deadline_epoch:
            voter={**voter,'http_timeout_seconds':min(float(voter.get('http_timeout_seconds',getattr(voter['client'],'timeout_seconds',timeout))),
                                                    max(.001,deadline_epoch-time.time()))}
        if repository is not None:
            from .request_execution import durable_attempt
            final=durable_attempt(repository,_request_key,_request_owner,vote_binding(voter,proposal),voter,
                proposal,feedback,timeout,lambda:_judge_validator_once(voter,proposal,feedback))
        else:
            final = _bounded_attempt(voter, proposal, feedback, timeout)
        attempts.append({**final, "attempt_no": attempt_no})
        if final.get("response_status") not in retryable or final.get("retryable") is False:
            break
        feedback = {"response_status": final.get("response_status"), "error": final.get("error"),
                    'raw_response_ref':final.get('attempt_id'),
                    'field_constraints':{'ontology_state':'schema_status must equal checks.ontology.status in v3; v4 uses schema_assessment only',
                        'schema_failure':'fail requires CONSTRAINT_VIOLATION and cannot use not_covered'},
                    "validation_errors": final.get("validation_errors", []),
                    "invalid_evidence_ids": final.get("invalid_evidence_ids", []),
                    "instruction": "修复上述错误，依据同一 evidence 目录重新输出完整 JSON；不得猜测或替换 ID。"}
    return {**final, "attempt_no": len(attempts), "attempts": attempts,
            "actual_calls":sum(0 if a.get('actual_calls')==0 else 1 for a in attempts)}


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
            output.put({**vote_binding(voter, proposal),
                        "response_status": "api_error", "verdict": None, "error": str(exc)})

    threading.Thread(target=invoke, daemon=True, name="bounded-model-request").start()
    while True:
        remaining = timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            client = voter["client"]
            return {**vote_binding(voter, proposal),
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
    deadline_epoch=None,
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
                        repository=repository,deadline_epoch=deadline_epoch): str(voter["id"])
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
