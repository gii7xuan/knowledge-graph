"""Same model services, independent ontology contract, namespace and ticket."""
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4
from review_agent.contracts import sha256_json
from review_agent.planner import bounded_json
from review_agent.validators import _parse_content
from .contracts import RESPONSE_SCHEMA, VALIDATOR_PROMPT, validate_response, vote_binding


def response_errors(value, proposal):
    errors = validate_response(value)
    if errors: return errors
    allowed = {e["evidence_id"] for e in proposal["evidence"]} | {r["record_id"] for r in proposal["check_records"].values()}
    cited = {*value["supporting_evidence_ids"], *value["counter_evidence_ids"], *(e for c in value["checks"] for e in c["evidence_ids"])}
    if cited - allowed: errors.append("reference outside frozen ontology proposal")
    if set(value["counter_evidence_ids"]) - {e["evidence_id"] for e in proposal["evidence"]}:
        errors.append("material counterevidence must reference actual source; cite deterministic records in checks")
    if value["decision"] == "approve":
        source_ids = {e["evidence_id"] for e in proposal["evidence"]}
        if not set(value["supporting_evidence_ids"]) & source_ids: errors.append("modeling approval needs actual source references")
        checked_ids = {eid for c in value["checks"] for eid in c["evidence_ids"]}
        if set(proposal.get("prior_counter_evidence_ids", [])) - checked_ids: errors.append("previous material counterevidence must be explicitly rechecked")
        for c in value["checks"]:
            if c["check"] in {"necessity", "definition", "hierarchy_direction", "constraint_strength"} and c["status"] == "pass" and not set(c["evidence_ids"]) & source_ids:
                errors.append("semantic check requires source/modeling evidence")
        general_constraints = any(set(op.get('changes',{})) & {'parent','parents','domain','range','datatype','datatypes'} for op in proposal['operations'])
        if general_constraints:
            check = next(c for c in value['checks'] if c['check']=='constraint_strength')
            if (check['status']!='pass' or check.get('evidence_scope') not in {'definition','classification','scope_rule','mixed'}
                    or not set(check['evidence_ids']) & source_ids):
                errors.append('general constraints require cited definition/classification/scope evidence; instance-only support cannot approve')
    return errors


def judge(voter, proposal, repository, *, retries, timeout):
    binding = vote_binding(voter, proposal)
    client = voter["client"]
    semantic = {k: v for k, v in proposal.items() if k not in {"audit_id", "round_id", "proposal_digest"}}
    cache_inputs = {k: v for k, v in semantic.items() if k not in {"prior_counter_evidence_ids", "human_review_context"}}
    key = sha256_json(["ontology_validation_single_flight_v1", cache_inputs, binding["model_identity"],
                      binding["model_id"], binding["model_family"], str(getattr(client, "base_url", "")),
                      sha256_json(getattr(client, "api_key", "")), VALIDATOR_PROMPT, RESPONSE_SCHEMA])
    owner = uuid4().hex
    cached = repository.claim_validation_request(key, owner)
    if cached["status"] != "claimed":
        original = cached.get("vote") or {}
        if cached["status"] == "completed" and not response_errors({k: original[k] for k in RESPONSE_SCHEMA["required"] if k in original}, proposal):
            return {**copy.deepcopy(original), **binding, "cached": True, "actual_calls": 0, "attempts": [], "input_tokens": 0, "output_tokens": 0,
                    "cache_source": {k: original.get(k) for k in ("audit_id", "round_id", "proposal_digest", "voter_id")}}
        return {**binding, "response_status": "api_error", "actual_calls": 0, "attempts": [], "error": "same frozen ontology request is in flight or outcome unknown", "cached": True}
    attempts, feedback = [], None
    payload = {**semantic, "proposal_digest": proposal["proposal_digest"], "output_contract": RESPONSE_SCHEMA,
               "allowed_evidence_ids": [e["evidence_id"] for e in proposal["evidence"]]}
    for number in range(retries + 1):
        response, error = bounded_json(client, {**payload, **({"correction_feedback": feedback} if feedback else {})}, timeout,
                                      system_prompt=VALIDATOR_PROMPT, max_tokens=4096)
        vote = {**binding, "response_status": "api_error", "attempt_no": number + 1}
        if response is None:
            from review_agent.execution import api_error_fields
            timed_out = isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower()
            vote.update(response_status="timeout" if timed_out else "api_error", error=str(error), **api_error_fields(error))
            if timed_out: vote.update(retryable=False, outcome_unknown=True)
        else:
            vote.update(input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                        returned_model_id=response.returned_model_id, response_id=response.response_id)
            try:
                raw = _parse_content(response.content)
                errors = response_errors(raw, proposal)
                if errors: vote.update(response_status="invalid_contract", error="; ".join(errors), raw_response=raw)
                else: vote.update(raw, response_status="ok")
            except (ValueError, TypeError, KeyError) as exc:
                vote.update(response_status="invalid_contract", error=str(exc), raw_response=response.content)
        attempts.append(copy.deepcopy(vote))
        if vote["response_status"] == "ok" or vote.get("retryable") is False or vote.get("pause_job"): break
        feedback = {"error": vote.get("error"), "instruction": "Only repair contract/references/service error for this same immutable proposal."}
    result = {**vote, "attempts": attempts, "actual_calls": len(attempts), "request_key": key}
    repository.finish_validation_request(key, owner, result)
    return result


def judge_independently(voters, proposal, repository, *, retries=0, timeout=45, on_vote=None):
    results = {}
    pool = ThreadPoolExecutor(max_workers=max(1, len(voters)))
    try:
        futures = {pool.submit(judge, voter, proposal, repository, retries=retries, timeout=timeout): voter for voter in voters}
        for future in as_completed(futures):
            voter = futures[future]
            try: vote = future.result()
            except Exception as exc: vote = {**vote_binding(voter, proposal), "response_status": "api_error", "error": str(exc), "actual_calls": retries + 1, "attempts": []}
            results[voter["id"]] = vote
            if on_vote: on_vote(vote)
    finally: pool.shutdown(wait=False, cancel_futures=True)
    return [results[v["id"]] for v in voters]


def consensus(votes, proposal):
    policy = proposal["committee_policy"]
    members = {m["id"]: m for m in proposal["committee_members"]}
    seen_voters, seen_models, families = set(), set(), {"approve": set(), "reject": set()}
    counts = dict.fromkeys(("approve", "revise", "reject", "uncertain", "error"), 0)
    eligible, counter = [], set()
    for vote in votes:
        voter = members.get(vote.get("voter_id"))
        if not voter or voter["id"] in seen_voters: continue
        binding = vote_binding(voter, proposal)
        seen_voters.add(voter["id"])
        if any(vote.get(k) != v for k, v in binding.items()) or vote.get("response_status") != "ok": counts["error"] += 1; continue
        if response_errors({k: vote[k] for k in RESPONSE_SCHEMA["required"] if k in vote}, proposal): counts["error"] += 1; continue
        identities = {str(vote.get(k) or "").strip().casefold() for k in ("model_identity", "returned_model_id")} - {""}
        if not identities or identities & seen_models: counts["error"] += 1; continue
        seen_models.update(identities)
        eligible.append(voter["id"])
        counts[vote["decision"]] += 1
        if vote["decision"] in families: families[vote["decision"]].add(vote["model_family"])
        counter.update(vote["counter_evidence_ids"])
    checks = proposal["check_records"]
    gates = all(checks.get(k, {}).get("status") == "pass" for k in ("structure", "impact", "competency"))
    gates = gates and checks.get("existing_schema", {}).get("status") == "pass"
    if proposal.get("reasoner_required"): gates = gates and checks.get("reasoner", {}).get("status") == "pass"
    approved = (counts["approve"] >= policy["required_votes"] and len(families["approve"]) >= policy["minimum_distinct_families"] and not counter and gates)
    rejected = counts["reject"] >= policy["required_votes"] and len(families["reject"]) >= policy["minimum_distinct_families"]
    return {"result": "approve" if approved else "reject" if rejected else "unresolved", "counts": counts,
            "committee_size": policy["committee_size"], "required_votes": policy["required_votes"],
            "missing": policy["committee_size"] - len(seen_voters), "eligible_voter_ids": eligible,
            "counter_evidence_ids": sorted(counter), "deterministic_checks_pass": gates}
