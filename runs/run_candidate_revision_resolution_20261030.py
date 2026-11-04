"""Review concrete patches, apply unanimous drafts, then audit each new version."""
from copy import deepcopy
from pathlib import Path
import hashlib
import argparse
import json
import os
import time
from document.hierarchy_index import load_document_index
from evidence.verification import make_evidence_bundle,verify_evidence_bundle
from review_agent.case_budget import CaseBudget
from review_agent.cli import _configured_validators
from review_agent.consensus import Committee
from review_agent.jobs import JobRunner
from review_agent.progress import write_status_snapshot
from review_agent.repository import ReviewRepository
from review_agent.revision_patch import merge_triple_patch
from review_agent.revision_service import RevisionService
from review_agent.service import ReviewService,load_config

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/candidate_revision_resolution_20261030'
REASON='用户要求处理候选修订和人工分歧；仅提交原文有依据的真实表达变更，经原三模型一致批准后应用，新版本重新独立审核；不以本体约束反推类型，不覆盖原票或未知请求'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',default='revision_plan.json');parser.add_argument('--status',default='status.json');parser.add_argument('--authorization-tag',default='candidate-revision-resolution-20261030');parser.add_argument('--gemma-wait-seconds',type=int)
    parser.add_argument('--ready-proof-candidate');parser.add_argument('--ready-proof-audit')
    args=parser.parse_args()
    config=load_config();repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');service=RevisionService(repo)
    if args.gemma_wait_seconds:
        if not 180<=args.gemma_wait_seconds<=360:raise ValueError('bounded Gemma wait required')
        for slot in config['validators']:
            if slot.get('family','').casefold()=='gemma':slot.update(request_timeout_seconds=args.gemma_wait_seconds,http_timeout_seconds=args.gemma_wait_seconds)
    if config['mode']!='shadow' or config['routing']['auto_accept_enabled']:raise RuntimeError('shadow/no-admission policy required')
    voters=_configured_validators(config);policy=config['consensus']
    committee=Committee([v['id'] for v in voters],policy['required_votes'],policy['minimum_distinct_families'],policy['version'])
    local=deepcopy(config);local['planner'].update(enabled=False,strategy='deterministic')
    local['consensus']['max_rounds']=1;local['budget'].update(max_retries_per_request=0,max_parallel_candidates=1,
        max_evidence_items=128,max_evidence_chars=100000)
    reviewer=ReviewService(repo,config=local)
    runner=JobRunner(repo,reviewer.execute_audit_job,max_workers=1,job_types=['audit'])
    plans=json.loads((OUT/args.plan).read_text(encoding='utf-8'))['items']
    protected={}
    with repo.connect() as connection:
        for table in ('validator_votes','revision_votes','ontology_votes'):
            keys=['audit_id','round_id','voter_id'] if table!='revision_votes' else ['draft_id','voter_id']
            for row in connection.execute("SELECT * FROM "+table+" WHERE json_extract(vote_json,'$.response_status')='ok'"):
                protected[(table,tuple(row[key] for key in keys))]=(keys,hashlib.sha256(row['vote_json'].encode()).hexdigest())
    state={'status':'running','pid':os.getpid(),'started_at_epoch':time.time(),'selected':len(plans),'processed':0,
        'applied':0,'new_revision_independently_completed':0,'results':[],'pilot_gate_passed':False,
        'gemma_wait_seconds':args.gemma_wait_seconds,'model_identity_evidence_and_output_caps_unchanged':True}
    if bool(args.ready_proof_candidate)!=bool(args.ready_proof_audit):raise ValueError('complete readiness proof required')
    if args.ready_proof_candidate:
        from review_agent.proposal_resolver import selected_proposal
        proof_candidate=repo.get_revision(args.ready_proof_candidate);decision=repo.latest_decision(args.ready_proof_candidate) or {}
        if decision.get('audit_id')!=args.ready_proof_audit or decision.get('source_support_result')!='supported':raise ValueError('current source-supported proof required')
        proof_audit=repo.get_audit(args.ready_proof_candidate,args.ready_proof_audit)
        proof,votes=selected_proposal(repo,proof_candidate,decision)
        if (not proof_audit['context'].get('independent_review_completed') or
                {v['voter_id'] for v in votes}!=set(committee.voter_ids) or
                any(not any(vote['voter_id']==voter['id'] and vote['model_id']==voter['model_id'] and vote['model_family']==voter['model_family'] for vote in votes) for voter in voters)):
            raise ValueError('readiness proof does not cover the configured independent committee')
        state['verified_readiness_proof']={'candidate_id':args.ready_proof_candidate,'audit_id':args.ready_proof_audit,
            'proposal_digest':proof['proposal_digest'],'source_support_result':'supported',
            'proof_votes_reused_for_new_candidates':False,'verified_at_epoch':time.time()}
    def save(**updates):state.update(updates,updated_at_epoch=time.time());write_status_snapshot(OUT/args.status,state)
    def room(identity):
        account=repo.case_status(repo.ensure_case(identity));wanted={'llm_calls':12,'tokens':220000,'tool_calls':16}
        additions={key:max(0,account['usage'].get(key,0)+value-account['limits'][key]) for key,value in wanted.items()}
        additions={key:value for key,value in additions.items() if value}
        if additions:repo.authorize_case_budget(identity,additions,reason=REASON,actor='codex_candidate_revision_resolution',authorization_key=args.authorization_tag+':'+identity)
    save()
    try:
        for number,plan in enumerate(plans):
            if number==3:
                ready=bool(state.get('verified_readiness_proof')) or any(item.get('applied') and item.get('new_revision_independent_completed') for item in state['results'])
                if not ready:save(status='pilot_blocked');break
                save(pilot_gate_passed=True)
            save(active={'candidate_id':plan['candidate_id'],'phase':'revision_review'})
            result={'candidate_id':plan['candidate_id'],'applied':False}
            try:
                current=repo.get_candidate(plan['candidate_id']);candidate=repo.get_revision(plan['candidate_id'])
                if candidate['revision']!=plan['base_revision'] or current['row_version']!=plan['expected_row_version']:
                    raise RuntimeError('candidate baseline changed since source review; replan instead of rebinding old tickets')
                audit=repo.get_audit(plan['candidate_id'],plan['source_audit_id'])
                source=next(p for p in audit['rounds'] if p['proposal_digest']==plan['source_proposal_digest'])
                manifest=repo.get_run(candidate['run_id'])
                document=load_document_index(manifest['document_path'],candidate['source_index_hash'],require_verified=True)
                archive=[item for p in audit['rounds'] for item in p['evidence_bundle']['items']]
                archive.extend((audit.get('agent_state') or {}).get('state',{}).get('archive',[]))
                archive.extend(plan.get('additional_verified_evidence',[]))
                bundle=verify_evidence_bundle(make_evidence_bundle(archive,['current_and_historical_verified_revision_scope']),document,candidate)
                if bundle.get('rejected_items'):raise RuntimeError('source archive contains unverified anchors; curate without dropping protected evidence')
                allowed={item['evidence_id'] for item in bundle['items']}
                if not set(plan['protected_evidence_ids'])<=allowed:raise RuntimeError('protected material missing; do not submit an incomplete revision')
                target=merge_triple_patch(candidate['triple'],plan['changes'],strict=True)
                with repo.connect() as connection:
                    prior_ids=[row[0] for row in connection.execute('SELECT draft_id FROM revision_drafts WHERE candidate_id=?',(candidate['candidate_id'],))]
                for identity in prior_ids:
                    old=service.draft(identity)
                    if any(v.get('response_status')=='ok' and v.get('decision') in {'reject','uncertain'} for v in old.get('review_result',{}).get('votes',[])):
                        if old['after']['triple']==target and old['evidence_bundle']['evidence_hash']==bundle['evidence_hash']:
                            raise RuntimeError('identical draft already has valid opposition; substantive correction or new evidence required')
                draft=service.propose(candidate,current['row_version'],plan['changes'],plan['evidence_ids'],plan['reason'],bundle,document,
                    protected_ids=plan['protected_evidence_ids'],source_audit_id=plan['source_audit_id'])
                room(candidate['candidate_id'])
                budget=CaseBudget({'max_llm_calls_per_candidate':6,'max_total_tokens':180000,'total_timeout_seconds':480},repo,
                    [repo.ensure_case(candidate['candidate_id'])],task_type='candidate_revision_review',task_id=draft['draft_id'])
                judged=service.review(draft['draft_id'],voters,committee,budget)
                if judged['status']=='review_incomplete' and service.recovery_available(draft['draft_id']):
                    judged=service.review(draft['draft_id'],voters,committee,budget)
                write_status_snapshot(OUT/(draft['draft_id']+'.json'),judged)
                result.update(draft_id=draft['draft_id'],revision_review_status=judged['status'],
                    votes=[{key:vote.get(key) for key in ('voter_id','response_status','decision','error_code','outcome_unknown')} for vote in judged.get('votes',[])])
                if judged['status']=='approved':
                    applied=service.apply(draft['draft_id'],draft['draft_hash']);result.update(applied,applied=True)
                    state['applied']+=1;save(active={'candidate_id':plan['candidate_id'],'phase':'new_revision_independent_review'})
                    with repo.connect() as connection:
                        pending=[row[0] for row in connection.execute("SELECT job_id FROM jobs WHERE job_type='audit' AND status='queued' ORDER BY created_at")]
                    if pending!=[applied['reaudit_job_id']]:raise RuntimeError('another audit queue appeared; leave unrelated jobs untouched')
                    job=runner.run_available_once();write_status_snapshot(OUT/(draft['draft_id']+'_reaudit.json'),job)
                    latest=repo.get_audit(plan['candidate_id']);decision=repo.latest_decision(plan['candidate_id']) or {}
                    result.update(reaudit_job_status=job['status'],new_revision_independent_completed=bool(latest['context'].get('independent_review_completed')),
                        new_audit_id=latest['audit_id'],source_support_result=decision.get('source_support_result'),workflow_state=repo.get_candidate(plan['candidate_id'])['workflow_state'])
                    if result['new_revision_independent_completed']:state['new_revision_independently_completed']+=1
            except Exception as exc:
                result.update(status='resolution_error',error_type=type(exc).__name__,error=str(exc))
            state['results'].append(result);state['processed']+=1;save(active=None)
            print(json.dumps(result,ensure_ascii=False),flush=True)
            if any(vote.get('outcome_unknown') and vote.get('error_code') in {'GATEWAY_REQUEST_OUTCOME_UNKNOWN','HARD_REQUEST_TIMEOUT'} for vote in result.get('votes',[])):
                save(status='model_gateway_hold');break
        else:save(status='completed')
    finally:runner.shutdown()
    with repo.connect() as connection:
        for (table,values),(keys,digest) in protected.items():
            stored=connection.execute('SELECT vote_json FROM '+table+' WHERE '+' AND '.join(key+'=?' for key in keys),values).fetchone()
            if not stored or hashlib.sha256(stored[0].encode()).hexdigest()!=digest:raise RuntimeError('an original valid vote changed')
    save(finished_at_epoch=time.time(),valid_original_votes_checked=len(protected),valid_original_votes_unchanged=True)


if __name__=='__main__':main()
