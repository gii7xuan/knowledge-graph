"""Prepare current source-supported tasks; defer fixed-committee dispatch explicitly."""
from pathlib import Path
from copy import deepcopy
import argparse,json,time,os
from ontology_review.preparation import OntologyPreparationRuntime
from ontology_review.service import effective_config
from ontology_review.publisher import current_release_id
from review_agent.repository import ReviewRepository
from review_agent.service import load_config
from review_agent.cli import _configured_validators
from review_agent.model_settings import create_planner_client
from review_agent.progress import write_status_snapshot
from review_agent.contracts import sha256_json

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/ontology_publication_20261031'
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int,default=3);parser.add_argument('--resume',action='store_true');parser.add_argument('--only',action='append');parser.add_argument('--recover-known-failures',action='store_true');args=parser.parse_args()
    repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');config=load_config()
    if config['mode']!='shadow' or config['routing']['auto_accept_enabled'] or config['ontology_extension']['auto_publish_enabled']:raise RuntimeError('shadow/no-admission/no-auto-publication required')
    statefile=OUT/'remaining_preparation_status.json'
    state=json.loads(statefile.read_text(encoding='utf-8')) if args.resume and statefile.exists() else {'status':'running','started_at_epoch':time.time(),'results':[]}
    completed={r['extension_id'] for r in state['results']};head=current_release_id(ROOT/'runs/ontology_releases')
    if args.recover_known_failures:
        latest={r['extension_id']:r for r in state['results']};recover=[]
        for identity,prior in latest.items():
            if prior.get('has_delta') or sum(r['extension_id']==identity for r in state['results'])>=2:continue
            known_budget=prior.get('error_type')=='ConflictError' and prior.get('error')=='budget authorization identity cannot be reused for different terms'
            if not known_budget and prior.get('stop_reason')!='ontology_planner_failed':continue
            if prior.get('audit_id'):
                audit=repo.ontology_audit(prior['audit_id'])
                attempts=[a for step in audit['steps'] if step['role']=='planner' for a in step.get('attempts',[])]
                if any(a.get('outcome_unknown') for a in attempts):continue
                failed=[s for s in audit['steps'] if s['role']=='planner' and s.get('status')=='failed']
                if not failed:continue
            if repo.extension_status(identity).get('delta') is not None:continue
            recover.append({'extension_id':identity,'prior_audit_id':prior.get('audit_id'),'known_budget_failure':known_budget,
                'reason':'one bounded preparation recovery after confirmed planner contract/budget failure; no unknown request replay; no independent vote'})
        (OUT/'preparation_known_failure_recovery_plan.json').write_text(json.dumps(recover,ensure_ascii=False,indent=2),encoding='utf-8')
        args.only=[r['extension_id'] for r in recover]
        if not args.only:
            print('No safely recoverable preparation failures');raise SystemExit(0)
    dossiers=json.loads((OUT/'current_task_dossiers.json').read_text(encoding='utf-8'))
    selected=[d['extension_id'] for d in dossiers if not d['has_delta'] and d['blockers']==['no_current_delta'] and
              (d['extension_id'] in args.only if args.only else d['extension_id'] not in completed)]
    selected=selected[:max(0,args.limit)]
    local=deepcopy(config);local['ontology_extension']['budget'].update(max_llm_calls_per_candidate=8,max_total_tokens=300000,
        max_planner_steps=10,max_tool_calls=12,max_retries_per_request=1,total_timeout_seconds=300)
    voters=_configured_validators(local);planner=create_planner_client(effective_config(local))
    if planner is None:raise RuntimeError('original ontology planner unavailable')
    state.update(status='running',pid=os.getpid(),selected_this_pass=len(selected),base_release_id=head)
    state.pop('finished_at_epoch',None)
    write_status_snapshot(statefile,state)
    for identity in selected:
        task=repo.extension_status(identity)
        if task.get('delta') is not None:continue
        if current_release_id(ROOT/'runs/ontology_releases')!=head:state['status']='base_release_changed';break
        state.update(active=identity,updated_at_epoch=time.time());write_status_snapshot(statefile,state)
        try:
            authorized=set()
            for sub in task['subscriptions']:
                if sub['status']!='active':continue
                c=repo.get_candidate(sub['candidate_id']);d=repo.latest_decision(c['candidate_id']) or {}
                if c['revision']!=sub['revision'] or c['row_version']!=sub['expected_row_version'] or d.get('source_support_result')!='supported':raise RuntimeError('current source prerequisite changed')
                if c['candidate_id'] in authorized:continue
                authorized.add(c['candidate_id'])
                account=repo.case_status(repo.ensure_case(c['candidate_id']))
                extra={k:max(0,account['usage'][k]+v-account['limits'][k]) for k,v in {'llm_calls':8,'tokens':300000,'tool_calls':12}.items()};extra={k:v for k,v in extra.items() if v}
                if extra:repo.authorize_case_budget(c['candidate_id'],extra,
                    reason='用户要求先完成不依赖未知审核结果的其余可推进工作：仅执行真实本体草案调查与预检，不派发独立投票、不发布，保留原 case 用量',
                    actor='codex_ontology_preparation',authorization_key='ontology-preparation-20261031:'+identity+':'+c['candidate_id']+':'+str(task['row_version'])+':'+sha256_json(extra))
            runtime=OntologyPreparationRuntime(repo,ROOT/'runs/ontology_releases',voters,planner,local)
            result=runtime.run(identity);after=repo.extension_status(identity)
            item={'extension_id':identity,'audit_id':result['audit_id'],'has_delta':after.get('delta') is not None,
                'delta_revision':after['delta_revision'],'stop_reason':result.get('stop_reason'),
                'independent_review_completed':False,'release_published':False,
                'checks':{k:v.get('status') for k,v in runtime.state.get('checks',{}).items()},
                'operations':(after.get('delta') or {}).get('operations',[]),'budget':result.get('budget')}
            audit=repo.ontology_audit(result['audit_id'])
            failed=[s for s in audit['steps'] if s['role']=='planner' and s.get('status')=='failed']
            if failed:item['planner_failure']={'error':failed[-1].get('error'),'attempts':[
                {k:a.get(k) for k in ('attempt_no','response_status','error','outcome_unknown','input_tokens','output_tokens','response_id')}
                for a in failed[-1].get('attempts',[])]}
        except Exception as exc:item={'extension_id':identity,'error_type':type(exc).__name__,'error':str(exc),'independent_review_completed':False,'release_published':False}
        state['results'].append(item);write_status_snapshot(statefile,state)
        print(json.dumps({k:item.get(k) for k in ('extension_id','audit_id','has_delta','checks','error_type','error')},ensure_ascii=False),flush=True)
        if len(state['results'])>=3:
            recent=state['results'][-3:]
            if all(r.get('error_type') for r in recent) or all(r.get('planner_failure',{}).get('attempts') for r in recent):state['status']='pilot_needs_investigation';break
    else:state['status']='completed'
    state.update(active=None,finished_at_epoch=time.time());write_status_snapshot(statefile,state)
