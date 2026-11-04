"""Resume explicitly authorized work without rerolling completed/unknown seats."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import json
import os
import threading
import time

from review_agent.batch import run_review_batch
from review_agent.cli import _configured_validators
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.progress import write_status_snapshot
from review_agent.recovery_service import recover_frozen_audit
from review_agent.repository import ReviewRepository
from review_agent.service import load_config

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_resume_20261029'
FIRST='cand_006761af7b46d4cb89f6'
REASON='用户要求恢复任务；原模型服务可用性探测通过，保留有效票和原预算，仅恢复已知失败席位'


def main():
    plan=json.loads((OUT/'resume_plan.json').read_text(encoding='utf-8'))
    repository=ReviewRepository(ROOT/'runs/v3/review.sqlite3')
    config=load_config()
    voters=_configured_validators(config)
    recovery=[r for r in plan['candidates'] if r['action'] in {'recover_failed_seats','recover_partial_round'} and r['candidate_id']!=FIRST]
    # Finish the billing-interrupted audit first; every old unresolved socket stays fenced.
    recovery.sort(key=lambda r:r['action']!='recover_partial_round')
    fresh=[r['candidate_id'] for r in plan['candidates'] if r['action']=='new_review']
    lock=threading.RLock()
    state={'status':'running','pid':os.getpid(),'run_id':plan['run_id'],'started_at_epoch':time.time(),
        'recovery_selected':len(recovery),'recovery_processed':0,'recovery_independent_completed':0,
        'initial_recovered_candidate':FIRST,'initial_recovery_independent_completed':True,
        'fresh_selected':len(fresh),'unknown_candidates_held':plan['counts'].get('waiting_reconciliation',0),
        'recovery_results':[],'source_plan':str(OUT/'resume_plan.json')}
    def paused():
        with repository.connect() as c:
            return bool(c.execute("SELECT 1 FROM service_pauses WHERE status='paused' LIMIT 1").fetchone())
    def save(**updates):
        with lock:
            state.update(updates,updated_at_epoch=time.time())
            write_status_snapshot(OUT/'status.json',state)
    def progress(value):
        save(fresh_progress={k:value.get(k) for k in ('status','selected','completed','failed','independent_review_completed',
            'review_incomplete','revised','active','remaining','paused','skipped')})
    def run_fresh():
        current_config=deepcopy(config)
        # One fresh candidate plus one recovery and one ontology job bound service load.
        current_config.setdefault('budget',{})['max_parallel_candidates']=1
        if not fresh:
            return {'status':'completed','selected':0,'completed':0}
        return run_review_batch(repository,plan['run_id'],current_config,candidate_ids=fresh,
            status_file=OUT/'fresh_status.json',on_progress=progress,should_cancel=paused)
    save()
    with ThreadPoolExecutor(max_workers=1,thread_name_prefix='fresh-review') as pool:
        future=pool.submit(run_fresh)
        for row in recovery:
            if paused():
                save(status='service_paused');break
            save(current_recovery={'candidate_id':row['candidate_id'],'audit_id':row['audit_id']})
            try:
                candidate=repository.get_candidate(row['candidate_id'])
                manifest=repository.get_run(candidate['run_id'])
                guard=OntologyGuard(load_snapshot(candidate.get('ontology_path') or manifest['ontology_path']))
                orchestrator=ReviewOrchestrator(repository,guard,voters,config=config)
                calls=len(row.get('recoverable_voter_ids',[])) or 3
                result=recover_frozen_audit(repository,orchestrator,row['candidate_id'],row['audit_id'],reason=REASON,
                    extra_calls=calls,extra_tokens=64000,extra_seconds=180)
                write_status_snapshot(OUT/('recovery_'+row['candidate_id']+'.json'),result)
                summary={k:result.get(k) for k in ('audit_id','candidate_id','status','independent_review_completed',
                    'committed','decision','workflow_state','recovery_grant_id','unknown_request_voter_ids','reason_code')}
                if result.get('independent_review_completed'): state['recovery_independent_completed']+=1
            except Exception as exc:
                summary={'candidate_id':row['candidate_id'],'audit_id':row['audit_id'],'status':'recovery_error',
                    'error_type':type(exc).__name__,'error':str(exc)}
                write_status_snapshot(OUT/('recovery_'+row['candidate_id']+'.json'),summary)
            state['recovery_results'].append(summary)
            state['recovery_processed']+=1
            save()
            print(json.dumps(summary,ensure_ascii=False),flush=True)
        save(current_recovery=None,recovery_finished=True)
        try:
            result=future.result()
            write_status_snapshot(OUT/'fresh_result.json',result)
            save(status='service_paused' if paused() else 'completed',fresh_finished=True,
                finished_at_epoch=time.time())
        except Exception as exc:
            save(status='failed',error_type=type(exc).__name__,error=str(exc),finished_at_epoch=time.time())
            raise


if __name__=='__main__':
    main()
