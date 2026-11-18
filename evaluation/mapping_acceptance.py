"""Three source-bound engineering fixtures on an isolated frozen ontology.

These fixtures have authored expected behaviors, not independent human gold labels.
They test the current real reviewer pipeline and cannot estimate paper accuracy.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .freeze_shared_ontology import ROOT,SOURCE,read,write
from review_agent.contracts import sha256_json,fact_key_for

# Fixed before reading mapping outcomes; no search for a model-approved answer.
PREFERENCES=[('R_198245964490791fcd11','平行于','asserted'),
             ('R_406bc04a6711460b8da5','等同于','can')]


def fixture_candidates(document,release,materials,original,run_id):
    from extract_triplets.candidate_contract import normalize_candidate
    from extract_triplets.pipeline_v4 import source_scope
    from ontologyBuild.ontology_adapter import iter_classes,iter_object_properties
    from schema.fact_identity import relation_semantic_hash
    classes={c['id']:c for c in iter_classes(release.ontology)}
    props={p['id']:p for p in iter_object_properties(release.ontology)}
    selected=next((x for x in PREFERENCES if x[0] in props),None)
    if selected is None:raise ValueError('neither predeclared clean fixture relation passed the annotation audit')
    relation_id,bad_surface,modality=selected;prop=props[relation_id]
    row=next(r for r in materials['definitions'] if r['relation_id']==relation_id)
    anchor=next(a for s in row['sources'] for a in s['anchors'])
    chunk=next(c for c in document['chunks'] if c['document_start']<=anchor['start']<c['document_end'])
    scope=source_scope(document,chunk,start=anchor['start'],end=anchor['end'],neighbors=3)
    raw={'head':classes[prop['domain'][0]]['label'],'surface_relation':prop['label'],
         'tail':classes[prop['range'][0]]['label'],'head_type_candidate':prop['domain'][0],
         'tail_type_candidate':prop['range'][0],'relation_id_candidate':relation_id,
         'schema_hint':'matched_local_schema','tail_kind':'entity','language':'zh',
         'qualifiers':{k:None for k in ('polarity','modality','condition','time_scope','applicability','quantifier')},
         'qualifier_status':{k:'not_stated' for k in ('polarity','modality','condition','time_scope','applicability','quantifier')},
         'evidence_quotes':[anchor['quote']]}
    raw['qualifiers'].update(polarity='positive',modality=modality)
    raw['qualifier_status'].update(polarity='extracted',modality='extracted')
    result=[]
    for ordinal,(name,surface) in enumerate([('positive_mapping',prop['label']),('semantic_mismatch',bad_surface)],1):
        item=deepcopy(raw);item['surface_relation']=surface
        candidate=normalize_candidate(item,index=document,scope=scope,ontology=release.ontology,
            release_binding=release.binding(),run_id=run_id,ordinal=ordinal,prompt_hash=sha256_json(['engineering_fixture_v1',name]))
        # Explicit fixture input, never an automatic semantic mapping action.
        candidate['triple'].update(relation_id=relation_id,relation_label=prop['label'],mapping_status='fixture_supplied',
                                   relation_semantic_hash=relation_semantic_hash(release.ontology,relation_id))
        candidate.update(fact_key_state='canonical',extraction_model='authored_engineering_fixture',
                         fixture_kind=name,independent_human_gold=False)
        candidate['fact_key']=fact_key_for(candidate)
        result.append((name,candidate))
    raw={k:deepcopy(original['triple'].get(k)) for k in ('head','tail','surface_relation','qualifiers','qualifier_status','tail_kind','language')}
    raw.update(evidence_quotes=original['evidence_quotes'],head_type_candidate=original['triple']['head_type'],
               tail_type_candidate=original['triple']['tail_type'],relation_id_candidate=None,schema_hint='needs_full_schema_lookup')
    candidate=normalize_candidate(raw,index=document,scope=original['source_scope'],ontology=release.ontology,
        release_binding=release.binding(),run_id=run_id,ordinal=3,prompt_hash=sha256_json(['engineering_fixture_v1','missing_material']))
    candidate.update(fixture_kind='missing_material',independent_human_gold=False,
                     engineering_fixture_parent={'candidate_id':original['candidate_id'],'revision':original['revision'],'hash':sha256_json(original)})
    result.append(('missing_material',candidate));return result


def acceptance(name,result,audit,candidate):
    proposal=audit['rounds'][-1]
    votes=[v for v in audit['votes'] if v.get('proposal_digest')==proposal['proposal_digest']]
    valid=[v for v in votes if v.get('response_status')=='ok']
    summaries=[{'voter_id':v.get('voter_id'),'model_family':v.get('model_family'),
        'response_status':v.get('response_status'),'source':v.get('dimensions',{}).get('triple_support'),
        'relation_semantics':v.get('dimensions',{}).get('relation_semantics'),
        'mapping':v.get('dimensions',{}).get('mapping')} for v in votes]
    statuses={a:d['status'] for a,d in result['review_outcome']['dimension_results'].items()}
    diverse=len(valid)==3 and len({v['model_family'] for v in valid})==3
    ready=proposal.get('mapping_preflight',{}).get('judgment_ready')
    if name=='positive_mapping':
        passed=diverse and ready is True and statuses['mapping']=='valid' and all(v['dimensions']['mapping']['status']=='matched' for v in valid)
        expectation='Complete real metadata and source-grounded predicate/type mapping are recognized by all three reviewers.'
    elif name=='semantic_mismatch':
        caught=[v for v in valid if v['dimensions']['mapping']['status']=='candidate_mapping_error'
                or v['dimensions']['relation_semantics']['status']=='fail']
        passed=ready is True and len({v['model_family'] for v in caught})>=2 and result.get('research_operation')!='accept'
        expectation='Structural compatibility alone cannot admit a deliberately wrong predicate; at least two families identify its semantic mismatch.'
    else:
        passed=diverse and ready is False and candidate['triple']['relation_id'] is None and statuses['mapping']!='valid'
        expectation='Missing exact relation/tail type stays incomplete; no forced formal assignment.'
    return {'fixture':name,'passed':bool(passed),'expectation':expectation,'dimension_statuses':statuses,
            'mapping_preflight_ready':ready,'research_operation':result.get('research_operation'),'votes':summaries,
            'weight_snapshot_id':result.get('weight_snapshot',{}).get('snapshot_id'),
            'human_gold':False,'paper_accuracy_evaluable':False}


def main():
    from ontologyBuild.release_service import load_release
    from review_agent.repository import ReviewRepository
    from evaluation.review_samples import ReadOnlyRepository
    from document.hierarchy_index import load_document_index
    from review_agent.ontology_guard import OntologyGuard,load_snapshot
    from review_agent.orchestrator import ReviewOrchestrator
    from review_agent.cli import _configured_validators
    from runs.regress_v8 import configuration,public_settings
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--run',action='store_true');args=parser.parse_args()
    output=args.output.resolve();frozen=args.freeze.resolve()
    if not output.is_relative_to(ROOT/'outputs') or output.exists():raise ValueError('use a new isolated outputs directory')
    release=load_release(read(frozen/'frozen_release.json')['path']);base=read(frozen/'preparation.json')
    audit_result=read(frozen/'audit_result.json')
    if len(audit_result['new_requests'])+9>base['max_new_calls']:raise ValueError('cumulative preparation request cap would be exceeded')
    prior_known_tokens=audit_result['known_input_tokens']+audit_result['known_output_tokens']
    if prior_known_tokens>=base['max_known_tokens']:raise ValueError('known preparation token limit already reached')
    original=read(SOURCE/'contract_recovery_completed_candidate.json')
    source=ReadOnlyRepository(SOURCE/'review.sqlite3');original_run=source.get_run(original['run_id'])
    source_hash=hashlib.sha256(source.database_path.read_bytes()).hexdigest()
    document=load_document_index(original_run['document_path'],original['source_index_hash'],require_verified=True)
    run_id='mapping_acceptance_'+output.name
    candidates=fixture_candidates(document,release,read(SOURCE/'definition_materials.json'),original,run_id)
    output.mkdir(parents=True)
    config=configuration(profile='weighted_diagnostic',directory=output)
    config['planner'].update(enabled=False,strategy='deterministic')
    config['mapping_preflight']={'enabled':True};config['consensus']['max_rounds']=1
    config['budget'].update(max_llm_calls_per_candidate=3,max_retries_per_request=0,max_rounds=1,
        total_timeout_seconds=240,per_call_timeout_seconds=180,max_total_tokens=150000)
    config['case_budget'].update(llm_calls=3,tokens=150000)
    repo=ReviewRepository(output/'review.sqlite3');repo.initialize()
    repo.create_run({**original_run,**release.binding(),'run_id':run_id,'mode':'shadow',
        'ontology_path':str(release.snapshot_path),'ontology_hash':release.manifest['ontology_version'],'experiment_id':output.name})
    guard=OntologyGuard(load_snapshot(release.snapshot_path));guard.require_explicit_mapping=True;guard.require_relation_definition=True
    conflict=deepcopy(candidates[0][1]);conflict['triple']['tail_type']=conflict['triple']['head_type']
    structural_conflict=guard.check(conflict)
    for _,candidate in candidates:repo.add_candidate(candidate)
    write(output/'fixtures.json',{'version':'mapping_acceptance_fixtures_v1','selection_order':PREFERENCES,
        'scope':'engineering_contract_checks_not_paper_evaluation','human_gold':False,'candidates':dict(candidates),
        'guard_results':{n:guard.check(c) for n,c in candidates},'release':release.binding(),
        'additional_type_conflict':{'candidate':conflict,'guard':structural_conflict,'model_calls':0}})
    write(output/'settings.json',{**public_settings(config),'vote_weighting':config['vote_weighting'],'mapping_preflight':config['mapping_preflight']})
    files=[Path(__file__),ROOT/'evaluation/freeze_shared_ontology.py',ROOT/'review_agent/orchestrator.py',
           ROOT/'review_agent/validators.py',ROOT/'review_agent/request_worker.py']
    write(output/'code_fingerprints.json',{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    if not args.run:print('prepared_only');return
    write(output/'started.json',{'automatic_relaunch':False,'max_new_calls':9,'ontology_preparation_calls':len(audit_result['new_requests'])})
    owner=ReviewOrchestrator(repo,guard,_configured_validators(config),config=config)
    rows=[]
    for name,candidate in candidates:
        with repo.connect() as connection:
            known=sum(sum(json.loads(r[0]or'{}').get(k)or 0 for k in ('input_tokens','output_tokens'))
                      for r in connection.execute('SELECT result_json FROM request_attempts'))
        if known+prior_known_tokens>=base['max_known_tokens']:raise ValueError('known preparation token stop reached; do not dispatch additional fixtures')
        result=owner.audit_candidate(candidate['candidate_id'],1,document)
        audit=repo.get_audit(candidate['candidate_id'],result['audit_id'])
        write(output/(name+'_result.json'),result);write(output/(name+'_audit.json'),audit)
        row=acceptance(name,result,audit,candidate);rows.append(row)
        print(json.dumps({'fixture':name,'passed':row['passed'],'dimensions':row['dimension_statuses']},ensure_ascii=False),flush=True)
    with repo.connect() as connection:
        requests=[dict(r) for r in connection.execute('SELECT attempt_id,state,result_json FROM request_attempts')]
        outbox=connection.execute('SELECT COUNT(*) FROM import_outbox').fetchone()[0]
    details=[{'attempt_id':r['attempt_id'],'state':r['state'],**{k:v.get(k) for k in ('input_tokens','output_tokens','response_status','error_code','client_request_id')}}
             for r in requests for v in [json.loads(r['result_json']or'{}')]]
    unchanged=hashlib.sha256(source.database_path.read_bytes()).hexdigest()==source_hash
    write(output/'acceptance_report.json',{'passed':all(r['passed'] for r in rows) and structural_conflict['hard_failure'],
        'fixtures':rows,'additional_structural_type_conflict_detected':structural_conflict['hard_failure'],'request_details':details,
        'known_input_tokens':sum(r.get('input_tokens')or 0 for r in details),'known_output_tokens':sum(r.get('output_tokens')or 0 for r in details),
        'cumulative_known_preparation_tokens':prior_known_tokens+sum((r.get('input_tokens')or 0)+(r.get('output_tokens')or 0) for r in details),
        'cumulative_preparation_requests':len(requests)+len(audit_result['new_requests']),'source_database_unchanged':unchanged,
        'isolated_import_outbox':outbox,'production_written':False,'human_gold':False,'paper_accuracy':None})
    if len(requests)>9 or not unchanged or outbox:raise ValueError('mapping isolation or request cap violated')


if __name__=='__main__':main()
