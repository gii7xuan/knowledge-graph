"""Recover known technical failures of this batch, keeping exact frozen votes."""
from copy import deepcopy
from pathlib import Path
import hashlib
import argparse
import json
import os
import time
from review_agent.case_budget import CaseBudget
from review_agent.cli import _configured_validators
from review_agent.consensus import Committee
from review_agent.jobs import JobRunner
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.progress import write_status_snapshot
from review_agent.recovery_service import recover_frozen_audit
from review_agent.repository import ReviewRepository
from review_agent.revision_service import RevisionService
from review_agent.service import ReviewService,load_config

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/candidate_revision_resolution_20261030'
REASON='处理候选修订和人工分歧：仅恢复本批次确认失败的原席位，保留有效原票与未知请求；修订一致批准后应用并重新独立审核'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source-status',default='status.json');parser.add_argument('--status',default='recovery_status.json');parser.add_argument('--gemma-wait-seconds',type=int)
    args=parser.parse_args()
    original=json.loads((OUT/args.source_status).read_text(encoding='utf-8'))
    if original['status'] not in {'completed','model_gateway_hold','pilot_blocked'} or not original.get('finished_at_epoch'):
        raise RuntimeError('original controller is still active')
    repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');config=load_config();revisions=RevisionService(repo)
    if args.gemma_wait_seconds:
        if not 180<=args.gemma_wait_seconds<=360:raise ValueError('bounded Gemma wait required')
        for slot in config['validators']:
            if slot.get('family','').casefold()=='gemma':slot.update(request_timeout_seconds=args.gemma_wait_seconds,http_timeout_seconds=args.gemma_wait_seconds)
    recovery_seconds=max(240,(args.gemma_wait_seconds or 0)+45)
    if config['mode']!='shadow' or config['routing']['auto_accept_enabled']:raise RuntimeError('shadow policy required')
    voters=_configured_validators(config);policy=config['consensus']
    committee=Committee([v['id'] for v in voters],policy['required_votes'],policy['minimum_distinct_families'],policy['version'])
    local=deepcopy(config);local['planner'].update(enabled=False,strategy='deterministic')
    local['consensus']['max_rounds']=1;local['budget'].update(max_retries_per_request=0,max_parallel_candidates=1,max_evidence_items=128,max_evidence_chars=100000)
    reviewer=ReviewService(repo,config=local);runner=JobRunner(repo,reviewer.execute_audit_job,max_workers=1,job_types=['audit'])
    state={'status':'running','pid':os.getpid(),'results':[],'started_at_epoch':time.time(),
        'gemma_wait_seconds':args.gemma_wait_seconds,'model_identity_evidence_and_output_caps_unchanged':True}
    def save(**updates):state.update(updates,updated_at_epoch=time.time());write_status_snapshot(OUT/args.status,state)
    protected=[]
    with repo.connect() as connection:
        for table,keys in [('validator_votes',['audit_id','round_id','voter_id']),('revision_votes',['draft_id','voter_id']),('ontology_votes',['audit_id','round_id','voter_id'])]:
            for row in connection.execute('SELECT * FROM '+table+" WHERE json_extract(vote_json,'$.response_status')='ok'"):
                protected.append((table,keys,[row[k] for k in keys],hashlib.sha256(row['vote_json'].encode()).hexdigest()))
    save()
    try:
        for previous in original['results']:
            result={'candidate_id':previous['candidate_id'],'draft_id':previous.get('draft_id')}
            try:
                draft=revisions.draft(previous['draft_id'])
                if draft['status']=='review_incomplete' and revisions.recovery_available(draft['draft_id']):
                    save(active={'phase':'recover_revision_contract','candidate_id':draft['candidate_id']})
                    budget=CaseBudget({'max_llm_calls_per_candidate':3,'max_total_tokens':100000,'total_timeout_seconds':recovery_seconds},repo,[repo.ensure_case(draft['candidate_id'])],task_type='candidate_revision_contract_recovery',task_id=draft['draft_id'])
                    judged=revisions.review(draft['draft_id'],voters,committee,budget)
                    write_status_snapshot(OUT/(draft['draft_id']+'_recovered.json'),judged)
                    result['revision_recovery_status']=judged['status'];draft=revisions.draft(draft['draft_id'])
                if draft['status']=='approved':
                    applied=revisions.apply(draft['draft_id'],draft['draft_hash']);result.update(applied)
                    with repo.connect() as connection:
                        pending=[r[0] for r in connection.execute("SELECT job_id FROM jobs WHERE job_type='audit' AND status='queued'")]
                    if pending!=[applied['reaudit_job_id']]:raise RuntimeError('unrelated queued audit present')
                    save(active={'phase':'new_revision_independent_review','candidate_id':draft['candidate_id'],
                        'job_id':applied['reaudit_job_id']})
                    job=runner.run_available_once();write_status_snapshot(OUT/(draft['draft_id']+'_recovered_reaudit.json'),job)
                    draft=revisions.draft(draft['draft_id'])
                result['draft_status']=draft['status']
                if draft['status']=='applied':
                    audit=repo.get_audit(draft['candidate_id'])
                    if not audit['context'].get('independent_review_completed'):
                        save(active={'phase':'recover_frozen_source','candidate_id':draft['candidate_id'],'audit_id':audit['audit_id']})
                        candidate=repo.get_revision(draft['candidate_id']);manifest=repo.get_run(candidate['run_id'])
                        guard=OntologyGuard(load_snapshot(candidate.get('ontology_path') or manifest['ontology_path']))
                        orchestrator=ReviewOrchestrator(repo,guard,voters,config=local)
                        recovered=recover_frozen_audit(repo,orchestrator,draft['candidate_id'],audit['audit_id'],reason=REASON,extra_calls=3,extra_tokens=100000,extra_seconds=recovery_seconds)
                        write_status_snapshot(OUT/(draft['draft_id']+'_source_recovered.json'),recovered)
                        result['source_recovery_status']=recovered.get('status',recovered.get('workflow_state'))
                    audit=repo.get_audit(draft['candidate_id']);decision=repo.latest_decision(draft['candidate_id']) or {}
                    result.update(audit_id=audit['audit_id'],independent_completed=bool(audit['context'].get('independent_review_completed')),source_support_result=decision.get('source_support_result'),workflow_state=repo.get_candidate(draft['candidate_id'])['workflow_state'])
            except Exception as exc:result.update(error_type=type(exc).__name__,error=str(exc))
            state['results'].append(result);save(active=None);print(json.dumps(result,ensure_ascii=False),flush=True)
        with repo.connect() as connection:
            for table,keys,values,digest in protected:
                row=connection.execute('SELECT vote_json FROM '+table+' WHERE '+' AND '.join(k+'=?' for k in keys),values).fetchone()
                if not row or hashlib.sha256(row[0].encode()).hexdigest()!=digest:raise RuntimeError('valid historical vote changed')
        save(status='completed',valid_original_votes_checked=len(protected),valid_original_votes_unchanged=True,finished_at_epoch=time.time())
    finally:runner.shutdown()


if __name__=='__main__':main()
