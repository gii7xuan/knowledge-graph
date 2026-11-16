"""One diagnosed full semantic reassessment, distinct from field patches and explanations."""
from copy import deepcopy
import json
from .contracts import sha256_json,canonical_json

VERSION='candidate_semantic_recheck_v1'


def binding(voter,proposal):
    return {'task_type':VERSION,'task_id':proposal['task_id'],'object_revision':proposal['candidate_revision'],
        'proposal_digest':proposal['proposal_digest'],'original_response_hash':proposal['original_response_hash'],
        'voter_id':voter['id'],'model_id':voter['model_id'],'model_family':voter['model_family'],
        'model_identity':str(voter.get('model_identity') or voter['model_id']).casefold()}


def judge_once(voter,proposal,feedback=None):
    from .validators import _judge_validator_once
    if sha256_json({k:v for k,v in proposal.items() if k!='proposal_digest'})!=proposal['proposal_digest']:
        return {**binding(voter,proposal),'response_status':'api_error','not_sent':True,'actual_calls':0,'retryable':False}
    vote=_judge_validator_once(voter,proposal['base_proposal'],{
        'kind':'independent_semantic_reassessment','diagnosis':proposal['diagnosis'],
        'original_response_hash':proposal['original_response_hash'],
        'instruction':'Independently reassess the complete exact statement against source. A failed or unresolved entity/meaning/direction/qualifier check needs its actual explicit finding and citations. Preserve failed/unknown when warranted. This is a new semantic review, not an instruction to change a check to pass. Explanation text is not a vote.'})
    return {**binding(voter,proposal),'response_status':vote['response_status'],'review_vote':vote,
        **{k:deepcopy(vote[k]) for k in ('input_tokens','output_tokens','latency_ms','raw_response','returned_model_id',
            'response_id','finish_reason','reasoning_tokens','error','error_code','outcome_unknown','http_status','retryable') if k in vote}}


def valid(result,proposal):
    from .review_observability import assess_vote
    vote=result.get('review_vote') or {}
    return bool(result.get('response_status')=='ok' and vote.get('voter_id')==proposal['voter_id'] and
                assess_vote(vote,proposal['base_proposal'])['whole_vote_eligible'])


def candidates(state):
    validation=state.current_validation() or {}
    return [v['voter_id'] for v in validation.get('votes',[]) if v.get('response_status')=='invalid_contract'
            and not v.get('outcome_unknown') and v.get('raw_response')
            and diagnosed(v) and v['voter_id'] not in state.context.get('semantic_recheck_results',{})]


def diagnosed(vote):
    from .validators import _parse_content
    try:raw=_parse_content(vote.get('raw_response'))
    except (ValueError,TypeError):return False
    if not isinstance(raw,dict) or not isinstance(raw.get('dimensions'),dict):return False
    checks=('entity_reference','relation_semantics','relation_direction','qualifiers')
    if not all(isinstance(raw['dimensions'].get(c),dict) for c in checks):return False
    findings=raw.get('expression_findings')
    if not isinstance(findings,list):return False
    if (vote.get('semantic_clarification') or {}).get('status')=='explanation_recorded':return True
    return any(raw['dimensions'][c].get('status') in {'fail','unknown'} and
               not any(isinstance(f,dict) and f.get('check')==c for f in findings) for c in checks)


def review(runtime,voter_id):
    from .planner_contracts import ActionError
    from .request_execution import judge_typed
    from .validators import _parse_content
    from .validator_contracts import contract_for
    state=runtime.state;validation=state.current_validation()
    if not validation or voter_id not in candidates(state):raise ActionError('select a completed invalid original response; unknown requests are never semantically resubmitted')
    base=next(p for p in runtime.repository.get_audit(state.candidate['candidate_id'],state.audit_id)['rounds']
              if p['proposal_digest']==validation['proposal_digest'])
    original=next(v for v in validation['votes'] if v['voter_id']==voter_id)
    raw=_parse_content(original['raw_response'])
    errors=contract_for(base['response_contract_version']).validate_response(raw)
    if not errors:raise ActionError('a specific semantic-contract diagnosis is required')
    proposal={'version':VERSION,'audit_id':state.audit_id,'candidate_id':state.candidate['candidate_id'],
        'candidate_revision':state.candidate['revision'],'base_proposal':deepcopy(base),'voter_id':voter_id,
        'original_response_hash':sha256_json(raw),'original_attempt_id':original.get('attempt_id'),
        'diagnosis':errors,'review_limit':1}
    proposal['task_id']='semantic_'+sha256_json([base['proposal_digest'],voter_id,proposal['original_response_hash']])[:32]
    proposal['proposal_digest']=sha256_json(proposal)
    repository=runtime.repository
    with repository.transaction(immediate=True) as c:
        c.execute('CREATE TABLE IF NOT EXISTS semantic_recheck_tasks(task_id TEXT PRIMARY KEY,audit_id TEXT NOT NULL,proposal_json TEXT NOT NULL,result_json TEXT NOT NULL)')
        prior=c.execute('SELECT result_json FROM semantic_recheck_tasks WHERE task_id=?',(proposal['task_id'],)).fetchone()
    if prior:return json.loads(prior[0]) or {'status':'technical_recovery_pending','automatic_resend':False}
    if not runtime.budget.reserve_calls(1):return {'status':'budget_exhausted','model_calls':0}
    with repository.transaction(immediate=True) as c:
        inserted=c.execute('INSERT OR IGNORE INTO semantic_recheck_tasks VALUES(?,?,?,?)',(proposal['task_id'],state.audit_id,canonical_json(proposal),'{}')).rowcount
    if not inserted:
        runtime.budget.refund_calls(1)
        return {'status':'technical_recovery_pending','automatic_resend':False}
    voter=next(v for v in runtime.orchestrator.validators if v['id']==voter_id)
    voter={**voter,'case_ids':list(getattr(runtime.budget,'case_ids',[]))}
    result=judge_typed(voter,proposal,repository,task_type=VERSION,binding=binding(voter,proposal),
        invoke=lambda feedback:judge_once(voter,proposal),validate_cached=lambda r:[] if valid(r,proposal) else ['invalid semantic recheck'],
        prompt_hash=sha256_json([VERSION,base.get('prompt_hash')]),retries=0,
        timeout=min(120,runtime.budget.remaining_seconds()),deadline_epoch=runtime.budget.deadline_epoch)
    if result.get('actual_calls')==0:runtime.budget.refund_calls(1)
    for attempt in result.get('attempts') or [result]:runtime.budget.tokens(attempt)
    result={**result,'review_proposal':proposal,'status':'completed' if valid(result,proposal) else 'review_incomplete',
            'original_vote_changed':False,'automatic_approval':False}
    with repository.transaction(immediate=True) as c:c.execute('UPDATE semantic_recheck_tasks SET result_json=? WHERE task_id=?',(canonical_json(result),proposal['task_id']))
    state.context.setdefault('semantic_recheck_results',{})[voter_id]=result
    runtime.refresh_resolution()
    return result
