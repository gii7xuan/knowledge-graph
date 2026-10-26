"""Durable single-flight validation, keyed by frozen inputs rather than audit IDs."""

import copy
import time
from uuid import uuid4

from .contracts import sha256_json
from .response_contract import RESPONSE_SCHEMA, SYSTEM_PROMPT, validate_response
from evidence.verification import verify_vote_references


def validation_request_key(voter, proposal):
    from .validators import validator_payload, vote_binding

    payload = validator_payload(proposal)
    payload.pop("proposal_digest", None)  # Transport binding, never the semantic cache identity.
    client = voter["client"]
    binding = vote_binding(voter, proposal)
    frozen = {k: proposal.get(k) for k in (
        "prompt_version", "prompt_hash", "response_contract_version", "response_contract_hash", "evidence_bundle_version", "rubric_version",
        "committee_version", "committee_policy_hash", "committee_members", "candidate_review_hash", "ontology_check_hash", "evidence_bundle_hash", "memory_snapshot_id", "memory_bundle_hash", "agent_versions")}
    return sha256_json({"version": "validation_single_flight_v2", "role": "validator", "task_type": "triple_review",
        "object_revision": proposal["candidate"]["revision"], "model_identity": binding["model_identity"],
        "namespace": proposal.get("request_cache_namespace", "default"),
        "model_id": binding["model_id"], "model_family": binding["model_family"],
        "client_kind": type(client).__module__ + "." + type(client).__qualname__,
        "base_url": str(getattr(client, "base_url", "")).rstrip("/"),
        "credential_fingerprint": sha256_json(getattr(client, "api_key", "")),
        "generation": {"temperature": 0.0, "response_format": "json_object"},
        "system_prompt": SYSTEM_PROMPT, "frozen": frozen, "payload": payload})


def cached_validation(repository, voter, proposal, invoke, wait_seconds):
    from .validators import vote_binding

    key = validation_request_key(voter, proposal)
    binding = vote_binding(voter, proposal)
    began = time.monotonic()
    owner = uuid4().hex
    entry = repository.claim_validation_request(key, owner)
    if entry["status"] == "claimed":
        # A process killed before this commit leaves 'running' fenced.  We must not
        # infer zero cost or automatically resend a request with an unknown result.
        vote = {**invoke(), "request_key": key}
        repository.finish_validation_request(key, owner, vote)
        return vote
    while entry["status"] == "running" and time.monotonic() - began < wait_seconds:
        time.sleep(min(.05, max(.001, wait_seconds - (time.monotonic() - began))))
        entry = repository.validation_request(key)
    if entry["status"] in {"completed", "failed"}:
        original = entry["vote"]
        vote = copy.deepcopy(original)
        vote.update(binding, request_key=key, cached=True, actual_calls=0, attempt_no=0,
            attempts=[], input_tokens=0, output_tokens=0, latency_ms=0,
            cache_wait_ms=int((time.monotonic() - began) * 1000),
            cache_source={k: original.get(k) for k in ("audit_id", "round_id", "voter_id", "proposal_digest")})
        if entry["status"] == "completed":
            errors = validate_response({k: vote[k] for k in RESPONSE_SCHEMA["properties"] if k in vote})
            if errors:
                return {**binding, "response_status": "invalid_contract", "verdict": None,
                    "error_code": "INVALID_CACHED_VOTE", "validation_errors": errors,
                    "retryable": False, "actual_calls": 0, "attempts": [], "request_key": key,
                    "cached": True, "input_tokens": 0, "output_tokens": 0}
            return verify_vote_references(vote, proposal.get("evidence_bundle", {}).get("items", []))
        return vote
    return {**binding, "response_status": "api_error", "verdict": None,
        "error_code": "DUPLICATE_REQUEST_SUPPRESSED", "retryable": False,
        "error": "same frozen model request is still running or its outcome is unknown; automatic resend suppressed",
        "outcome_unknown": True, "actual_calls": 0, "attempts": [], "request_key": key,
        "cached": True, "input_tokens": 0, "output_tokens": 0}
