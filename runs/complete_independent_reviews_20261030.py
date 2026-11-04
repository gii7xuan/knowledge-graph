"""Bounded completion of exact frozen candidate reviews; never reroll unknowns."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import os
import time

from document.hierarchy_index import load_document_index
from review_agent.agent_state import AgentState
from review_agent.cli import _configured_validators
from review_agent.consensus import aggregate_votes
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.progress import write_status_snapshot
from review_agent.recovery_service import recover_frozen_audit
from review_agent.repository import ReviewRepository
from review_agent.runtime import AgentRuntime
from review_agent.service import load_config

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/independent_completion_20261030/triples'
OUT.mkdir(exist_ok=True)
REASON='用户要求补齐独立审核；仅恢复原失败席位或原票对账，保留有效票、原冻结版本与历史用量，不重发结果未知请求'


def main():
    repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');config=load_config()
    if config['mode']!='shadow' or config['routing']['auto_accept_enabled']:
        raise RuntimeError('preserve shadow/no-admission policy')
    probe=json.loads((OUT.parent/'service_probe.json').read_text(encoding='utf-8'))
    if not probe['all_healthy']: raise RuntimeError('all original model identities must be available')
    plan=json.loads((OUT.parent/'plan.json').read_text(encoding='utf-8'))
    rows=[row for row in plan['items'] if row['action'] not in {'already_completed','unknown_request_reconciliation'}]
    rows.sort(key=lambda row:(row['action']!='existing_votes_reconciliation',
        not any(seat.get('request_status')=='completed' and seat['voter_id'] in row.get('recoverable_voter_ids',[]) for seat in row.get('seats',[])),
        row['action']!='fresh_no_vote_review',row['candidate_id']))
    voters=_configured_validators(config)
    original={}
    with repo.connect() as connection:
        for row in connection.execute("SELECT audit_id,round_id,voter_id,vote_json FROM validator_votes WHERE response_status='ok'"):
            original[(row['audit_id'],row['round_id'],row['voter_id'])]=hashlib.sha256(row['vote_json'].encode()).hexdigest()
    state={'status':'running','pid':os.getpid(),'started_at_epoch':time.time(),'selected':len(rows),
        'processed':0,'results':[],'held_unknown_candidates':plan['actions']['unknown_request_reconciliation'],
        'initial_completed':plan['completed'],'temporarily_held_voter_ids':[]}
    def save(**changes):
        state.update(changes,updated_at_epoch=time.time());write_status_snapshot(OUT/'status.json',state)
    def grant(candidate_id,calls,tokens):
        account=repo.case_status(repo.ensure_case(candidate_id))
        additions={key:max(0,account['usage'].get(key,0)+value-account['limits'][key]) for key,value in {'llm_calls':calls,'tokens':tokens}.items()}
        additions={key:value for key,value in additions.items() if value}
        if additions:
            repo.authorize_case_budget(candidate_id,additions,reason=REASON,actor='codex_independent_completion',
                authorization_key='independent_completion_20261030:'+candidate_id)
    save()
    for row in rows:
        if any(repo.service_pause(__import__('review_agent.request_execution',fromlist=['service_key']).service_key(voter)) for voter in voters):
            save(status='service_paused');break
        if set(row.get('recoverable_voter_ids',[])) & set(state['temporarily_held_voter_ids']):
            result={'candidate_id':row['candidate_id'],'status':'held_after_gateway_failure','model_calls':0}
        else:
            save(active={'candidate_id':row['candidate_id'],'action':row['action'],'audit_id':row['audit_id']})
            try:
                current=repo.get_candidate(row['candidate_id'])
                if current['revision']!=row['revision'] or repo.get_audit(row['candidate_id'])['audit_id']!=row['audit_id']:
                    raise RuntimeError('current revision/audit changed; do not overwrite')
                candidate=repo.get_revision(row['candidate_id']);manifest=repo.get_run(candidate['run_id'])
                guard=OntologyGuard(load_snapshot(candidate.get('ontology_path') or manifest['ontology_path']))
                local=deepcopy(config)
                if row['action']=='fresh_no_vote_review':
                    local['planner'].update(enabled=False,strategy='deterministic')
                    local['consensus']['max_rounds']=1;local['budget']['max_retries_per_request']=0
                orchestrator=ReviewOrchestrator(repo,guard,voters,config=local)
                if row['action']=='existing_votes_reconciliation':
                    audit=repo.get_audit(row['candidate_id'],row['audit_id']);saved=audit['agent_state']
                    agent=AgentState(**saved['state']);validation=agent.current_validation()
                    proposal=next(p for p in audit['rounds'] if p['round_id']==validation['round_id'])
                    if guard.check(candidate)!=proposal['ontology_check']:
                        raise RuntimeError('ontology diagnostics changed; original vote binding preserved')
                    runtime=AgentRuntime(orchestrator);runtime.state=agent
                    runtime.tools.accept_validation(proposal,validation['votes'],agent.validation_key())
                    agent.context['execution_row_version']=current['row_version']
                    result=orchestrator._finalize(candidate,agent.context,audit['audit_id'],agent.guard,agent.bundle,
                        validation['votes'],agent.current_validation()['consensus'],runtime.policy.final_gaps(agent),
                        proposal['round_id'],saved.get('budget',{}).get('usage',{}))
                    result.update(model_calls=0,existing_votes_reconciled=True)
                elif row['action']=='fresh_no_vote_review':
                    grant(candidate['candidate_id'],3,80000)
                    document=load_document_index(manifest['document_path'],candidate['source_index_hash'],require_verified=True)
                    result=orchestrator.audit_candidate(candidate['candidate_id'],candidate['revision'],document,
                        context={'configured_strategy':config['planner']['strategy'],'executed_strategy':'deterministic',
                            'operator_reason':'complete mandatory original three-model review after no-vote planner stop',
                            'supersedes_no_vote_audit':row['audit_id']})
                else:
                    calls=sum(seat.get('request_status')!='completed' for seat in row.get('seats',[])
                              if seat['voter_id'] in row.get('recoverable_voter_ids',[]))
                    if calls: grant(candidate['candidate_id'],calls,80000)
                    result=recover_frozen_audit(repo,orchestrator,candidate['candidate_id'],row['audit_id'],reason=REASON,
                        extra_calls=calls,extra_tokens=80000 if calls else 0,extra_seconds=240 if calls else 60)
                write_status_snapshot(OUT/(row['candidate_id']+'.json'),result)
                audit=repo.get_audit(row['candidate_id'])
                last_round=result.get('selected_round_id') or (audit['rounds'][-1]['round_id'] if audit['rounds'] else None)
                votes=[vote for vote in audit['votes'] if vote['round_id']==last_round]
                for vote in votes:
                    if (vote['voter_id'] in row.get('recoverable_voter_ids',[]) or row['action']=='fresh_no_vote_review') and vote.get('outcome_unknown'):
                        if vote.get('http_status')==504 or vote.get('error_code') in {'GATEWAY_REQUEST_OUTCOME_UNKNOWN','HARD_REQUEST_TIMEOUT'}:
                            state['temporarily_held_voter_ids']=sorted(set(state['temporarily_held_voter_ids'])|{vote['voter_id']})
                result={key:result.get(key) for key in ('candidate_id','audit_id','revision','status',
                    'independent_review_completed','review_completion_status','source_support_result','workflow_state','model_calls','existing_votes_reconciled')}
                result['seats']=[{key:vote.get(key) for key in ('voter_id','response_status','error_code','outcome_unknown','late_response','cached')} for vote in votes]
            except Exception as exc:
                result={'candidate_id':row['candidate_id'],'status':'completion_error','error_type':type(exc).__name__,'error':str(exc)}
        state['results'].append(result);state['processed']+=1;save(active=None)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    with repo.connect() as connection:
        unchanged=all((stored:=connection.execute('SELECT vote_json FROM validator_votes WHERE audit_id=? AND round_id=? AND voter_id=?',key).fetchone())
            and hashlib.sha256(stored[0].encode()).hexdigest()==digest for key,digest in original.items())
    if not unchanged: raise RuntimeError('an original valid independent vote changed')
    save(status='completed_with_holds' if state['temporarily_held_voter_ids'] else 'completed',
        finished_at_epoch=time.time(),valid_original_votes_checked=len(original),valid_original_votes_unchanged=True)


if __name__=='__main__': main()
