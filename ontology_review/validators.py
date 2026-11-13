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


def judge_once(voter, proposal, feedback=None):
    from review_agent.execution import api_error_fields
    from review_agent.llm_clients import response_metadata
    from review_agent.transport_diagnostics import safe_error_text
    from .modeling_context import enabled, verify, suggestion_diagnostics
    vote=vote_binding(voter,proposal)
    from .review_payload import validator_payload
    response = None
    provider_called = False
    try:
        verify(proposal)
        semantic=validator_payload(proposal,delivery_version=voter.get('ontology_delivery_version'))
        payload={**semantic,'output_contract':RESPONSE_SCHEMA,
            'allowed_evidence_ids':[e['evidence_id'] for e in proposal['evidence']],
            **({'correction_feedback':feedback} if feedback else {})}
        prompt=prompt_for(proposal)
        provider_called = True
        response=voter['client'].complete_json(payload,system_prompt=prompt,
            max_tokens=int(voter.get('max_output_tokens',4096)),timeout_seconds=voter.get('http_timeout_seconds'))
        vote.update(response_metadata(response))
        raw=_parse_content(response.content)
        errors=response_errors(raw,proposal)
        vote.update(raw if not errors else {'error':'; '.join(errors),'raw_response':raw},
            response_status='invalid_contract' if errors else 'ok',input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,returned_model_id=response.returned_model_id,
            response_id=response.response_id,latency_ms=response.latency_ms,
            finish_reason=getattr(response,'finish_reason',None),reasoning_tokens=getattr(response,'reasoning_tokens',None))
        if enabled(proposal):
            vote['suggestion_diagnostics']=suggestion_diagnostics(raw,proposal)
    except Exception as exc:
        if not provider_called:
            vote.update(response_status='api_error',error=safe_error_text(exc,voter['client']),
                error_code='ONTOLOGY_MODELING_CONTEXT_PREFLIGHT_FAILED' if enabled(proposal) else 'ONTOLOGY_PAYLOAD_PREFLIGHT_FAILED',
                not_sent=True,actual_calls=0,retryable=False)
            return vote
        timed_out=isinstance(exc,TimeoutError) or 'timeout' in type(exc).__name__.lower()
        vote.update(response_status='timeout' if timed_out else 'api_error',
                    error=safe_error_text(exc,voter['client']),**api_error_fields(exc))
        if timed_out: vote.update(retryable=False,outcome_unknown=True)
    return vote


def prompt_for(proposal):
    from .contracts import ROOT, PROMPT_VERSION
    version=proposal.get('versions',{}).get('prompt_version')
    paths={'ontology_extension_validator_v1':'ontology_extension_validator_v1.txt',
           'ontology_extension_validator_v2':'ontology_extension_validator_v2_legacy.txt',
           'ontology_extension_validator_v2_constraint_scope_v1':'ontology_extension_validator_v2.txt',
           PROMPT_VERSION:'ontology_extension_validator_v3.txt'}
    if version is None:
        prompt=(ROOT/'prompts/ontology_extension_validator_v2.txt').read_text(encoding='utf-8')
    elif version in paths:
        prompt=(ROOT/'prompts'/paths[version]).read_text(encoding='utf-8')
    else:
        raise ValueError('unsupported frozen ontology prompt version')
    expected=proposal.get('versions',{}).get('prompt_hash')
    if expected and sha256_json(prompt)!=expected:
        raise ValueError('frozen ontology prompt hash mismatch')
    return prompt


def judge(voter, proposal, repository, *, retries, timeout,deadline_epoch=None):
    from review_agent.request_execution import judge_typed
    return judge_typed(voter,proposal,repository,task_type='ontology_review',binding=vote_binding(voter,proposal),
        invoke=lambda feedback:judge_once(voter,proposal,feedback),
        validate_cached=lambda vote:response_errors({k:vote[k] for k in RESPONSE_SCHEMA['required'] if k in vote},proposal),
        prompt_hash=sha256_json([prompt_for(proposal),RESPONSE_SCHEMA]),retries=retries,timeout=timeout,deadline_epoch=deadline_epoch)


def judge_independently(voters, proposal, repository, *, retries=0, timeout=45, on_vote=None,deadline_epoch=None):
    results = {}
    pool = ThreadPoolExecutor(max_workers=max(1, len(voters)))
    try:
        futures = {pool.submit(judge, voter, proposal, repository, retries=retries, timeout=timeout,deadline_epoch=deadline_epoch): voter for voter in voters}
        for future in as_completed(futures):
            voter = futures[future]
            try: vote = future.result()
            except Exception as exc: vote = {**vote_binding(voter, proposal), "response_status": "api_error", "error": str(exc), "actual_calls": retries + 1, "attempts": []}
            results[voter["id"]] = vote
            if on_vote: on_vote(vote)
            if vote.get('pause_job'):
                from review_agent.execution import AuditPaused
                raise AuditPaused(vote)
    finally: pool.shutdown(wait=False, cancel_futures=True)
    return [results[v["id"]] for v in voters]


def competency_pass(proposal,semantic_competency=None):
    check=proposal['check_records'].get('competency',{})
    if check.get('status')=='pass':return True
    proof=semantic_competency or {}
    if (check.get('status')!='unknown' or proof.get('status')!='pass' or
            proof.get('parent_proposal_digest')!=proposal['proposal_digest'] or not proof.get('request_hash')):return False
    questions=set(proof.get('question_ids',[]))
    return bool(check.get('checks')) and all(item.get('status')=='pass' or
        item.get('status')=='unknown' and item.get('id') in questions for item in check['checks'])


def consensus(votes, proposal, *, semantic_competency=None):
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
    gates = all(checks.get(k, {}).get("status") == "pass" for k in ("structure", "impact")) and competency_pass(proposal,semantic_competency)
    gates = gates and checks.get("existing_schema", {}).get("status") == "pass"
    if proposal.get("reasoner_required"): gates = gates and checks.get("reasoner", {}).get("status") == "pass"
    approved = (counts["approve"] >= policy["required_votes"] and len(families["approve"]) >= policy["minimum_distinct_families"] and not counter and gates)
    rejected = counts["reject"] >= policy["required_votes"] and len(families["reject"]) >= policy["minimum_distinct_families"]
    return {"result": "approve" if approved else "reject" if rejected else "unresolved", "counts": counts,
            "committee_size": policy["committee_size"], "required_votes": policy["required_votes"],
            "missing": policy["committee_size"] - len(seen_voters), "eligible_voter_ids": eligible,
            "counter_evidence_ids": sorted(counter), "deterministic_checks_pass": gates}
