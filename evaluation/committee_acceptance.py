"""New-case, bounded complete-committee acceptance with a separate concurrency check."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
from zipfile import ZipFile,ZIP_DEFLATED

from .freeze_shared_ontology import ROOT,SOURCE,read,write
from .review_samples import ReadOnlyRepository
from .mapping_acceptance import acceptance
from review_agent.contracts import sha256_json,fact_key_for

PRIMARY='R_406bc04a6711460b8da5'
PARALLEL='R_f29f60e4559cc505f99b'
CASES=[('positive_mapping',PRIMARY,None,'can',1),
       ('semantic_mismatch',PRIMARY,'完全等同于','can',1),
       ('missing_material',PRIMARY,None,'can',1),
       ('parallel_positive_mapping',PARALLEL,None,'asserted',3)]


def signature(candidate):
    """Compare actual review content, not newly allocated candidate/run IDs."""
    return sha256_json({k:candidate.get(k) for k in ('document_id','document_version','source_scope','triple','evidence_quotes')})


def fixtures(document,release,materials,run_id):
    from ontologyBuild.ontology_adapter import iter_classes,iter_object_properties
    from extract_triplets.candidate_contract import normalize_candidate
    from extract_triplets.pipeline_v4 import source_scope
    from schema.fact_identity import relation_semantic_hash
    classes={c['id']:c for c in iter_classes(release.ontology)}
    props={p['id']:p for p in iter_object_properties(release.ontology)}
    definitions={r['relation_id']:r for r in materials['definitions']}
    result=[]
    for ordinal,(name,rid,changed_surface,modality,parallelism) in enumerate(CASES,1):
        prop=props[rid];row=definitions[rid]
        anchor=next(a for s in row['sources'] for a in s['anchors'])
        if document['source_snapshot']['canonical_text'][anchor['start']:anchor['end']]!=anchor['quote']:
            raise ValueError('fixture source anchor changed')
        chunk=next(c for c in document['chunks'] if c['document_start']<=anchor['start']<c['document_end'])
        scope=source_scope(document,chunk,start=anchor['start'],end=anchor['end'],neighbors=3)
        raw={'head':classes[prop['domain'][0]]['label'],'tail':classes[prop['range'][0]]['label'],
            'surface_relation':changed_surface or prop['label'],'head_type_candidate':prop['domain'][0],
            'tail_type_candidate':prop['range'][0],'relation_id_candidate':rid,'tail_kind':'entity','language':'zh',
            'evidence_quotes':[anchor['quote']],
            'qualifiers':{k:None for k in ('polarity','modality','condition','time_scope','applicability','quantifier')},
            'qualifier_status':{k:'not_stated' for k in ('polarity','modality','condition','time_scope','applicability','quantifier')}}
        raw['qualifiers'].update(polarity='positive',modality=modality)
        raw['qualifier_status'].update(polarity='extracted',modality='extracted')
        c=normalize_candidate(raw,index=document,scope=scope,ontology=release.ontology,release_binding=release.binding(),
            run_id=run_id,ordinal=ordinal,prompt_hash=sha256_json(['committee_engineering_fixtures_v1',name]))
        c['triple'].update(relation_id=rid,relation_label=prop['label'],mapping_status='fixture_supplied',
                          relation_semantic_hash=relation_semantic_hash(release.ontology,rid))
        c.update(fact_key_state='canonical',extraction_model='authored_engineering_fixture',independent_human_gold=False)
        if name=='missing_material':
            c['triple'].update(relation_id=None,relation_label=None,relation_semantic_hash=None,tail_type=None,mapping_status='unassigned')
            c['extraction_hints'].update(relation_id_candidate=None,tail_type_candidate=None)
            c['fact_key_state']='provisional'
        c['fact_key']=fact_key_for(c)
        if c['contract_issues']:raise ValueError('fixture has source/expression contract issues: '+str(c['contract_issues']))
        result.append({'name':name,'candidate':c,'max_parallel_validators':parallelism,
            'acceptance_kind':'positive_mapping' if name=='parallel_positive_mapping' else name,
            'source_task_signature':signature(c)})
    return result


def current_cost(repository):
    with repository.connect() as c:
        rows=c.execute('SELECT state,result_json FROM request_attempts').fetchall()
    return {'calls':len(rows),'known_tokens':sum(sum(json.loads(r['result_json']or'{}').get(k)or 0
            for k in ('input_tokens','output_tokens')) for r in rows),
            'unsettled':sum(r['state'] in {'running','unknown'} for r in rows)}


def project(repository,case,audit,votes):
    from review_agent.review_observability import outcome
    from review_agent.response_consistency import issues
    from review_agent.validators import _parse_content
    from runs.revalidate_v8 import committee
    p={k:v for k,v in audit['rounds'][-1].items() if k not in {'consensus','gaps'}}
    derived=outcome(p,votes,committee(p),audit_status='completed')
    result={'review_outcome':derived,'research_operation':derived.get('research_operation'),'weight_snapshot':derived.get('weight_snapshot',{})}
    row=acceptance(case['acceptance_kind'],result,{**audit,'votes':votes},case['candidate']);row['fixture']=case['name']
    row['max_parallel_validators']=case['max_parallel_validators']
    row['whole_committee_valid']=len(votes)==3 and all(v.get('response_status')=='ok' and
        derived['vote_validation'].get(v['voter_id'],{}).get('whole_vote_eligible') for v in votes)
    diagnostics=[]
    for v in votes:
        error=None
        try:
            if v.get('response_shape_projection') is not None:
                from review_agent.response_projection import effective_raw
                raw=effective_raw(v,p)
            else:raw=_parse_content(v.get('corrected_response') or v.get('raw_response'))
        except (ValueError,TypeError) as exc:raw=None;error=str(exc)
        diagnostics.append({'voter_id':v['voter_id'],'response_status':v['response_status'],'issues':issues(raw),
            'model_view_error':error,'shape_projection_applied':v.get('response_shape_projection') is not None})
    row['consistency']=diagnostics
    row['all_three_consistent']=row['whole_committee_valid'] and all(not d['issues'] and not d['model_view_error'] for d in diagnostics)
    snapshot=derived.get('weight_snapshot')or{}
    weight_checks={'mode':snapshot.get('mode'),'axes':{},'passed':snapshot.get('mode')=='dynamic'}
    for axis,x in snapshot.get('axes',{}).items():
        seats=x['seats'];total=sum(s['weight'] for s in seats.values())
        valid=(abs(total-1)<1e-9 and all(0<=s['weight']<=.49+1e-9 and
               (s['effective_weight']==s['weight'] if s['usable'] else s['effective_weight']==0) for s in seats.values()))
        weight_checks['axes'][axis]={'weights_sum':total,'passed':valid,
            'weights':{v:{k:s[k] for k in ('weight','effective_weight','reliability','adaptation','usable','opinion')} for v,s in seats.items()}}
        weight_checks['passed']&=valid
    if len(weight_checks['axes'])!=4:weight_checks['passed']=False
    row['weight_checks']=weight_checks
    expected={'source':'supported','expression':'standard','mapping':'valid','scope':'include'}
    if row['dimension_statuses']==expected:
        row['routing_check']=derived.get('research_operation')=='accept' or bool(derived.get('blocking_reasons'))
    else:row['routing_check']=derived.get('research_operation')!='accept'
    with repository.connect() as c:
        attempts=[dict(r) for r in c.execute('SELECT attempt_id,state,started_at,response_received_at,metadata_json,result_json FROM request_attempts')
                  if json.loads(r['metadata_json'])['binding'].get('task_id')==case['candidate']['candidate_id']]
    first={}
    for r in sorted(attempts,key=lambda x:x['started_at']):
        v=json.loads(r['metadata_json'])['binding']['voter_id'];first.setdefault(v,r)
    overlap=None
    if {'judge_a','judge_b'}<=first.keys():
        a,b=first['judge_a'],first['judge_b']
        overlap=max(0,min(a['response_received_at']or time.time(),b['response_received_at']or time.time())-max(a['started_at'],b['started_at']))
    row['qwen_gemma_request_overlap_seconds']=overlap
    row['scheduling_check']=overlap is not None and (overlap==0 if case['max_parallel_validators']==1 else overlap>0)
    return {'proposal':p,'votes':votes,'derived_outcome':derived,'engineering_acceptance':row,
            'original_audit_hash':sha256_json(audit),'original_decision_rewritten':False}


def recover_format(repository,case,audit,voters,config,remaining):
    """At most three study-wide field patches; substantive judgments never rerolled."""
    from review_agent.targeted_validation import prepare
    from review_agent.validators import judge_validator,model_request_count
    from review_agent.case_budget import CaseBudget
    p={k:v for k,v in audit['rounds'][-1].items() if k not in {'consensus','gaps'}}
    votes=deepcopy(audit['votes']);records=[]
    for i,old in enumerate(votes):
        if remaining<=0 or old.get('response_status') not in {'invalid_contract','invalid_json','invalid_reference'} or old.get('outcome_unknown'):continue
        raw,plan,blocked=prepare(old,p,p['correction_policy'])
        if blocked or not plan:
            records.append({'voter_id':old['voter_id'],'status':'no_safe_field_only_repair','reasons':blocked});continue
        cost=current_cost(repository)
        if cost['unsettled'] or cost['calls']>=15 or cost['known_tokens']>=600000:break
        candidate=case['candidate'];cid=candidate['candidate_id']
        repository.authorize_case_budget(cid,{'llm_calls':1,'tokens':54000},actor='codex_user_authorized',
            reason='One bounded known-field correction in user-authorized complete-committee engineering acceptance',
            authorization_key=config['request_cache_namespace']+':format:'+cid+':'+old['voter_id'])
        budget=CaseBudget({**config['budget'],'max_llm_calls_per_candidate':1,'total_timeout_seconds':120},repository,
            [repository.ensure_case(cid)],task_type='committee_format_correction',task_id=cid)
        if not budget.reserve_calls(1):break
        voter={**next(v for v in voters if v['id']==old['voter_id']),'case_ids':budget.case_ids,
            'correction_token_budget':max(0,600000-cost['known_tokens'])}
        new=judge_validator(voter,p,max_retries=0,per_call_timeout_seconds=120,repository=repository,deadline_epoch=budget.deadline_epoch)
        calls=model_request_count(new);budget.refund_calls(1-calls)
        for attempt in new.get('attempts')or[new]:budget.tokens(attempt)
        votes[i]=new;records.append({'voter_id':old['voter_id'],'status':new['response_status'],'actual_calls':calls})
        remaining-=calls
        if new.get('outcome_unknown') or new.get('pause_job'):break
    return votes,records,remaining


def main():
    from ontologyBuild.release_service import load_release
    from document.hierarchy_index import load_document_index
    from review_agent.repository import ReviewRepository
    from review_agent.orchestrator import ReviewOrchestrator
    from review_agent.ontology_guard import OntologyGuard,load_snapshot
    from review_agent.cli import _configured_validators
    from runs.regress_v8 import configuration,public_settings
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--run',action='store_true')
    args=parser.parse_args();out=args.output.resolve()
    if not out.is_relative_to(ROOT/'outputs') or out.exists():raise ValueError('new isolated output directory required')
    release=load_release(read(ROOT/'outputs/ontology_freeze_20261116/live/frozen_release.json')['path'])
    source=ReadOnlyRepository(SOURCE/'review.sqlite3');original=read(SOURCE/'contract_recovery_completed_candidate.json')
    source_run=source.get_run(original['run_id']);document=load_document_index(source_run['document_path'],original['source_index_hash'],require_verified=True)
    cases=fixtures(document,release,read(SOURCE/'definition_materials.json'),'committee_acceptance_'+out.name)
    historical=ROOT/'outputs/response_consistency_20261116/live/review.sqlite3'
    watched={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (source.database_path,historical)}
    with ReadOnlyRepository(historical).connect() as c:
        old_unknown=[json.loads(r[0])['proposal']['candidate'] for r in c.execute("SELECT metadata_json FROM request_attempts WHERE state='unknown'")]
    if {signature(c) for c in old_unknown}&{case['source_task_signature'] for case in cases}:
        raise ValueError('new fixture repeats an unresolved original source task')
    out.mkdir(parents=True);repo=ReviewRepository(out/'review.sqlite3');repo.initialize()
    repo.create_run({**source_run,**release.binding(),'run_id':cases[0]['candidate']['run_id'],'mode':'shadow',
        'ontology_path':str(release.snapshot_path),'ontology_hash':release.manifest['ontology_version'],'experiment_id':out.name})
    for case in cases:repo.add_candidate(case['candidate'])
    config=configuration(profile='weighted_diagnostic',directory=out)
    config['planner'].update(enabled=False,strategy='deterministic');config['mapping_preflight']={'enabled':True}
    config['consensus']['max_rounds']=1
    config['budget'].update(max_llm_calls_per_candidate=3,max_retries_per_request=0,max_rounds=1,
        total_timeout_seconds=600,per_call_timeout_seconds=240,max_total_tokens=160000,max_parallel_validators=1)
    config['case_budget'].update(llm_calls=3,tokens=160000)
    for slot in config['validators']:slot.update(request_timeout_seconds=240,http_timeout_seconds=240)
    voters=_configured_validators(config)
    if len(voters)!=3 or len({v['model_family'] for v in voters})!=3:raise ValueError('requires complete diverse committee')
    write(out/'fixtures.json',cases)
    write(out/'settings.json',{**public_settings(config),'vote_weighting':config['vote_weighting'],'mapping_preflight':config['mapping_preflight']})
    write(out/'experiment.json',{'kind':'complete_committee_new_case_engineering_acceptance','max_initial_requests':12,
        'max_requests_including_known_field_corrections':15,'max_field_corrections':3,'known_token_stop':600000,
        'release':release.binding(),'source_database_hashes':watched,'old_unknown_source_signatures':[signature(c) for c in old_unknown],
        'new_case_input_distinct_from_old_unknowns':True,'main_cases':'first three serial; fourth separate concurrent source task',
        'concurrency_causality_claim':False,'stop_expansion_on_any_unknown':True,'human_gold':False,'paper_accuracy':None,
        'test_partition_reliability_updates':False,'old_votes_reused':False,'production_written':False,
        'route_expectations':{'positive_mapping':'accept when all four axes and prerequisites are complete; otherwise retain actual blockers',
          'semantic_mismatch':'revise rather than admit when substantive error is established',
          'missing_material':'manual_review with no forced assignment'},'original_decisions_overwritten':False})
    files=[p for folder in ('review_agent','evaluation','evidence','document','schema','ontologyBuild','prompts','specs')
        for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.txt','.json'}]
    hashes={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    write(out/'code_fingerprints.json',hashes)
    with ZipFile(out/'execution_code.zip','x',ZIP_DEFLATED) as archive:
        for name in sorted(hashes):archive.write(ROOT/name,name)
    if not args.run:print('prepared_only',flush=True);return
    write(out/'started.json',{'automatic_relaunch':False,'max_model_requests':15,'unknown_resend':False})
    rows=[];remaining=3;stopped=None
    for case in cases:
        cost=current_cost(repo)
        if cost['unsettled'] or cost['known_tokens']>=600000 or cost['calls']+3>15:
            stopped='unsettled_request_or_budget_stop';break
        current=deepcopy(config);current['budget']['max_parallel_validators']=case['max_parallel_validators']
        write(out/(case['name']+'_execution_policy.json'),{'max_parallel_validators':case['max_parallel_validators'],
            'settings_hash':sha256_json({**public_settings(current),'vote_weighting':current['vote_weighting']}),
            'case_deadline_seconds':600,'per_call_deadline_seconds':240,'old_request_retry':False})
        owner=ReviewOrchestrator(repo,OntologyGuard(load_snapshot(release.snapshot_path)),voters,config=current)
        try:result=owner.audit_candidate(case['candidate']['candidate_id'],1,document)
        except Exception as exc:
            from review_agent.review_observability import redact
            write(out/(case['name']+'_execution_failure.json'),{'error_type':type(exc).__name__,'error':redact(str(exc))});stopped='execution_failure';break
        audit=repo.get_audit(case['candidate']['candidate_id'],result['audit_id'])
        write(out/(case['name']+'_result.json'),result);write(out/(case['name']+'_audit.json'),audit)
        merged=deepcopy(audit['votes']);corrections=[]
        if not current_cost(repo)['unsettled']:
            merged,corrections,remaining=recover_format(repo,case,audit,voters,current,remaining)
        write(out/(case['name']+'_corrections.json'),corrections)
        projected=project(repo,case,audit,merged);write(out/(case['name']+'_projection.json'),projected)
        row=projected['engineering_acceptance'];rows.append(row)
        print(json.dumps({'fixture':case['name'],'committee_valid':row['whole_committee_valid'],'passed':row['passed'],
            'operation':row['research_operation'],'dimensions':row['dimension_statuses'],
            'qwen_gemma_overlap_seconds':row['qwen_gemma_request_overlap_seconds']},ensure_ascii=False),flush=True)
        if current_cost(repo)['unsettled']:stopped='new_request_outcome_unknown';break
    cost=current_cost(repo);unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in watched.items())
    core=[r for r in rows if r['max_parallel_validators']==1]
    with repo.connect() as c:outbox=c.execute('SELECT COUNT(*) FROM import_outbox').fetchone()[0]
    report={'rows':rows,'core_three_case_gate_passed':len(core)==3 and all(r['passed'] and r['all_three_consistent'] and
        r['weight_checks']['passed'] and r['routing_check'] and r['scheduling_check'] for r in core),
        'positive_accept_path_demonstrated':any(r['fixture']=='positive_mapping' and r['research_operation']=='accept' for r in rows),
        'concurrent_case_completed':any(r['max_parallel_validators']==3 and r['whole_committee_valid'] for r in rows),
        'stop_reason':stopped,'calls':cost['calls'],'known_tokens':cost['known_tokens'],'unsettled_requests':cost['unsettled'],
        'source_databases_unchanged':unchanged,'isolated_import_outbox':outbox,'field_correction_calls':3-remaining,
        'old_unknown_requests_resent':0,'price':None,'unknown_cost_is_zero':False,'human_gold':False,'paper_accuracy':None,'production_written':False}
    write(out/'report.json',report)
    if not unchanged or outbox or cost['calls']>15:raise ValueError('study isolation or request cap failed')
    print(json.dumps({k:report[k] for k in ('core_three_case_gate_passed','positive_accept_path_demonstrated','concurrent_case_completed','calls','unsettled_requests','stop_reason')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
