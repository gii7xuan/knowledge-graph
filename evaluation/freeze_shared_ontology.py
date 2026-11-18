"""Audit restored definitions and freeze an isolated, machine-reviewed research subset."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from review_agent.contracts import sha256_json

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'outputs/review_completion_20261116/live'


def write(path,value):
    from review_agent.review_observability import redact
    with path.open('x',encoding='utf-8') as stream:json.dump(redact(value),stream,ensure_ascii=False,indent=2)


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def prepare(output):
    from document.hierarchy_index import load_document_index
    from evaluation.review_samples import ReadOnlyRepository
    from review_agent.shadow_revision import clone
    from ontologyBuild.release_service import load_release
    from ontology_review.definition_audit import packets
    from review_agent.cli import _configured_validators
    from runs.regress_v8 import configuration,public_settings
    source=ReadOnlyRepository(SOURCE/'review.sqlite3')
    candidate=read(SOURCE/'contract_recovery_completed_candidate.json')
    if sha256_json(source.get_revision(candidate['candidate_id']))!=sha256_json(candidate):raise ValueError('source candidate changed')
    original=source.get_run(candidate['run_id'])
    document=load_document_index(original['document_path'],candidate['source_index_hash'],require_verified=True)
    base=load_release(candidate['ontology_release_path'])
    materials=read(SOURCE/'definition_materials.json')
    if materials['ontology_hash']!=sha256_json(base.ontology):raise ValueError('base ontology mismatch')
    repository=clone(source.database_path,output,allowed_root=ROOT/'outputs');repository.initialize()
    config=configuration(profile='weighted_diagnostic',directory=output)
    config['budget'].update(max_llm_calls_per_candidate=48,max_total_tokens=500000,total_timeout_seconds=2400)
    voters=_configured_validators(config)
    members=[{k:v[k] for k in ('id','model_id','model_family','model_identity') if k in v} for v in voters]
    if len(voters)!=3 or len({v['model_family'] for v in voters})!=3:raise ValueError('requires three independent model families')
    proposals=packets(materials,base.ontology,document['source_snapshot']['canonical_text'],base.manifest['release_id'],members)
    source_hash=hashlib.sha256(source.database_path.read_bytes()).hexdigest()
    write(output/'preparation.json',{'source_database':str(source.database_path),'source_database_sha256':source_hash,
          'base_release':base.binding(),'materials_hash':sha256_json(materials),'definition_count':len(materials['definitions']),
          'audit_batches':len(proposals),'planned_audit_calls':len(proposals)*3,'max_new_calls':48,
          'max_known_tokens':500000,'unknown_resend':False,'human_labels_used':False,'production_written':False,
          'study_role':'engineering_preexperiment_only','classes_not_fully_semantically_recertified':True})
    write(output/'settings.json',{**public_settings(config),'vote_weighting':config['vote_weighting']})
    write(output/'definition_packets.json',proposals)
    from zipfile import ZipFile,ZIP_DEFLATED
    files=[p for folder in ('review_agent','ontology_review','ontologyBuild','evaluation','evidence','document','schema','prompts','specs')
           for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.json','.txt'}]
    fingerprints={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    write(output/'code_fingerprints.json',fingerprints)
    with ZipFile(output/'execution_code.zip','x',ZIP_DEFLATED) as archive:
        for name in sorted(fingerprints):archive.write(ROOT/name,name)
    return repository,candidate,document,base,materials,config,voters,proposals


def freeze(output,base,materials,rows,*,audit_name='definition_audit.json'):
    from rdflib import URIRef, Literal, SKOS
    from ontologyBuild.rdf_graph import parse_turtle
    from ontologyBuild.rdf_artifact import canonical_turtle
    from ontologyBuild.ontology_adapter import iter_classes,iter_object_properties
    from ontologyBuild.owl_to_project_schema import convert_turtle
    from ontologyBuild.providers.base import OntologyArtifact
    from evaluation.experiment import seed_catalog
    audit=read(output/audit_name)
    if (audit['rows']!=rows or audit['materials_hash']!=sha256_json(materials)
            or audit['base_release_id']!=base.manifest['release_id']):raise ValueError('aggregate freeze binding changed')
    approved={r['relation_id'] for r in rows if r['status']=='machine_approved'}
    if not approved:return None
    properties=list(iter_object_properties(base.ontology));classes=list(iter_classes(base.ontology))
    graph=parse_turtle((base.path/'ontology.ttl').read_text(encoding='utf-8'),preserve_lexical=True)
    removed={URIRef(p['iri']) for p in properties if p['id'] not in approved}
    for node in removed:graph.remove((node,None,None))
    if any(o in removed for _,_,o in graph):raise ValueError('retained schema references excluded predicate')
    for row in materials['definitions']:
        if row['relation_id'] in approved:graph.add((URIRef(row['iri']),SKOS.definition,Literal(row['definition'],lang='zh')))
    turtle=canonical_turtle(graph)
    old_method=read(base.path/'method_manifest.json')
    bindings={r['iri']:r['id'] for r in [*classes,*properties] if r['id'] in approved or r in classes}
    converted=convert_turtle(turtle,iri_id_map=bindings,base_iri=old_method['source_base_iri'])
    schema=converted['project_schema'];newprops={p['id']:p for p in iter_object_properties(schema)}
    if set(newprops)!=approved:raise ValueError('frozen property membership changed')
    for prop in properties:
        if prop['id'] in approved and any(newprops[prop['id']].get(k)!=prop.get(k) for k in ('label','domain','range')):
            raise ValueError('retained property signature changed')
    if {c['id']:c for c in iter_classes(schema)}!={c['id']:c for c in classes}:
        raise ValueError('existing class hierarchy changed')
    artifact=OntologyArtifact(project_schema=schema,
        method_manifest={'method':'source_bound_definition_audit_subset_v1','rdf_parse_version':'lexical_turtle_v1',
            'source_base_iri':old_method['source_base_iri'],'source_sha256':hashlib.sha256(turtle.encode()).hexdigest(),
            'base_release_id':base.manifest['release_id'],'restoration_materials_hash':sha256_json(materials),
            'definition_audit_hash':sha256_json(audit),'human_confirmed':False,'research_only':True},
        validation_report={'domain_semantic_review':{'status':'machine_reviewed_subset','human_confirmed':False,
            'approved_property_count':len(approved),'quarantined_property_count':len(properties)-len(approved),
            'class_hierarchy_semantic_recertification':'not_performed','audit':audit}},
        mapping_report=converted['mapping_report'],application_profile=converted['application_profile'],
        iri_id_map=converted['iri_id_map'],source_ttl=turtle,
        requirements=read(base.path/'requirements.json'),competency_questions=read(base.path/'competency_questions.json'))
    catalog=seed_catalog(base,output/'releases')
    release=catalog.publish(artifact,expected_base_release_id=base.manifest['release_id'],approval={
        'status':'approved','approved_by':'automated_three_family_definition_audit','human_approved':False,
        'approval_kind':'research_only_machine_review','reason':'User-authorized isolated preexperiment freeze; all included annotations pass every axis in three independent model-family reviews. Human labels and formal study approval remain separate.'})
    write(output/'frozen_release.json',{'status':'research_preexperiment_subset_frozen','path':str(release.path),
        **release.binding(),'approved_property_ids':sorted(approved),'class_count':len(classes),
        'human_confirmed':False,'production_activated':False,'class_hierarchy_semantically_recertified':False,
        'fair_comparison_rule':'all methods must use this exact release, profile, source and candidate snapshot; schema extension disabled in fixed-ontology arm',
        'definition_audit_hash':sha256_json(audit)})
    return release


def execute(output,prepared,recovered=None):
    from review_agent.case_budget import CaseBudget
    from ontology_review.definition_audit import review,aggregate
    repository,candidate,document,base,materials,config,voters,proposals=prepared
    with repository.connect() as connection:
        baseline={r['attempt_id'] for r in connection.execute('SELECT attempt_id,metadata_json FROM request_attempts')
                  if json.loads(r['metadata_json'])['task_type']!='ontology_definition_audit_v1'}
    write(output/('local_validation_continuation_started.json' if recovered else 'started.json'),
          {'automatic_relaunch':False,'budget_scope':'new_ontology_preparation_and_mapping_acceptance_calls','max_new_calls':48})
    repository.authorize_case_budget(candidate['candidate_id'],{'llm_calls':48,'tokens':500000,'tool_calls':24},
        reason='User requested independent definition checks, isolated common ontology freeze and mapping engineering acceptance',
        actor='codex_user_authorized',authorization_key=output.name+':ontology-preparation-v1')
    budget=CaseBudget(config['budget'],repository,[repository.ensure_case(candidate['candidate_id'])],
                      task_type='ontology_preparation',task_id=output.name)
    if recovered:
        budget.usage['llm_calls']=len(recovered['votes'])
        for vote in recovered['votes']:budget.tokens(vote)
    rows=[]
    for index,proposal in enumerate(proposals,1):
        result=recovered if recovered and index==1 else review(repository,proposal,voters,budget)
        write(output/f'definition_batch_{index:02}.json',result)
        failed=[v for v in result['votes'] if v.get('response_status') in {'invalid_contract','invalid_json','invalid_reference'}]
        # One contract-only repair, retaining all initial receipts and valid cached opinions.
        if failed and not any(v.get('outcome_unknown') for v in result['votes']):
            result=review(repository,proposal,voters,budget)
            write(output/f'definition_batch_{index:02}_contract_repair.json',result)
        rows.extend(aggregate(proposal,result['votes'],proposal['committee_members']))
        progress={'completed_batches':index,'total_batches':len(proposals),'reviewed_definitions':len(rows),
                  'machine_approved':sum(r['status']=='machine_approved' for r in rows),
                  'quarantined':sum(r['status']=='quarantined' for r in rows),'budget':budget.snapshot()}
        (output/'progress.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in progress.items() if k!='budget'},ensure_ascii=False),flush=True)
    write(output/'definition_audit.json',{'version':'definition_audit_aggregate_v1','materials_hash':sha256_json(materials),
        'base_release_id':base.manifest['release_id'],'policy':proposals[0]['policy'],'rows':rows,
        'human_confirmed':False,'human_labels_used':False})
    write(output/'gaps.json',{'quarantined':[r for r in rows if r['status']!='machine_approved'],
        'restoration_unresolved':materials['unresolved'],'candidate_mapping_gaps':[
            {'candidate_id':candidate['candidate_id'],'surface_relation':candidate['triple']['surface_relation'],
             'relation_id':candidate['triple']['relation_id'],'tail_type':candidate['triple']['tail_type'],
             'status':'unresolved_exact_analogy_relation_and_tail_referent','force_mapping':False}],
        'human_semantic_labels':'still_required_for_paper','class_hierarchy_semantic_review':'not_complete'})
    # Keep the model aggregate provisional until explicit source-veto finalization.
    release=None
    with repository.connect() as connection:
        new=[dict(r) for r in connection.execute('SELECT attempt_id,state,result_json FROM request_attempts') if r['attempt_id'] not in baseline]
    details=[{'attempt_id':r['attempt_id'],'state':r['state'],**{k:read_result.get(k) for k in
              ('input_tokens','output_tokens','client_request_id','response_id','response_status','error_code')}}
             for r in new for read_result in [json.loads(r['result_json']or'{}')]]
    unchanged=hashlib.sha256(Path(read(output/'preparation.json')['source_database']).read_bytes()).hexdigest()==read(output/'preparation.json')['source_database_sha256']
    write(output/'audit_result_preliminary.json',{'status':'reviewed_pending_source_check_and_freeze',
        'machine_approved':sum(r['status']=='machine_approved' for r in rows),
        'quarantined':sum(r['status']=='quarantined' for r in rows),'new_requests':details,
        'known_input_tokens':sum(r.get('input_tokens')or 0 for r in details),
        'known_output_tokens':sum(r.get('output_tokens')or 0 for r in details),
        'price':None,'unknown_cost_is_zero':False,'source_database_unchanged':unchanged,
        'frozen_release_path':str(release.path) if release else None,'human_confirmed':False,'production_written':False})
    if not unchanged or len(new)>48:raise ValueError('source immutability or request cap violated')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--run',action='store_true')
    parser.add_argument('--recover-local-validator',action='store_true',help='Revalidate the three known first-batch receipts locally; never resend them')
    args=parser.parse_args();output=args.output.resolve()
    if args.recover_local_validator:
        if not args.run or not output.is_relative_to(ROOT/'outputs'):raise ValueError('explicit isolated continuation required')
        prepared,recovered=recover_local_validator(output)
        execute(output,prepared,recovered);return
    if not output.is_relative_to(ROOT/'outputs') or output.exists():raise ValueError('use a new isolated outputs directory')
    prepared=prepare(output)
    if args.run:execute(output,prepared)
    else:print(json.dumps({'status':'prepared_only','model_calls':0,'output':str(output)}))


def recover_local_validator(output):
    from review_agent.repository import ReviewRepository
    from review_agent.cli import _configured_validators
    from review_agent.validators import _parse_content
    from ontology_review.definition_audit import binding,errors,packets
    from document.hierarchy_index import load_document_index
    from ontologyBuild.release_service import load_release
    from runs.regress_v8 import configuration
    if (not (output/'started.json').exists() or (output/'local_validation_continuation_started.json').exists()
            or (output/'definition_batch_01.json').exists()):raise ValueError('continuation requires stopped first-batch-only driver')
    prep=read(output/'preparation.json')
    if hashlib.sha256(Path(prep['source_database']).read_bytes()).hexdigest()!=prep['source_database_sha256']:
        raise ValueError('original source changed')
    repo=ReviewRepository(output/'review.sqlite3');candidate=read(SOURCE/'contract_recovery_completed_candidate.json')
    document=load_document_index(repo.get_run(candidate['run_id'])['document_path'],candidate['source_index_hash'],require_verified=True)
    base=load_release(candidate['ontology_release_path']);materials=read(SOURCE/'definition_materials.json')
    config=configuration(profile='weighted_diagnostic',directory=output)
    config['budget'].update(max_llm_calls_per_candidate=48,max_total_tokens=500000,total_timeout_seconds=2400)
    voters=_configured_validators(config)
    members=[{k:v[k] for k in ('id','model_id','model_family','model_identity') if k in v} for v in voters]
    proposals=read(output/'definition_packets.json')
    if packets(materials,base.ontology,document['source_snapshot']['canonical_text'],base.manifest['release_id'],members)!=proposals:
        raise ValueError('frozen task changed')
    with repo.connect() as connection:
        attempts=[dict(r) for r in connection.execute('SELECT attempt_id,state,metadata_json,result_json FROM request_attempts')
                  if json.loads(r['metadata_json'])['task_type']=='ontology_definition_audit_v1']
    if len(attempts)!=3:raise ValueError('only exactly three original first-batch receipts can be recovered')
    votes=[]
    for attempt in attempts:
        receipt=json.loads(attempt['result_json']or'{}');metadata=json.loads(attempt['metadata_json'])
        member=next(v for v in members if v['id']==receipt.get('voter_id'))
        if (attempt['state']!='failed' or receipt.get('response_status')!='invalid_contract' or receipt.get('outcome_unknown')
                or 'unsupported local schema keyword' not in receipt.get('error','') or metadata['proposal']!=proposals[0]
                or any(receipt.get(k)!=v for k,v in binding(member,proposals[0]).items())):
            raise ValueError('not the known local schema validation defect')
        raw=_parse_content(receipt['raw_response']);invalid=errors(raw,proposals[0])
        if invalid:raise ValueError('original receipt contains actual contract error: '+str(invalid))
        votes.append({**receipt,**raw,'response_status':'ok','attempt_id':attempt['attempt_id'],
                      'local_validation_recovery':{'original_receipt_hash':sha256_json(receipt),'original_status':receipt['response_status'],
                        'original_attempt_state_unchanged':True,'new_model_calls':0,'reason':'unsupported uniqueItems replaced by equivalent explicit duplicate checks'}})
    recovered={'status':'original_receipts_locally_revalidated','proposal':proposals[0],'votes':votes,'new_model_calls':0}
    write(output/'local_validation_recovery.json',recovered)
    from zipfile import ZipFile,ZIP_DEFLATED
    files=[ROOT/'ontology_review/definition_audit.py',Path(__file__),ROOT/'tests/test_definition_audit.py']
    write(output/'continuation_code_fingerprints.json',{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    with ZipFile(output/'continuation_execution_code.zip','x',ZIP_DEFLATED) as archive:
        for p in files:archive.write(p,p.relative_to(ROOT).as_posix())
    return (repo,candidate,document,base,materials,config,voters,proposals),recovered


if __name__=='__main__':main()
