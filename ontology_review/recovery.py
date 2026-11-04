"""Operator-authorized recovery of known failed seats on one frozen proposal."""
import copy
import json
import time
from pathlib import Path
from uuid import uuid4
from ontologyBuild.release_service import load_release
from review_agent.case_budget import CaseBudget
from review_agent.contracts import canonical_json
from review_agent.repository import ConflictError,ValidationError
from review_agent.validators import model_request_count
from .evidence import load_sources
from .runtime import OntologyReviewRuntime
from .validators import judge_independently,consensus


def recover_frozen_ontology(repository,release_root,voters,config,audit_id,*,reason,extra_calls,extra_tokens,extra_seconds):
    if not reason.strip() or extra_calls<1 or extra_tokens<1 or extra_seconds<=0:
        raise ValidationError('operator recovery needs a reason and bounded additional budget')
    audit=repository.ontology_audit(audit_id)
    if audit['status']=='running': raise ConflictError('audit is already active')
    task=repository.extension_status(audit['extension_id'])
    if task['status'] in {'published','publishing','rejected','needs_rebase'}: raise ConflictError('task is not recoverable')
    if task.get('review_invalidated_by_human'): raise ConflictError('human-requested changes require a new proposal')
    from .publisher import current_release_id
    if current_release_id(release_root)!=task['base_release_id']: raise ConflictError('base release changed')
    if not audit['proposals']: raise ValidationError('original frozen proposal required')
    proposal=audit['proposals'][-1]
    if task.get('delta',{}).get('delta_hash')!=proposal['delta_hash'] or task['delta_revision']!=proposal['object_revision']:
        raise ConflictError('delta changed')
    roster=[{k:v[k] for k in ('id','model_id','model_family')} for v in voters]
    if roster!=[{k:v[k] for k in ('id','model_id','model_family')} for v in proposal['committee_members']]:
        raise ConflictError('original independent roster required')
    original={v['voter_id']:v for v in audit['votes'] if v['round_id']==proposal['round_id']}
    selected=[];unknown=[]
    for voter in voters:
        old=original.get(voter['id'],{})
        if old.get('response_status')=='ok': continue
        key=old.get('request_key');entry=repository.validation_request(key) if key else None
        if not entry or entry['status'] in {'running','unknown'}:
            unknown.append(voter['id']);continue
        if entry['status'] not in {'failed','completed'}: continue
        history=repository.request_history(key)
        generation=history[-1]['metadata'].get('generation',{}) if history else {}
        frozen={**voter,'max_output_tokens':generation.get('max_output_tokens',voter.get('max_output_tokens',4096)),
            'ontology_delivery_version':generation.get('ontology_delivery_version','ontology_validator_payload_v2')}
        client=voter['client']
        if hasattr(client,'thinking_mode'):
            from review_agent.llm_clients import OpenAICompatibleClient
            frozen['client']=OpenAICompatibleClient(api_key=client.api_key,base_url=client.base_url,model_id=client.model_id,
                model_family=client.model_family,timeout_seconds=client.timeout_seconds,verify_ssl=client.verify_ssl,
                thinking_mode=generation.get('thinking_mode'),stream_responses=getattr(client,'stream_responses',False))
        selected.append(frozen)
    if not selected: return {'status':'waiting_request_reconciliation','unknown_voter_ids':unknown,'model_calls':0}
    if extra_calls<len(selected): raise ValidationError('budget does not cover selected failed seats')
    evidence,documents,candidates,case_ids=load_sources(repository,task)
    for candidate_id in candidates:
        account=repository.case_status(repository.ensure_case(candidate_id))
        additions={k:max(0,account['usage'].get(k,0)+v-account['limits'][k]) for k,v in {'llm_calls':extra_calls,'tokens':extra_tokens}.items()}
        additions={k:v for k,v in additions.items() if v}
        if additions:
            repository.authorize_case_budget(candidate_id,additions,reason=reason,actor=config.get('local_operator','local_operator'),
                authorization_key='ontology-frozen:'+audit_id+':'+candidate_id+':payload-v2')
    grant='ontology_recovery_'+uuid4().hex
    context=copy.deepcopy(audit['context'])
    context.setdefault('recovery_history',[]).append({'grant_id':grant,'reason':reason,'previous_result':audit['result'],
        'selected_voter_ids':[v['id'] for v in selected],
        'delivery_versions':{v['id']:v['ontology_delivery_version'] for v in selected}})
    with repository.transaction(immediate=True) as c:
        current=c.execute('SELECT row_version FROM ontology_extension_tasks WHERE extension_id=?',(task['extension_id'],)).fetchone()
        if current[0]!=task['row_version']: raise ConflictError('task changed before recovery')
        c.execute('INSERT INTO recovery_authorizations VALUES(?,?,?,?,?,?,?,NULL)',
            (grant,audit_id,reason,extra_calls,extra_tokens,extra_seconds,time.time()))
        c.execute("UPDATE ontology_audits SET status='running',context_json=? WHERE audit_id=?",(canonical_json(context),audit_id))
    budget=CaseBudget({'max_llm_calls_per_candidate':extra_calls,'max_total_tokens':extra_tokens,'total_timeout_seconds':extra_seconds},
        repository,case_ids,task_type='ontology_recovery',task_id=grant)
    retries=min(1,extra_calls//len(selected)-1)
    reserved=len(selected)*(1+retries)
    if not budget.reserve_calls(reserved): raise ValidationError('additional recovery budget unavailable')
    def completed(vote):
        repository.ontology_recovered_vote(vote);original[vote['voter_id']]=vote
    try:
        recovered=judge_independently(selected,proposal,repository,retries=retries,timeout=180,deadline_epoch=budget.deadline_epoch,on_vote=completed)
    except Exception as exc:
        with repository.transaction(immediate=True) as c:
            c.execute("UPDATE ontology_audits SET status='paused' WHERE audit_id=?",(audit_id,))
            c.execute('UPDATE recovery_authorizations SET result_json=? WHERE grant_id=?',
                (canonical_json({'status':'paused','error':str(exc),'budget':budget.snapshot()}),grant))
        raise
    budget.refund_calls(reserved-sum(model_request_count(v) for v in recovered))
    for vote in recovered:
        for attempt in vote.get('attempts') or [vote]: budget.tokens(attempt)
    runtime=OntologyReviewRuntime(repository,release_root,voters,None,config)
    runtime.base=load_release(Path(release_root)/task['base_release_id'])
    runtime.context=copy.deepcopy(audit['context']);runtime.budget=budget
    runtime.documents=documents;runtime.candidates=candidates
    state=copy.deepcopy(audit['checkpoint']['state'])
    state.update(task=task,evidence=proposal['evidence'],checks=proposal['check_records'],delta=task['delta'])
    resolved={'case_or_task_budget_exhausted','case_token_budget_exhausted','ontology_planner_failed',
        'ontology_recovery_budget_exhausted','repeated_action_without_information_gain'}
    resolved_issues=[i for i in state['critical_issues'] if i in resolved]
    state['critical_issues']=[i for i in state['critical_issues'] if i not in resolved]
    state['stop_reason']='operator_frozen_seat_recovery'
    runtime.state=state
    state['validation']={'key':runtime.key(),'round_id':proposal['round_id'],'proposal_digest':proposal['proposal_digest'],
        'consensus':consensus(list(original.values()),proposal),'votes':list(original.values())}
    runtime.check_current(state)
    result=runtime.finalize(state)
    result.update(recovery_grant_id=grant,known_failed_seats_only=True,unknown_voter_ids=unknown,
        resolved_execution_issues=resolved_issues,previous_costs_preserved=True)
    result['recovery_budget']=copy.deepcopy(result['budget'])
    prior_usage=(audit.get('result') or {}).get('budget',{}).get('usage',{})
    result['budget']['usage']={k:prior_usage.get(k,0)+v for k,v in budget.usage.items()}
    repository.finish_ontology_audit(audit_id,result,task['row_version'])
    state['task']=repository.extension_status(task['extension_id'])
    runtime.checkpoint('finished_recovery')
    with repository.transaction(immediate=True) as c:
        c.execute('UPDATE recovery_authorizations SET result_json=? WHERE grant_id=?',(canonical_json(result),grant))
    return result
