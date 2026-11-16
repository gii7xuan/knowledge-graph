"""Prepare, verify and execute one isolated, explicitly budgeted agent-resolution experiment."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import json
import time
from zipfile import ZipFile,ZIP_DEFLATED

from evaluation.frozen_regression import read,save,prepare as prepare_diagnostic
from evaluation.review_samples import ReadOnlyRepository
from evaluation.review_quality import bind_packet,annotation_template,quality_report
from review_agent.batch import run_review_batch
from review_agent.contracts import sha256_json
from review_agent.repository import ReviewRepository
from runs.regress_v8 import ROOT,DATA,configuration,public_settings,fingerprints
from runs.revalidate_v8 import digest

VERSION='isolated_agent_resolution_experiment_v1'


def prepare(directory,*,limit=3,data_path=DATA):
    directory=Path(directory).resolve()
    if type(limit) is not int or not 1<=limit<=30:raise ValueError('select 1..30 fixed development cases')
    initial=prepare_diagnostic(directory,data_path)
    bindings=read(directory/'sample_bindings.json')[:limit]
    source=Path(read(Path(data_path))['source_database']).resolve()
    config=configuration(profile='agent_resolution',directory=directory)
    settings=public_settings(config)
    target=ReviewRepository(directory/'review.sqlite3')
    for b in bindings:target.ensure_case(b['candidate_id'],config['case_budget'])
    frozen={**initial,'version':VERSION,'population':len(bindings),
        'candidate_hashes':[b['candidate_content_hash'] for b in bindings],
        'sample_bindings_hash':sha256_json(bindings),'settings_hash':sha256_json(settings),
        'selection_policy':'first N original frozen bindings, before observing new outcomes',
        'execution_policy':'one initial audit plus at most one reviewed semantic revision; all split children independently rechecked',
        'limits':{'calls_per_root_case':config['case_budget']['llm_calls'],
            'total_calls_upper_bound':len(bindings)*config['case_budget']['llm_calls'],
            'tokens_per_root_case':config['case_budget']['tokens'],'tool_calls_per_audit':config['budget']['max_tool_calls'],
            'semantic_revision_versions_per_root':1,'reaudit_jobs_per_root':8,'automatic_relaunches':0,'production_written':False},
        'profile':settings['experiment_profile'],'source_database':str(source),'source_database_sha256':digest(source)}
    frozen.pop('freeze_hash',None);frozen['freeze_hash']=sha256_json(frozen)
    manifest={'version':'isolated_shadow_revision_v1','source':str(source),'source_sha256':digest(source),
        'database':str(directory/'review.sqlite3'),'allowed_root':str(ROOT/'outputs'),'mode':'isolated_shadow',
        'max_semantic_revision_rounds':1,'revision_scope':'per_candidate','production_written':False}
    for name,value in [('diagnostic_preparation_only.json',initial),('experiment.json',frozen),
        ('sample_bindings.json',bindings),('settings.json',settings),('shadow_manifest.json',manifest),
        ('status.json',{'status':'prepared','model_calls':0,'population':len(bindings)})]:save(directory,name,value)
    return frozen


def verify(directory):
    directory=Path(directory).resolve();frozen=read(directory/'experiment.json')
    if frozen.get('version')!=VERSION or frozen['freeze_hash']!=sha256_json({k:v for k,v in frozen.items() if k!='freeze_hash'}):raise ValueError('experiment binding changed')
    config=configuration(profile='agent_resolution',directory=directory)
    if frozen['settings_hash']!=sha256_json(public_settings(config)) or read(directory/'settings.json')!=public_settings(config):raise ValueError('effective profile changed')
    if fingerprints()!=read(directory/'code_fingerprints.json') or sha256_json(fingerprints())!=frozen['code_hash']:raise ValueError('execution code changed after preparation')
    bindings=read(directory/'sample_bindings.json')
    if sha256_json(bindings)!=frozen['sample_bindings_hash']:raise ValueError('population changed')
    repository=ReadOnlyRepository(directory/'review.sqlite3')
    for binding in bindings:
        if sha256_json(repository.get_revision(binding['candidate_id'],binding['revision']))!=binding['candidate_content_hash']:raise ValueError('frozen original candidate changed')
    for path,expected in frozen['assets'].items():
        if digest(Path(path))!=expected:raise ValueError('source or approved ontology changed')
    for path,expected in read(directory/'preserved_inputs.json')['sha256'].items():
        if digest(ROOT/path)!=expected:raise ValueError('historical input changed')
    if digest(Path(frozen['source_database']))!=frozen['source_database_sha256']:raise ValueError('source database changed')
    return frozen,bindings


def execute(directory):
    directory=Path(directory).resolve();frozen,bindings=verify(directory)
    with (directory/'started.json').open('x',encoding='utf-8') as stream:
        json.dump({'freeze_hash':frozen['freeze_hash'],'automatic_relaunch':False,'started_at_epoch':time.time()},stream)
    with ZipFile(directory/'execution_code_snapshot.zip','x',ZIP_DEFLATED) as archive:
        for name in read(directory/'code_fingerprints.json'):archive.write(ROOT/name,name)
    repository=ReviewRepository(directory/'review.sqlite3');config=configuration(profile='agent_resolution',directory=directory)
    run_id=repository.get_revision(bindings[0]['candidate_id'])['run_id']
    save(directory,'status.json',{'status':'running','stage':'initial','population':len(bindings)})
    try:
        result=run_review_batch(repository,run_id,config,candidate_ids=[b['candidate_id'] for b in bindings],status_file=directory/'progress.json')
        save(directory,'initial_batch.json',result)
        drain_reaudits(repository,run_id,config,directory,max_jobs=len(bindings)*frozen['limits']['reaudit_jobs_per_root'],
                       root_candidate_ids={b['candidate_id'] for b in bindings})
        verify(directory)
        report=seal(directory)
        save(directory,'status.json',{'status':'processing_completed','semantic_completed':report['semantic_completed'],
            'technical_incomplete':report['technical_incomplete'],'quality_evaluable':False,
            'completed_at_epoch':time.time(),
            'production_written':False,'automatic_relaunch':False})
    except BaseException as exc:
        save(directory,'status.json',{'status':'stopped','reason':type(exc).__name__,'automatic_relaunch':False})
        raise
    return read(directory/'status.json')


def drain_reaudits(repository,run_id,config,directory,*,max_jobs,root_candidate_ids=None):
    """Consume new revision/rebind jobs, including jobs created by a prior re-audit."""
    owner='isolated_resolution_runner';processed=[]
    while True:
        with repository.connect() as c:
            rows=c.execute("SELECT job_id,payload_json FROM jobs WHERE status='queued' AND job_type='audit' ORDER BY created_at,job_id").fetchall()
        if not rows:return processed
        if len(processed)>=max_jobs:raise ValueError('bounded re-audit job limit reached; remaining jobs require technical review')
        identity,payload_json=rows[0];payload=json.loads(payload_json)
        if (payload.get('run_id')!=run_id or not payload.get('candidate_ids') or
                not any(payload.get(k) for k in ('revision_draft_id','split_group_id','ontology_rebind')) or
                any(repository.get_revision(cid)['run_id']!=run_id for cid in payload['candidate_ids'])):
            raise ValueError('unexpected unrelated job in isolated database')
        if root_candidate_ids is not None:
            for cid in payload['candidate_ids']:
                candidate=repository.get_revision(cid)
                root=(candidate.get('split_origin') or {}).get('parent_candidate_id',cid)
                if root not in root_candidate_ids:raise ValueError('re-audit candidate is outside the frozen root population')
        job=repository.claim_job(owner,lease_seconds=1800,job_types=['audit'],job_id=identity)
        if not job:raise ValueError('queued re-audit is paused or claimed; explicit recovery required')
        child_config=deepcopy(config)
        child_config['agent_resolution']['final_review_only']=not bool(payload.get('ontology_rebind'))
        child=run_review_batch(repository,run_id,child_config,candidate_ids=payload['candidate_ids'],
            status_file=Path(directory)/('reaudit_'+identity+'.json'),job_id=identity,
            job_binding={'job_id':identity,'owner':owner,'lease_token':job['lease_token']},
            on_progress=lambda p:repository.heartbeat_job(identity,p,lease_seconds=1800,owner=owner,lease_token=job['lease_token']))
        repository.complete_job(identity,child,failed=bool(child.get('failed')),owner=owner,lease_token=job['lease_token'])
        processed.append(identity)


def revision_review_materials(repository,root_ids,final_packets,*,historical_applied_draft_ids=()):
    """Freeze before/after material once per applied root operation, including all split children."""
    with repository.connect() as connection:
        drafts=[json.loads(r[0]) for r in connection.execute("SELECT draft_json FROM revision_drafts WHERE status='applied'")]
    indexed={p['candidate']['candidate_id']:p for p in final_packets};operations={};packets={}
    for draft in drafts:
        if draft['draft_id'] in historical_applied_draft_ids:continue
        cid=draft['candidate_id'];aid=draft.get('source_audit_id')
        if cid not in root_ids or not aid:continue
        audit=repository.get_audit(cid,aid)
        if not audit['context'].get('resolution_snapshots_enabled'):continue # Exclude copied historical ancestry.
        group=(draft.get('split_context') or {}).get('group_id');identity=group or draft['draft_id']
        if identity in operations:continue
        children=[d['after']['candidate_id'] for d in drafts if (d.get('split_context') or {}).get('group_id')==group] if group else [cid]
        if not all(child in indexed for child in children):raise ValueError('applied revision requires a report packet for every child')
        proposal=next(p for p in reversed(audit['rounds']) if p['candidate']['revision']==draft['base_revision'])
        parent=bind_packet(draft['before'],proposal,draft['evidence_bundle'],proposal.get('review_scope_policy') or {},
                           system={'comparison_role':'before_applied_shadow_revision'})
        packets[parent['packet_id']]=parent
        for child in children:packets[indexed[child]['packet_id']]=indexed[child]
        operations[identity]={'revision_id':identity,'mode':'isolated_shadow','root_candidate_id':cid,
            'parent_packet_id':parent['packet_id'],'child_packet_ids':[indexed[child]['packet_id'] for child in children]}
    return list(packets.values()),list(operations.values())


def seal(directory,*,annotations=(),work_logs=(),revision_annotations=(),revision_assessments=()):
    from review_agent.resolution_status import technical_tasks,classify
    from evaluation.continue_regression import physical_calls
    from evaluation.resolution_metrics import sampling_plan
    directory=Path(directory).resolve();frozen,bindings=verify(directory)
    repository=ReadOnlyRepository(directory/'review.sqlite3');rows=[];packets=[];root_technical={}
    for binding in bindings:
        parent=repository.get_candidate(binding['candidate_id'])
        with repository.connect() as c:
            children=[r[0] for r in c.execute("SELECT c.candidate_id FROM candidates c JOIN candidate_revisions r ON "
                "r.candidate_id=c.candidate_id AND r.revision=c.current_revision WHERE json_extract(r.candidate_json,'$.split_origin.parent_candidate_id')=?",(parent['candidate_id'],))]
        ids=children or [parent['candidate_id']]
        with repository.connect() as c:
            audits=[r[0] for r in c.execute('SELECT audit_id FROM audit_runs WHERE candidate_id IN ('+
                ','.join('?' for _ in set(ids+[parent['candidate_id']]))+')',sorted(set(ids+[parent['candidate_id']])))]
        root_technical[parent['candidate_id']]=[task for aid in audits for task in technical_tasks(repository,aid)]
        for cid in ids:
            decision=repository.latest_decision(cid) or {}
            outcome=deepcopy(decision.get('review_outcome') or {})
            from review_agent.repository import NotFoundError
            try:audit=repository.get_audit(cid,decision.get('audit_id'))
            except NotFoundError:audit=None
            current_revision=repository.get_revision(cid)['revision']
            if not audit or audit['revision']!=current_revision or not audit['rounds']:
                system={'auto_admitted':False,'prediction_observed':False,'recommendation':None,'review_completed':False,
                    'semantic_status':'unresolved','technical_status':'pending','technical_incomplete':True,
                    'end_to_end_incomplete':True,'required_human_action':'technical_recovery'}
                packet=bind_packet(repository.get_revision(cid),{}, {'items':[]},read(directory/'settings.json').get('scope_policy') or {},system=system)
                packet['sample_id']=binding['sample_id'];packets.append(packet)
                rows.append({'candidate_id':cid,'root_candidate_id':parent['candidate_id'],**system})
                root_technical[parent['candidate_id']].append({'task_type':'candidate_audit','candidate_id':cid,
                    'request_state':'not_started','execution_unknown':False,'usage_unknown':False,
                    'required_action':'audit_not_started','automatic_resend':False})
                continue
            from review_agent.resolution_recovery import project_context
            from review_agent.review_observability import outcome as derive_outcome
            from runs.revalidate_v8 import committee
            proposal=next((p for p in audit['rounds'] if p['proposal_digest']==decision.get('selected_proposal_digest')),audit['rounds'][-1])
            roster=committee(proposal)
            context=project_context(repository,audit,roster)
            votes=[v for v in audit['votes'] if v['proposal_digest']==proposal['proposal_digest']]
            outcome=derive_outcome(proposal,votes,roster,audit_status=audit['status'],context=context,decision=decision)
            tasks=context.get('technical_recovery_tasks',technical_tasks(repository,audit['audit_id']))
            outcome.update(classify(outcome,tasks))
            for task in outcome['technical_recovery_tasks']:
                if task not in root_technical[parent['candidate_id']]:root_technical[parent['candidate_id']].append(task)
            if proposal:
                effective=deepcopy(proposal)
                if context.get('resolution_snapshot'):
                    effective['ontology_context']=context.get('resolution_ontology_context') or proposal.get('ontology_context')
                    effective['ontology_context_hash']=sha256_json(effective['ontology_context'])
                system={k:outcome.get(k) for k in ('recommendation','review_completed','semantic_status','technical_status',
                    'technical_incomplete','end_to_end_incomplete','required_human_action')}
                system.update(auto_admitted=False,prediction_observed=True,
                    source_support_result=outcome['dimension_results']['source']['status'],
                    mapping_completed=outcome['dimension_results']['mapping']['status']=='valid' and bool(proposal['candidate']['triple'].get('relation_id')),
                    revision_requested=outcome.get('recommendation')=='revise',
                    error_category=','.join(sorted({i['code'] for i in context.get('issue_history',[]) if i.get('status','open')!='resolved'})) or None)
                system.update(auto_admitted=False,prediction_observed=True,source_support_result=outcome['dimension_results']['source']['status'],
                              mapping_completed=outcome['dimension_results']['mapping']['status']=='valid',revision_requested=outcome['recommendation']=='revise')
                packet=bind_packet(proposal['candidate'],effective,context.get('resolution_bundle') or proposal['evidence_bundle'],
                    proposal.get('review_scope_policy') or {},system=system,resolution_snapshot=context.get('resolution_snapshot'))
                packet['sample_id']=binding['sample_id'];packets.append(packet)
            rows.append({'candidate_id':cid,'root_candidate_id':parent['candidate_id'],**outcome})
    with repository.connect() as c:
        attempts=[dict(r) for r in c.execute("SELECT state,metadata_json,result_json FROM request_attempts WHERE state!='not_sent'")
                  if json.loads(r['result_json'] or '{}').get('actual_calls')!=0]
    costs={'physical_model_calls':physical_calls(repository),'by_task_type':dict(Counter(json.loads(r['metadata_json']).get('task_type') for r in attempts)),
        'reported_tokens':{k:sum(json.loads(r['result_json'] or '{}').get(k) or 0 for r in attempts) for k in ('input_tokens','output_tokens')},
        'usage_missing_requests':sum(any(type(json.loads(r['result_json'] or '{}').get(k)) is not int for k in ('input_tokens','output_tokens')) for r in attempts)}
    by_model={}
    for attempt in attempts:
        meta=json.loads(attempt['metadata_json']);result=json.loads(attempt['result_json'] or '{}')
        identity=(meta.get('binding') or {}).get('model_id') or result.get('model_id') or 'unrecorded'
        model=by_model.setdefault(identity,{'physical_requests':0,'unknown_execution':0,'failed_responses':0,'usage_missing':0,'error_codes':Counter()})
        model['physical_requests']+=1;model['unknown_execution']+=attempt['state'] in {'running','unknown'}
        model['failed_responses']+=attempt['state']=='failed'
        model['usage_missing']+=any(type(result.get(k)) is not int for k in ('input_tokens','output_tokens'))
        if result.get('error_code'):model['error_codes'][result['error_code']]+=1
    costs['by_model']=by_model
    phases=Counter()
    for attempt in attempts:
        meta=json.loads(attempt['metadata_json']);kind=meta.get('task_type');p=meta.get('proposal') or {}
        phase=('preprocessing' if kind=='planner_action' else 'revision_review' if kind=='candidate_revision_review' else
            'final_review' if kind=='triple_review' and (p.get('candidate',{}).get('revision_provenance') or p.get('candidate',{}).get('split_origin')) else
            'initial_review' if kind=='triple_review' else 'evidence_dispute')
        phases[phase]+=1
    costs['physical_calls_by_phase']=dict(phases)
    costs['elapsed_seconds']=None
    if (directory/'started.json').exists():
        started=read(directory/'started.json').get('started_at_epoch')
        if started:costs['elapsed_seconds']=max(0,read(directory/'status.json').get('completed_at_epoch',time.time())-started)
    assert costs['physical_model_calls']<=frozen['limits']['total_calls_upper_bound']
    roots=[b['candidate_id'] for b in bindings]
    complete=sum(bool([r for r in rows if r['root_candidate_id']==cid]) and all(r.get('semantic_status')=='completed'
        for r in rows if r['root_candidate_id']==cid) for cid in roots)
    quality=quality_report(packets,annotations,work_logs=work_logs,execution_costs=costs)
    revision_packets,operations=revision_review_materials(repository,set(roots),packets,
        historical_applied_draft_ids=read(directory/'ancestry.json')['historical_applied_draft_ids'])
    revision_quality=quality_report(revision_packets,revision_annotations,revision_operations=operations,
        revision_assessments=revision_assessments,shadow_revision_operations=operations,shadow_revision_assessments=revision_assessments)
    quality['isolated_revision_evaluation']={'applied_root_operations':len(operations),'report':'revision_quality_report.json',
        'parent_and_child_packets':'revision_review_packets.json','root_population_not_inflated':True}
    if operations:
        next(m for m in quality['metrics'] if m['id']=='revision_success_rate')['reason']='影子修订按原候选单列于 revision_quality_report.json，父版本不加入主报告分母'
    report={'version':VERSION,'root_population':len(roots),'evaluated_child_or_original_population':len(rows),
        'semantic_completed':complete,'technical_incomplete':sum(bool(root_technical[cid]) for cid in roots),
        'root_automatic_closure_rate':complete/len(roots) if roots else None,
        'root_technical_recovery_tasks':root_technical,
        'cases':rows,'costs':costs,'human_labels':quality['human_resolved'],
        'applied_root_revision_operations':len(operations),
        'actual_human_minutes':quality['resolution_metrics']['human_work']['actual_minutes'],
        'quality_evaluable':bool(quality['human_resolved']),'production_written':False,'source_database_unchanged':True}
    for name,value in [('resolution_report.json',report),('review_packets.json',{'samples':packets}),
        ('annotation_template.json',{'annotations':[annotation_template(p) for p in packets]}),
        ('quality_report.json',quality),('quality_sampling_plan.json',sampling_plan(packets)),
        ('revision_review_packets.json',{'samples':revision_packets}),('revision_operations.json',operations),
        ('revision_annotation_template.json',{'annotations':[annotation_template(p) for p in revision_packets]}),
        ('revision_assessment_template.json',{'assessments':[dict(o,annotated_by='human',reviewer=None,annotated_at=None,
            original_issue_resolved=None,introduced_new_problem=None) for o in operations]}),
        ('revision_quality_report.json',revision_quality)]:save(directory,name,value)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','verify','run','report']);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--limit',type=int,default=3)
    parser.add_argument('--annotations',type=Path);parser.add_argument('--work-log',type=Path)
    parser.add_argument('--revision-annotations',type=Path);parser.add_argument('--revision-assessments',type=Path);args=parser.parse_args()
    if args.action=='prepare':result=prepare(args.output,limit=args.limit)
    elif args.action=='verify':result={'verified':True,'freeze_hash':verify(args.output)[0]['freeze_hash']}
    elif args.action=='run':result=execute(args.output)
    else:
        labels=read(args.annotations) if args.annotations else []
        work=read(args.work_log) if args.work_log else []
        revision_labels=read(args.revision_annotations) if args.revision_annotations else []
        revision_truth=read(args.revision_assessments) if args.revision_assessments else []
        result=seal(args.output,annotations=labels.get('annotations',[]) if isinstance(labels,dict) else labels,
                    work_logs=work.get('entries',[]) if isinstance(work,dict) else work,
                    revision_annotations=revision_labels.get('annotations',[]) if isinstance(revision_labels,dict) else revision_labels,
                    revision_assessments=revision_truth.get('assessments',[]) if isinstance(revision_truth,dict) else revision_truth)
    print(json.dumps({k:result.get(k) for k in ('status','population','limits','freeze_hash','semantic_completed') if k in result},ensure_ascii=False))


if __name__=='__main__':main()
