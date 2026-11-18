"""Restore annotation materials and run one bounded, independently reviewed qualifier completion.

All changes stay in a new isolated clone. The source DB, original votes, and
published ontology are preserved. Initial --run permits eight NEW requests;
the explicit known-contract recovery phase raises the cumulative limit to ten.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit,unquote

from .review_samples import ReadOnlyRepository
from review_agent.contracts import sha256_json
from review_agent.review_observability import redact

ROOT=Path(__file__).resolve().parents[1]


def save(folder,name,value):
    with (folder/name).open('x',encoding='utf-8') as stream:json.dump(redact(value),stream,ensure_ascii=False,indent=2)


def prepare(source_folder,output):
    from review_agent.shadow_revision import clone
    from document.hierarchy_index import load_document_index
    from ontologyBuild.release_service import load_release
    from ontologyBuild.definition_materials import restoration_materials,restored_graph
    from ontologyBuild.rdf_artifact import canonical_turtle
    from ontologyBuild.owl_to_project_schema import convert_turtle
    from ontologyBuild.ontology_adapter import iter_classes,iter_object_properties
    source_folder=Path(source_folder).resolve();output=Path(output).resolve()
    source=ReadOnlyRepository(source_folder/'review.sqlite3')
    old=json.loads((source_folder/'result.json').read_text(encoding='utf-8'))
    candidate=source.get_revision(old['candidate_id'],old['revision'])
    audit=source.get_audit(candidate['candidate_id'],old['audit_id'])
    base=next(p for p in audit['rounds'] if p['proposal_digest']==old['selected_proposal_digest'])
    run=source.get_run(candidate['run_id'])
    document=load_document_index(run['document_path'],candidate['source_index_hash'],require_verified=True)
    release=load_release(candidate['ontology_release_path'])
    method=json.loads((release.path/'method_manifest.json').read_text(encoding='utf-8'))
    uri=urlsplit(method['source_base_iri'])
    upstream=Path(unquote(uri.path).lstrip('/') if uri.scheme=='file' and len(uri.path)>2 and uri.path[2]==':' else unquote(uri.path))
    if not upstream.is_absolute() or not upstream.exists():raise ValueError('original source ontology artifact unavailable')
    if hashlib.sha256(upstream.read_bytes()).hexdigest()!=method['source_sha256']:raise ValueError('original ontology source hash changed')
    candidates=json.loads((upstream.parent/'normalized_candidates.json').read_text(encoding='utf-8'))
    provenance=json.loads((upstream.parent/'chapter1_provenance.json').read_text(encoding='utf-8'))
    materials=restoration_materials(release.ontology,candidates,provenance,document['source_snapshot']['canonical_text'])
    repo=clone(source.database_path,output,allowed_root=ROOT/'outputs');repo.initialize()
    save(output,'definition_materials.json',materials)
    graph=restored_graph((release.path/'ontology.ttl').read_text(encoding='utf-8'),materials)
    turtle=canonical_turtle(graph)
    (output/'definition_restoration_draft.ttl').write_text(turtle,encoding='utf-8')
    bindings={x['iri']:x['id'] for x in [*iter_classes(release.ontology),*iter_object_properties(release.ontology)]}
    converted=convert_turtle(turtle,iri_id_map=bindings,base_iri=method['source_base_iri'])['project_schema']
    restored_props={p['id']:p for p in iter_object_properties(converted)}
    if set(restored_props)!={p['id'] for p in iter_object_properties(release.ontology)}:raise ValueError('restoration changed formal property IDs')
    for prop in iter_object_properties(release.ontology):
        if any(restored_props[prop['id']].get(k)!=prop.get(k) for k in ('label','domain','range')):
            raise ValueError('restoration changed a property signature')
    save(output,'definition_restoration_draft.json',converted)
    props=list(iter_object_properties(release.ontology));surface=candidate['triple']['surface_relation']
    exact=[p['id'] for p in props if surface in [p.get('label'),p.get('name'),*(p.get('aliases')or[])]]
    packet={'status':'mapping_materials_prepared_not_semantic_assignment','base_release_id':release.manifest['release_id'],
        'base_ontology_hash':sha256_json(release.ontology),'candidate_id':candidate['candidate_id'],
        'original_expression':candidate['triple'],'source_evidence':base['evidence_bundle']['items'],
        'restored_definition_count':len(materials['definitions']),'unresolved_definition_count':len(materials['unresolved']),
        'existing_relation_inventory':[{k:p.get(k) for k in ('id','label','domain','range')} for p in props],
        'exact_or_declared_alias_matches':exact,'full_inventory_scanned':True,'semantic_gap_confirmed':False,
        'next_modeling_questions':[
            {'kind':'relation_semantics','surface':surface,'question':'Does an existing full definition express the exact analogy, or is a shared analogy relation needed? Do not substitute causation or approximate equivalence.'},
            {'kind':'tail_referent','surface':candidate['triple']['tail'],'question':'Determine from source whether the tail denotes an entity, an event or a scoped proposition before assigning a type.'}],
        'definition_status':'upstream_candidate_annotations_restored; independent semantic review still required',
        'original_release_changed':False,'new_ids_assigned':False}
    save(output,'mapping_materials.json',packet)
    from review_agent.request_reconciliation import report
    save(output,'unknown_requests_before.json',report(repo,candidate_id=candidate['candidate_id']))
    save(output,'preparation.json',{'candidate_id':candidate['candidate_id'],'candidate_hash':sha256_json(candidate),
        'source_database':str(source.database_path),'source_audit_id':audit['audit_id'],
        'definition_export_defect':'object-property definition_candidate omitted from Turtle writer',
        'new_model_calls_limit':8,'actual_new_model_calls':0,'production_written':False})
    return repo,candidate,base,document


def execute(repo,candidate,base,document,output,*,continue_format=False,recover_contract=False):
    from runs.regress_v8 import configuration,public_settings
    from review_agent.cli import _configured_validators
    from review_agent.model_settings import create_planner_client
    from review_agent.case_budget import CaseBudget
    from review_agent.qualifier_completion import propose
    from review_agent.revision_service import RevisionService
    from review_agent.shadow_revision import apply_reviewed
    from review_agent.orchestrator import ReviewOrchestrator
    from review_agent.ontology_guard import OntologyGuard,load_snapshot
    from review_agent.request_reconciliation import report
    config=configuration(profile='weighted_diagnostic',directory=output)
    config['planner'].update(stream_responses=True,thinking_mode='disabled')
    config['budget'].update(total_timeout_seconds=360,max_total_tokens=120000)
    config['budget']['max_llm_calls_per_candidate']=8
    repo.authorize_case_budget(candidate['candidate_id'],{'llm_calls':8,'tokens':120000,'tool_calls':8},
        reason='User requested qualifier completion, ontology material recovery and unknown request handling',
        actor='codex_user_authorized',authorization_key=output.name+':completion-v1')
    if recover_contract:
        repo.authorize_case_budget(candidate['candidate_id'],{'llm_calls':2,'tokens':60000},
            reason='One bounded repair of known revision-contract failures plus mandatory new-version review; no unknown resend',
            actor='codex_user_authorized',authorization_key=output.name+':known-contract-v1')
        config['budget']['max_llm_calls_per_candidate']=6
    case=repo.ensure_case(candidate['candidate_id'])
    budget=CaseBudget(config['budget'],repo,[case],task_type='qualifier_completion',task_id=candidate['candidate_id'])
    validators=_configured_validators(config)
    prefix='contract_recovery_' if recover_contract else 'continued_' if continue_format else ''
    stage_prefix='contract_recovery_' if recover_contract else ''
    settings=public_settings(config);settings['vote_weighting']=config['vote_weighting'];save(output,prefix+'settings.json',settings)
    with repo.connect() as connection:baseline={r[0] for r in connection.execute('SELECT attempt_id FROM request_attempts')}
    if continue_format or recover_contract:
        previous=json.loads((output/('continued_result.json' if recover_contract else 'result.json')).read_text(encoding='utf-8'))
        baseline-={r['attempt_id'] for r in previous['new_request_details']}
    save(output,prefix+'started.json',{'automatic_relaunch':False,'max_new_model_calls':10 if recover_contract else 8,
        'new_planner_calls':0 if continue_format or recover_contract else 1})
    from zipfile import ZipFile,ZIP_DEFLATED
    files=[p for folder in ('review_agent','ontologyBuild','evidence','document','schema','prompts','specs')
        for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.txt','.json'}]
    files += [Path(__file__),ROOT/'runs/generate_chapter1_ontology.py']
    fingerprints={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    save(output,prefix+'code_fingerprints.json',fingerprints)
    with ZipFile(output/(prefix+'execution_code.zip'),'x',ZIP_DEFLATED) as archive:
        for name in sorted(fingerprints):archive.write(ROOT/name,name)
    if continue_format or recover_contract:
        from review_agent.qualifier_completion import complete_record,bounded_rationale_projection
        saved=json.loads((output/('continued_qualifier_proposal.json' if recover_contract else 'qualifier_proposal.json')).read_text(encoding='utf-8'))
        if not bounded_rationale_projection(saved['receipt']):raise ValueError('only a known narrative-length failure can continue without a new call')
        if recover_contract:
            prior=json.loads((output/'revision_review.json').read_text(encoding='utf-8'))
            if (prior['status']!='review_incomplete' or not prior['votes'] or any(v.get('response_status')!='invalid_contract'
                    or v.get('outcome_unknown') or 'historical material evidence' not in v.get('error','') for v in prior['votes'])):
                raise ValueError('contract recovery requires known protected-material contract failures; uncertain or unknown votes cannot be retried')
        proposed=complete_record(repo,candidate,base['evidence_bundle'],document,saved,source_audit_id=base['audit_id'])
    else:proposed=propose(repo,candidate,base['evidence_bundle'],document,create_planner_client(config),budget,source_audit_id=base['audit_id'])
    save(output,prefix+'qualifier_proposal.json',proposed)
    result={'status':proposed['status'],'candidate_id':candidate['candidate_id'],'original_revision':candidate['revision'],
        'production_written':False,'human_confirmed_accuracy':None}
    if proposed['status']=='draft_proposed':
        committee=ReviewOrchestrator(repo,OntologyGuard(load_snapshot(Path(candidate['ontology_release_path'])/'ontology_snapshot.json')),validators,config=config).committee
        reviewed=RevisionService(repo).review(proposed['draft']['draft_id'],validators,committee,budget)
        save(output,stage_prefix+'revision_review.json',reviewed);result['revision_review_status']=reviewed['status']
        if reviewed['status']=='approved':
            applied=apply_reviewed(repo,draft_id=proposed['draft']['draft_id']);save(output,stage_prefix+'revision_applied.json',applied)
            current=repo.get_revision(candidate['candidate_id'])
            save(output,stage_prefix+'completed_candidate.json',current)
            # A new immutable version gets new independent opinions; no old vote reuse.
            final_config=deepcopy(config);final_config['planner']={'enabled':False,'strategy':'deterministic'}
            final_config['budget'].update(max_llm_calls_per_candidate=3,max_rounds=1,max_retries_per_request=0)
            audited=ReviewOrchestrator(repo,OntologyGuard(load_snapshot(Path(current['ontology_release_path'])/'ontology_snapshot.json')),
                validators,config=final_config).audit_candidate(current['candidate_id'],current['revision'],document)
            save(output,stage_prefix+'new_version_review.json',audited)
            owner='bounded_qualifier_completion';job=repo.claim_job(owner,job_id=applied['reaudit_job_id'])
            if job:repo.complete_job(job['job_id'],{'audit_id':audited['audit_id'],'execution':'new_version_independent_review'},
                owner=owner,lease_token=job['lease_token'])
            from review_agent.revision_patch import expression_diagnostics
            result.update(status='completion_applied_and_rechecked',new_revision=current['revision'],
                qualifier_diagnostics=expression_diagnostics(current),new_qualifiers=current['triple']['qualifiers'],
                new_qualifier_status=current['triple']['qualifier_status'],research_operation=audited.get('research_operation'),
                new_dimension_statuses={a:d['status'] for a,d in audited['review_outcome']['dimension_results'].items()},old_votes_reused=False)
        else:result['status']='completion_draft_not_approved'
    with repo.connect() as connection:
        new=[dict(r) for r in connection.execute('SELECT attempt_id,state,metadata_json,result_json FROM request_attempts') if r['attempt_id'] not in baseline]
    result['new_model_requests']=len(new)
    if len(new)>(10 if recover_contract else 8):raise ValueError('new-request cap exceeded')
    result['new_request_states']={s:sum(r['state']==s for r in new) for s in sorted({r['state'] for r in new})}
    result['new_request_details']=[{'attempt_id':r['attempt_id'],'state':r['state'],
        'task_type':json.loads(r['metadata_json'])['task_type'],
        'input_tokens':json.loads(r['result_json']or'{}').get('input_tokens'),
        'output_tokens':json.loads(r['result_json']or'{}').get('output_tokens'),
        'client_request_id':json.loads(r['result_json']or'{}').get('client_request_id')} for r in new]
    save(output,prefix+'unknown_requests_after.json',report(repo,candidate_id=candidate['candidate_id']))
    save(output,prefix+'result.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',default='outputs/vote_weighting_20261116/live_dev006')
    parser.add_argument('--output',required=True);parser.add_argument('--run',action='store_true')
    parser.add_argument('--continue-format',action='store_true',help='Continue a known narrative-length failure from its saved response; no new planner call')
    parser.add_argument('--recover-known-contract',action='store_true',help='One bounded protected-material contract repair; maximum ten new requests across all phases')
    args=parser.parse_args();output=Path(args.output).resolve()
    if args.continue_format and args.recover_known_contract:raise ValueError('select one recovery phase')
    if (args.continue_format or args.recover_known_contract) and not args.run:raise ValueError('recovery phases require --run')
    if args.continue_format or args.recover_known_contract:
        if not output.is_relative_to(ROOT/'outputs'):raise ValueError('continuation must stay in outputs')
        from review_agent.repository import ReviewRepository
        from document.hierarchy_index import load_document_index
        preparation=json.loads((output/'preparation.json').read_text(encoding='utf-8'))
        repo=ReviewRepository(output/'review.sqlite3');candidate=repo.get_revision(preparation['candidate_id'])
        if sha256_json(candidate)!=preparation['candidate_hash']:raise ValueError('candidate changed after the saved proposal')
        audit=repo.get_audit(candidate['candidate_id'],preparation['source_audit_id']);base=audit['rounds'][-1]
        run=repo.get_run(candidate['run_id']);document=load_document_index(run['document_path'],candidate['source_index_hash'],require_verified=True)
    else:repo,candidate,base,document=prepare(args.source,output)
    if args.run:
        try:result=execute(repo,candidate,base,document,output,continue_format=args.continue_format,recover_contract=args.recover_known_contract)
        except Exception as exc:
            from review_agent.request_reconciliation import report
            save(output,'failure.json',{'status':'stopped','reason':str(exc),'automatic_relaunch':False})
            if not (output/'unknown_requests_after.json').exists():save(output,'unknown_requests_after.json',report(repo,candidate_id=candidate['candidate_id']))
            raise
        print(json.dumps(result,ensure_ascii=False))
    else:print(json.dumps({'status':'materials_prepared','output':str(output),'model_calls':0}))


if __name__=='__main__':main()
