"""Correct known technical failures; preserve frozen inputs and unknown fences."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import os
import time

from review_agent.agent_state import AgentState
from review_agent.case_budget import CaseBudget
from review_agent.cli import _configured_validators
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.progress import write_status_snapshot
from review_agent.recovery_service import recover_frozen_audit
from review_agent.repository import ReviewRepository
from review_agent.runtime import AgentRuntime
from review_agent.service import load_config
from review_agent.validation_recovery import recovery_plan
from review_agent.validators import judge_validator,model_request_count

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/independent_completion_20261030/contract_correction'
OUT.mkdir(exist_ok=True)
REASON='用户要求补齐独立审核；修复已知契约失败，或已知推理输出耗尽造成的JSON截断；保留原正文、契约、输出上限、有效票及未知请求围栏'


def grant(repo,candidate_id,calls=1,tokens=50000):
    account=repo.case_status(repo.ensure_case(candidate_id))
    additions={key:max(0,account['usage'].get(key,0)+value-account['limits'][key])
               for key,value in {'llm_calls':calls,'tokens':tokens}.items()}
    additions={key:value for key,value in additions.items() if value}
    if additions:
        repo.authorize_case_budget(candidate_id,additions,reason=REASON,actor='codex_contract_recovery',
            authorization_key='contract-correction-20261030:'+candidate_id)


def recover_reasoning_truncation(repo,orchestrator,row,voter):
    audit=repo.get_audit(row['candidate_id'],row['audit_id'])
    saved=audit['agent_state'];state=AgentState(**saved['state']);validation=state.current_validation()
    old=next(vote for vote in validation['votes'] if vote['voter_id']==voter['id'])
    request=repo.validation_request(old['request_key'])
    if (old.get('response_status')!='invalid_json' or old.get('outcome_unknown')
            or request['status']!='failed' or old.get('finish_reason')!='length'
            or (old.get('reasoning_tokens') or 0)<=0):
        raise RuntimeError('only a proven known reasoning-truncation failure is eligible')
    if voter['model_family']!='deepseek' or voter['client'].thinking_mode!='disabled':
        raise RuntimeError('the current configured original DeepSeek model with thinking disabled is required')
    proposal=next(p for p in audit['rounds'] if p['round_id']==validation['round_id'])
    if repo.get_candidate(row['candidate_id'])['revision']!=state.candidate['revision']:
        raise RuntimeError('candidate revision changed')
    declared=next((item for item in proposal.get('validator_generation',[]) if item['voter_id']==voter['id']),None)
    history=repo.request_history(old['request_key'])
    historical=history[-1]['metadata'].get('generation',{})
    cap=historical.get('max_output_tokens',(declared or {}).get('max_output_tokens'))
    if declared and cap!=declared['max_output_tokens']:
        raise RuntimeError('historical output cap differs from the frozen cap')
    adjusted={**voter,'max_output_tokens':cap}
    grant(repo,row['candidate_id'])
    authorization=repo.authorize_recovery_budget(audit['audit_id'],REASON+'；显式记录将未冻结的 thinking 默认值改为当前配置 disabled，保留原输出上限并使用不同请求键',1,50000,180)
    delivery={'grant_id':authorization,'voter_id':voter['id'],'original_request_key':old['request_key'],
        'frozen_proposal_digest':proposal['proposal_digest'],'frozen_output_cap':cap,
        'old_thinking_mode':historical.get('thinking_mode'),'new_thinking_mode':'disabled',
        'source_and_response_contract_changed':False,'reason':'known reasoning truncation, not an unknown outcome'}
    state.context.setdefault('explicit_generation_recoveries',[]).append(delivery)
    repo.update_audit_context(audit['audit_id'],state.context)
    budget=CaseBudget({'max_llm_calls_per_candidate':1,'max_total_tokens':50000,'total_timeout_seconds':180},
        repo,[repo.ensure_case(row['candidate_id'])],task_type='validation_recovery',task_id=authorization)
    if not budget.reserve_calls(1):raise RuntimeError('recovery budget unavailable')
    recovered=judge_validator(adjusted,proposal,repository=repo,max_retries=0,deadline_epoch=budget.deadline_epoch,
        _initial_feedback={'response_status':'invalid_json','error':old.get('error'),
            'instruction':'Return the complete JSON required by the original frozen contract, using the same evidence IDs. Correct the JSON truncation without changing identifiers or seeking approval.'})
    if recovered.get('actual_calls',0) and recovered.get('request_key')==old['request_key']:
        raise RuntimeError('changed generation must have a distinct request key')
    budget.refund_calls(1-model_request_count(recovered))
    for attempt in recovered.get('attempts') or [recovered]:budget.tokens(attempt)
    repo.record_recovered_vote(audit['audit_id'],proposal,recovered)
    merged={vote['voter_id']:vote for vote in validation['votes']};merged[voter['id']]=recovered
    runtime=AgentRuntime(orchestrator);runtime.state=state;runtime.budget=budget
    runtime.tools.accept_validation(proposal,list(merged.values()),state.validation_key())
    current=repo.get_candidate(row['candidate_id']);state.context['execution_row_version']=current['row_version']
    prior_usage=saved.get('budget',{}).get('usage',{})
    usage={key:prior_usage.get(key,0)+value for key,value in budget.usage.items()}
    result=orchestrator._finalize(state.candidate,state.context,audit['audit_id'],state.guard,state.bundle,
        list(merged.values()),state.current_validation()['consensus'],runtime.policy.final_gaps(state),proposal['round_id'],usage)
    repo.save_agent_checkpoint(audit['audit_id'],state.export(),{**budget.snapshot(),'usage':usage},'finished_recovery')
    delivery.update(new_request_key=recovered.get('request_key'),response_status=recovered['response_status'],
                    actual_calls=recovered.get('actual_calls',0),independent_review_completed=result['independent_review_completed'])
    with repo.transaction(immediate=True) as connection:
        connection.execute('UPDATE recovery_authorizations SET result_json=? WHERE grant_id=?',
            (json.dumps({'delivery_change':delivery,'result':result},ensure_ascii=False),authorization))
    write_status_snapshot(OUT/(row['candidate_id']+'_thinking.json'),{'delivery_change':delivery,'result':result})
    return result


if __name__=='__main__':
    config=load_config();repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');voters=_configured_validators(config)
    original=json.loads((OUT.parent/'plan.json').read_text(encoding='utf-8'))
    candidates=[row for row in original['items'] if row['action']=='recover_exact_frozen_seats']
    state={'status':'running','pid':os.getpid(),'started_at_epoch':time.time(),'selected':len(candidates),'processed':0,'results':[]}
    protected={}
    with repo.connect() as connection:
        for row in connection.execute("SELECT audit_id,round_id,voter_id,vote_json FROM validator_votes WHERE response_status='ok'"):
            protected[(row['audit_id'],row['round_id'],row['voter_id'])]=hashlib.sha256(row['vote_json'].encode()).hexdigest()
    write_status_snapshot(OUT/'status.json',state)
    for row in candidates:
        state['active']=row['candidate_id'];write_status_snapshot(OUT/'status.json',state)
        try:
            audit=repo.get_audit(row['candidate_id'],row['audit_id'])
            if audit['context'].get('independent_review_completed'):
                result={'candidate_id':row['candidate_id'],'status':'already_completed','independent_review_completed':True}
            else:
                candidate=repo.get_revision(row['candidate_id']);manifest=repo.get_run(candidate['run_id'])
                guard=OntologyGuard(load_snapshot(candidate.get('ontology_path') or manifest['ontology_path']))
                orchestrator=ReviewOrchestrator(repo,guard,voters,config=config)
                current=AgentState(**audit['agent_state']['state']);validation=current.current_validation()
                for voter in voters:
                    old=next(vote for vote in validation['votes'] if vote['voter_id']==voter['id'])
                    if (old.get('response_status')=='invalid_json' and old.get('finish_reason')=='length'
                            and (old.get('reasoning_tokens') or 0)>0 and not old.get('outcome_unknown')
                            and (repo.validation_request(old['request_key']) or {}).get('status')=='failed'):
                        recover_reasoning_truncation(repo,orchestrator,row,voter)
                audit=repo.get_audit(row['candidate_id'],row['audit_id'])
                current=AgentState(**audit['agent_state']['state']);plan=recovery_plan(current,repo,allow_permanent_failure=True)
                if plan['recoverable_voter_ids']:
                    grant(repo,row['candidate_id'],len(plan['recoverable_voter_ids']))
                    result=recover_frozen_audit(repo,orchestrator,row['candidate_id'],row['audit_id'],reason=REASON,
                        extra_calls=len(plan['recoverable_voter_ids']),extra_tokens=80000,extra_seconds=240)
                else:
                    result={'candidate_id':row['candidate_id'],'status':'completed' if audit['context'].get('independent_review_completed') else 'waiting_request_reconciliation',
                        'independent_review_completed':bool(audit['context'].get('independent_review_completed')),'unknown_voter_ids':plan['unknown_voter_ids']}
                write_status_snapshot(OUT/(row['candidate_id']+'.json'),result)
                result={key:result.get(key) for key in ('candidate_id','audit_id','status','independent_review_completed','source_support_result','workflow_state','unknown_voter_ids')}
        except Exception as exc:
            result={'candidate_id':row['candidate_id'],'status':'correction_error','error_type':type(exc).__name__,'error':str(exc)}
        state['results'].append(result);state['processed']+=1;write_status_snapshot(OUT/'status.json',state)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    with repo.connect() as connection:
        unchanged=all((row:=connection.execute('SELECT vote_json FROM validator_votes WHERE audit_id=? AND round_id=? AND voter_id=?',key).fetchone())
            and hashlib.sha256(row[0].encode()).hexdigest()==digest for key,digest in protected.items())
    if not unchanged:raise RuntimeError('an original valid vote was changed')
    state.update(status='completed',active=None,finished_at_epoch=time.time(),
        valid_votes_checked=len(protected),valid_votes_unchanged=True)
    write_status_snapshot(OUT/'status.json',state)
