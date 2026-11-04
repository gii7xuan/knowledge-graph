"""Bounded real-model follow-up, with a pilot gate before queue expansion."""
from datetime import datetime,timezone
from pathlib import Path
import json
import os
import time
from uuid import uuid4
from document.hierarchy_index import load_document_index
from review_agent.case_budget import CaseBudget
from review_agent.cli import _configured_validators
from review_agent.consensus import Committee
from review_agent.jobs import JobRunner
from review_agent.progress import write_status_snapshot
from review_agent.repository import ReviewRepository
from review_agent.revision_service import RevisionService
from review_agent.service import ReviewService,load_config

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_followup_20261030'
REASON='用户于2026-10-30要求继续后续任务和本体扩展；新规划版本恢复调查，保留历史调用、费用、有效票与反证'
ONTOLOGY_ONLY=False
GRANT_SCOPE='ontology'


def main():
    validation=json.loads((OUT/'validation.json').read_text(encoding='utf-8'))
    migration=json.loads((OUT/'migration_receipt.json').read_text(encoding='utf-8'))
    if not validation['successful'] or migration['migration_version']<8: raise RuntimeError('validated v8 migration required')
    plan=json.loads((OUT/'continuation_plan.json').read_text(encoding='utf-8'))
    config=load_config();repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3')
    if config['mode']!='shadow' or config['ontology_extension']['auto_publish_enabled']: raise RuntimeError('preserve reviewed shadow/publication policy')
    service=ReviewService(repo,config=config)
    state={'status':'running','phase':'revision_review','pid':os.getpid(),'started_at_epoch':time.time(),
        'pilot_selected':len(plan['pilot']),'pilot_results':[],'remaining_results':[],'case_budget_grants':[]}
    def save(**updates):
        state.update(updates,updated_at_epoch=time.time());write_status_snapshot(OUT/'status.json',state)
    def paused():
        with repo.connect() as c: return bool(c.execute("SELECT 1 FROM service_pauses WHERE status='paused' LIMIT 1").fetchone())
    def grant(candidate_id,scope,room):
        key='followup_20261030:'+scope+':'+candidate_id
        with repo.connect() as c:
            prior=c.execute('SELECT grant_id FROM case_budget_grants WHERE authorization_key=?',(key,)).fetchone()
        if prior: return prior[0]
        account=repo.case_status(repo.ensure_case(candidate_id))
        additions={k:max(0,account['usage'].get(k,0)+v-account['limits'][k]) for k,v in room.items()}
        additions={k:v for k,v in additions.items() if v}
        if not additions: return None
        record=repo.authorize_case_budget(candidate_id,additions,reason=REASON,actor=config.get('local_operator','local_operator'),authorization_key=key)
        state['case_budget_grants'].append({'grant_id':record['grant_id'],'candidate_id':candidate_id,'additions':additions})
        save();return record['grant_id']
    save()
    # The old approval is retained, but its stale CAS token is never relabelled.
    revisions=RevisionService(repo)
    old=revisions.draft('draft_3bcb38875ddb42e294442a57213f4bb6')
    if old['status']=='applied':
        save(revision={'status':'already_applied','draft_id':old['draft_id']})
    elif ONTOLOGY_ONLY:
        save(revision={'status':'needs_human','existing_valid_opposition_preserved':True,
            'review_record':str(ROOT/'runs/review_followup_20261030/revision_review.json')})
    else:
        current=repo.get_candidate(old['candidate_id']);candidate=repo.get_revision(old['candidate_id'])
        manifest=repo.get_run(candidate['run_id'])
        document=load_document_index(manifest['document_path'],candidate['source_index_hash'],require_verified=True)
        grant(candidate['candidate_id'],'revision',{'llm_calls':6,'tokens':100000,'tool_calls':6})
        draft=revisions.propose(candidate,current['row_version'],old['changes'],old['evidence_ids'],
            '原批准草案基线已变更，依当前基线重新独立核验修订',old['evidence_bundle'],document,
            protected_ids=old['protected_evidence_ids'],source_audit_id=old.get('source_audit_id'))
        voters=_configured_validators(config)
        policy=config['consensus']
        committee=Committee([v['id'] for v in voters],required_votes=policy['required_votes'],minimum_distinct_families=policy['minimum_distinct_families'])
        budget=CaseBudget({'max_llm_calls_per_candidate':6,'max_total_tokens':100000,'total_timeout_seconds':300},repo,
            [repo.ensure_case(candidate['candidate_id'])],task_type='candidate_revision_review',task_id=draft['draft_id'])
        reviewed=revisions.review(draft['draft_id'],voters,committee,budget)
        if reviewed['status']=='review_incomplete' and revisions.recovery_available(draft['draft_id']) and not paused():
            reviewed=revisions.review(draft['draft_id'],voters,committee,budget)
        write_status_snapshot(OUT/'revision_review.json',reviewed)
        if reviewed['status']=='approved':
            applied=revisions.apply(draft['draft_id'],draft['draft_hash'])
            save(revision=applied)
            audit_runner=JobRunner(repo,service.execute_audit_job,max_workers=1,job_types=['audit'])
            try:
                job=audit_runner.run_available_once()
                write_status_snapshot(OUT/'revision_reaudit_job.json',job)
                save(revision_reaudit_job={'job_id':job['job_id'],'status':job['status']})
            finally: audit_runner.shutdown()
        else: save(revision={'status':reviewed['status'],'draft_id':draft['draft_id']})
    if paused(): save(status='service_paused');return
    def enqueue(task):
        current=repo.extension_status(task['extension_id'])
        if current['status'] not in {'needs_human','proposed'}: return None
        subscribers=[]
        for sub in current['subscriptions']:
            if sub['status']!='active': continue
            candidate=repo.get_candidate(sub['candidate_id'])
            if candidate['revision']==sub['revision'] and candidate['row_version']==sub['expected_row_version'] and candidate['human_status']=='pending':
                subscribers.append(candidate['candidate_id'])
        if not subscribers: return None
        for candidate_id in subscribers:
            grant(candidate_id,GRANT_SCOPE,{'llm_calls':32,'tokens':500000,'tool_calls':24})
        audit_id='ontology_audit_'+uuid4().hex
        from ontology_review.contracts import PLANNER_VERSION
        return repo.create_job('ontology_extension',{'extension_id':task['extension_id'],'audit_id':audit_id,
            'followup_reason':REASON,'planning_version':PLANNER_VERSION})
    def run_one(runner):
        job=runner.run_available_once()
        if not job: return None
        payload=job['payload'];aid=payload['audit_id']
        with repo.connect() as c:
            proposals=c.execute('SELECT COUNT(*) FROM ontology_proposals WHERE audit_id=?',(aid,)).fetchone()[0]
            votes=[json.loads(r[0]) for r in c.execute('SELECT vote_json FROM ontology_votes WHERE audit_id=? AND round_id=?',(aid,job['progress'].get('selected_round_id')))]
        result={'job_id':job['job_id'],'extension_id':payload['extension_id'],'audit_id':aid,'job_status':job['status'],
            'recommendation':job['progress'].get('recommendation'),'stop_reason':job['progress'].get('stop_reason'),
            'error':job['progress'].get('error'),'proposals':proposals,'votes':len(votes),
            'valid_votes':sum(v.get('response_status')=='ok' for v in votes)}
        write_status_snapshot(OUT/(aid+'.json'),job)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        return result
    save(phase='ontology_pilot')
    for task in plan['pilot']: enqueue(task)
    runner=JobRunner(repo,service.execute_audit_job,max_workers=1,job_types=['ontology_extension'])
    try:
        for _ in plan['pilot']:
            if paused(): save(status='service_paused');return
            save(active_stage='running next ontology pilot')
            result=run_one(runner)
            if result: state['pilot_results'].append(result)
            save()
        pipeline_ready=any(r['proposals'] and r['valid_votes']>=3 for r in state['pilot_results'])
        if not pipeline_ready:
            save(status='pilot_blocked',phase='investigate_pilot',active_stage=None,finished_at_epoch=time.time());return
        save(phase='ontology_remaining',pilot_gate_passed=True)
        queued=[enqueue(task) for task in plan['remaining_tasks']]
        save(remaining_selected=sum(job is not None for job in queued))
        while not paused():
            result=run_one(runner)
            if result is None: break
            state['remaining_results'].append(result);save()
        save(status='service_paused' if paused() else 'completed',active_stage=None,finished_at_epoch=time.time())
    finally: runner.shutdown()


if __name__=='__main__':
    main()
