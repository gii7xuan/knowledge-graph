"""Review exact prepared deltas with the original committee, without a planner rerun."""
from pathlib import Path
import hashlib
import json
import os
import time
from uuid import uuid4

from ontologyBuild.release_service import load_release
from ontology_review.contracts import versions
from ontology_review.evidence import load_sources
from ontology_review.publisher import current_release_id
from ontology_review.runtime import OntologyReviewRuntime
from review_agent.case_budget import CaseBudget
from review_agent.cli import _configured_validators
from review_agent.contracts import sha256_json
from review_agent.progress import write_status_snapshot
from review_agent.repository import ReviewRepository
from review_agent.service import load_config

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/independent_completion_20261030/ontology'
OUT.mkdir(exist_ok=True)
RELEASES=ROOT/'runs/ontology_releases'
IDS=['ext_4a9a96a9450145e596202e196fd0c124','ext_f9b004e2df2c4613aa4d060302238889',
     'ext_4272d42b83144e8c8da86d3530f76019','ext_da6ddb7f3eeb40db9c3a0b314301e71d']
REASON='用户要求补齐独立审核；对修订后或尚未产生独立票的明确差异，执行真实工具检查后提交原三模型；不复投已有有效或未知票，不发布'


def review(repo,config,voters,identity):
    task=repo.extension_status(identity)
    if task.get('delta') is None:raise RuntimeError('a withdrawn draft cannot be reviewed')
    if current_release_id(RELEASES)!=task['base_release_id']:raise RuntimeError('base release changed')
    with repo.connect() as connection:
        # Stored proposals bind object_revision inside their immutable JSON.
        prior=connection.execute("SELECT p.audit_id FROM ontology_proposals p JOIN ontology_audits a ON p.audit_id=a.audit_id WHERE a.extension_id=? AND json_extract(p.proposal_json,'$.object_revision')=? LIMIT 1",
            (identity,task['delta_revision'])).fetchone()
    if prior:raise RuntimeError('current delta already has frozen tickets; recover those exact seats instead')
    counter=repo.ontology_counter_history(identity)
    task['review_evidence']=list({item['evidence_id']:item for item in [*task.get('review_evidence',[]),*counter]}.values())
    evidence,documents,candidates,cases=load_sources(repo,task)
    for candidate_id in candidates:
        account=repo.case_status(repo.ensure_case(candidate_id))
        additions={key:max(0,account['usage'].get(key,0)+value-account['limits'][key])
            for key,value in {'llm_calls':3,'tokens':400000,'tool_calls':4}.items()}
        additions={key:value for key,value in additions.items() if value}
        if additions:
            repo.authorize_case_budget(candidate_id,additions,reason=REASON,actor='codex_independent_ontology_review',
                authorization_key='independent-prepared-ontology-20261030:'+identity+':'+str(task['delta_revision'])+':'+candidate_id)
    runtime=OntologyReviewRuntime(repo,RELEASES,voters,None,config)
    runtime.base=load_release(RELEASES/task['base_release_id']);runtime.documents=documents;runtime.candidates=candidates
    members=[{key:voter[key] for key in ('id','model_id','model_family')}|{'model_identity':str(voter.get('model_identity') or voter['model_id']).casefold()} for voter in voters]
    runtime.context={'versions':versions(),'committee_members':members,
        'committee_policy':{key:config['consensus'][key] for key in ('committee_size','required_votes','minimum_distinct_families')},
        'planner_model':None,'planner_family':None,'options_hash':sha256_json(runtime.options),'mode':'shadow',
        'configured_strategy':'llm_react','executed_strategy':'operator_prepared_delta_real_tools',
        'operator_reason':REASON,'no_planner_output_or_semantic_vote_fabricated':True}
    audit_id=repo.start_ontology_audit(identity,runtime.context,'ontology_audit_'+uuid4().hex)
    runtime.budget=CaseBudget({**runtime.options['budget'],'max_llm_calls_per_candidate':3,'max_retries_per_request':0,
        'total_timeout_seconds':480},repo,cases,task_type='ontology_extension',task_id=audit_id)
    runtime.state={'audit_id':audit_id,'task':task,'delta':task['delta'],'evidence':evidence,
        'checks':{},'history':[],'validations':{},'validation':None,'fingerprints':{},'critical_issues':[],
        'full_schema_searched':False,'stop_reason':'','step_no':0,
        'prior_counter_evidence_ids':sorted({item['evidence_id'] for item in counter}|set(task.get('unresolved_counter_evidence_ids',[])))}
    runtime.checkpoint('ready')
    methods=[('search_existing_schema',lambda:runtime.tools.search_existing_schema(' '.join(op['changes'].get('label','') for op in task['delta']['operations']) or task['description'][:600])),
             ('validate_schema_structure',runtime.tools.validate_schema_structure),
             ('analyze_schema_impact',runtime.tools.analyze_schema_impact),
             ('run_competency_checks',runtime.tools.run_competency_checks)]
    try:
        for number,(name,execute) in enumerate(methods,1):
            runtime.check_current(runtime.state)
            if not runtime.budget.consume_tool():raise RuntimeError('deterministic check budget exhausted')
            result=execute();runtime.state['step_no']=number
            runtime.state['history'].append({'step_no':number,'tool':name,'status':result['status']})
            repo.ontology_step(audit_id,number,'tool',{'tool':name,**result});runtime.checkpoint()
        if runtime.state['checks']['structure']['status']!='pass':raise RuntimeError('structure failure must be repaired first')
        runtime.run_validation()
        runtime.state['stop_reason']='operator_prepared_delta_independent_review_completed_or_held'
    except Exception as exc:
        runtime.state['stop_reason']=type(exc).__name__+': '+str(exc)
        runtime.state['critical_issues'].append(runtime.state['stop_reason'])
        runtime.checkpoint('validating' if runtime.state.get('pending_validation') else 'interrupted')
        with repo.transaction(immediate=True) as connection:
            connection.execute("UPDATE ontology_audits SET status='paused',lease_expires=0 WHERE audit_id=?",(audit_id,))
        raise
    result=runtime.finalize(runtime.state)
    audit=repo.ontology_audit(audit_id)
    selected=runtime.state.get('validation') or {}
    eligible=selected.get('consensus',{}).get('eligible_voter_ids',[])
    result.update(independent_review_completed=len(eligible)==3,
        independent_valid_seats=len(eligible),counter_evidence_ids=selected.get('consensus',{}).get('counter_evidence_ids',[]))
    repo.finish_ontology_audit(audit_id,result,task['row_version'])
    runtime.state['task']=repo.extension_status(identity);runtime.checkpoint('finished')
    write_status_snapshot(OUT/(identity+'.json'),{'result':result,'votes':audit['votes']})
    return {key:result.get(key) for key in ('extension_id','audit_id','independent_review_completed',
        'independent_valid_seats','recommendation','selected_proposal_digest','stop_reason')}|{
        'seats':[{key:vote.get(key) for key in ('voter_id','response_status','decision','error_code','outcome_unknown')} for vote in audit['votes']]}


if __name__=='__main__':
    repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');config=load_config();voters=_configured_validators(config)
    if config['mode']!='shadow' or config['ontology_extension']['auto_publish_enabled']:raise RuntimeError('shadow policy required')
    state={'status':'running','pid':os.getpid(),'started_at_epoch':time.time(),'selected':len(IDS),'processed':0,'results':[]}
    write_status_snapshot(OUT/'status.json',state)
    for identity in IDS:
        state['active']=identity;write_status_snapshot(OUT/'status.json',state)
        try:result=review(repo,config,voters,identity)
        except Exception as exc:result={'extension_id':identity,'status':'review_error','error':str(exc),'independent_review_completed':False}
        state['results'].append(result);state['processed']+=1;write_status_snapshot(OUT/'status.json',state)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if not result.get('independent_review_completed'):
            state.update(status='pilot_blocked',active=None,finished_at_epoch=time.time());break
    else:state.update(status='completed',active=None,finished_at_epoch=time.time())
    write_status_snapshot(OUT/'status.json',state)
