"""Execute the current three-model review and export its persisted decisions."""
from datetime import datetime,timezone
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
JOB=ROOT/'runs/review_jobs/chapter1_20261028_review'
RUN_ID='run_chapter1_20261028_resume1'


def write(name,data):
    from review_agent.progress import write_status_snapshot
    write_status_snapshot(JOB/name,data)


def main():
    global JOB
    parser=argparse.ArgumentParser()
    parser.add_argument('--job')
    parser.add_argument('--force-fresh',action='store_true')
    parser.add_argument('--candidate-id',action='append')
    args=parser.parse_args()
    if args.job:
        JOB=Path(args.job).resolve()
        if JOB.parent!=ROOT/'runs/review_jobs': raise ValueError('review job must stay in runs/review_jobs')
    from review_agent.batch import run_review_batch
    from review_agent.repository import ReviewRepository
    from review_agent.service import load_config,ReviewService
    from review_agent.model_settings import configured_planner,validator_settings
    config=load_config(JOB/'review_config.json')
    if (JOB/'execution_plan.json').exists():
        plan=json.loads((JOB/'execution_plan.json').read_text(encoding='utf-8'))
        for filename,expected in plan['source_files_sha256'].items():
            if hashlib.sha256((ROOT/filename).read_bytes()).hexdigest()!=expected:
                raise ValueError('review source changed after the execution plan was frozen: '+filename)
    preflight=json.loads((JOB/'preflight.json').read_text(encoding='utf-8'))
    authorization=json.loads((JOB/'destination_authorization.json').read_text(encoding='utf-8'))
    if authorization.get('source')!='explicit_user_reply': raise ValueError('destination authorization is missing')
    probes=json.loads((JOB/'model_probe.json').read_text(encoding='utf-8'))
    if any(r['status']!='reachable' or not all(r['configured_model_available'].values()) for r in probes['results']):
        raise ValueError('all three committee model identities must pass the availability probe')
    slots=[configured_planner(config)]+[validator_settings(s) for s in config['validators']]
    for current,frozen in zip(slots,preflight['models']):
        if current['base_url'].rstrip('/') not in authorization['authorized_endpoints']:
            raise ValueError('review endpoint was not authorized by the user')
        if current['model']!=frozen['model'] or current['base_url'].rstrip('/')!=frozen['endpoint'].rstrip('/'):
            raise ValueError('model identity or endpoint changed after preflight')
        if current['verify_ssl'] is not frozen['verify_ssl']: raise ValueError('connection settings changed after preflight')
    if (JOB/'status.json').exists(): raise FileExistsError('review job already started; inspect its checkpoint before resuming')
    repository=ReviewRepository(ROOT/config['database_path'])
    repository.initialize()
    previous=None
    def progress(state):
        nonlocal previous
        key=tuple(state.get(k) for k in ('status','completed','independent_review_completed','review_incomplete','failed','skipped','remaining'))
        if key!=previous:
            print(json.dumps({k:state.get(k) for k in ('status','completed','independent_review_completed','review_incomplete','selected','failed','skipped','remaining')},ensure_ascii=False),flush=True)
            previous=key
    result=run_review_batch(repository,RUN_ID,config,status_file=JOB/'status.json',on_progress=progress,
        force=args.force_fresh,candidate_ids=args.candidate_id)
    write('batch_result.json',result)
    service=ReviewService(repository,config=config)
    summary=service.summary(RUN_ID)
    exports=service.export_run(RUN_ID,JOB/'exports')
    output={'status':result['status'],'run_id':RUN_ID,'finished_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'selected':result['selected'],'completed':result['completed'],'failed':result['failed'],'skipped':result['skipped'],
        'independent_review_completed':result['independent_review_completed'],'review_incomplete':result['review_incomplete'],
        'execution_id':result['execution_id'],'summary':summary,'exports':exports}
    write('review_summary.json',output)
    print(json.dumps(output,ensure_ascii=False,indent=2),flush=True)
    return 0 if result['status']=='completed' else 2


if __name__=='__main__': raise SystemExit(main())
