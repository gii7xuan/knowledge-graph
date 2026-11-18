"""Fresh bounded response-consistency smoke on the three frozen engineering fixtures."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED

from .freeze_shared_ontology import ROOT,SOURCE,read,write
from .mapping_acceptance import fixture_candidates,acceptance
from review_agent.contracts import sha256_json


def run(output,send):
    from ontologyBuild.release_service import load_release
    from evaluation.review_samples import ReadOnlyRepository
    from document.hierarchy_index import load_document_index
    from review_agent.repository import ReviewRepository
    from review_agent.orchestrator import ReviewOrchestrator
    from review_agent.ontology_guard import OntologyGuard,load_snapshot
    from review_agent.cli import _configured_validators
    from review_agent.response_consistency import policy
    from runs.regress_v8 import configuration,public_settings
    if not output.is_relative_to(ROOT/'outputs') or output.exists():raise ValueError('new isolated outputs directory required')
    frozen=ROOT/'outputs/ontology_freeze_20261116/live'
    release=load_release(read(frozen/'frozen_release.json')['path'])
    source=ReadOnlyRepository(SOURCE/'review.sqlite3');original=read(SOURCE/'contract_recovery_completed_candidate.json')
    source_run=source.get_run(original['run_id']);source_hash=hashlib.sha256(source.database_path.read_bytes()).hexdigest()
    document=load_document_index(source_run['document_path'],original['source_index_hash'],require_verified=True)
    run_id='response_consistency_'+output.name
    candidates=fixture_candidates(document,release,read(SOURCE/'definition_materials.json'),original,run_id)
    # Test the previously failing engineered predicate first; no outcome-based selection.
    candidates.sort(key=lambda pair:('semantic_mismatch','positive_mapping','missing_material').index(pair[0]))
    output.mkdir(parents=True)
    config=configuration(profile='weighted_diagnostic',directory=output)
    config['planner'].update(enabled=False,strategy='deterministic')
    config['mapping_preflight']={'enabled':True};config['consensus']['max_rounds']=1
    config['budget'].update(max_llm_calls_per_candidate=3,max_retries_per_request=0,max_rounds=1,
        total_timeout_seconds=330,per_call_timeout_seconds=240,max_total_tokens=100000)
    config['case_budget'].update(llm_calls=3,tokens=100000)
    for slot in config['validators']:slot.update(request_timeout_seconds=240,http_timeout_seconds=240)
    repository=ReviewRepository(output/'review.sqlite3');repository.initialize()
    repository.create_run({**source_run,**release.binding(),'run_id':run_id,'mode':'shadow',
        'ontology_path':str(release.snapshot_path),'ontology_hash':release.manifest['ontology_version'],'experiment_id':output.name})
    for _,candidate in candidates:repository.add_candidate(candidate)
    write(output/'experiment.json',{'kind':'fresh_engineering_response_consistency_smoke','max_initial_calls':9,
        'max_new_calls_including_bounded_field_corrections':12,'known_token_stop':300000,
        'ontology':release.binding(),'consistency_policy':policy(),'source_database':str(source.database_path),
        'source_database_sha256':source_hash,'old_votes_used':False,'old_decisions_rewritten':False,
        'selection':'same three authored engineering fixtures; mismatch first','human_gold':False,'paper_accuracy':None,
        'production_written':False,'timeout_change':'explicit 240s wait for all seats; does not change semantic judgments'})
    write(output/'fixtures.json',dict(candidates))
    write(output/'settings.json',{**public_settings(config),'vote_weighting':config['vote_weighting'],
        'mapping_preflight':config['mapping_preflight'],'response_consistency_policy':policy()})
    files=[p for folder in ('review_agent','evaluation','ontologyBuild','evidence','document','schema','prompts','specs')
           for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.txt','.json'}]
    fingerprints={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    write(output/'code_fingerprints.json',fingerprints)
    with ZipFile(output/'execution_code.zip','x',ZIP_DEFLATED) as archive:
        for name in sorted(fingerprints):archive.write(ROOT/name,name)
    if not send:print('prepared_only',flush=True);return
    write(output/'started.json',{'automatic_relaunch':False,'max_initial_calls':9,'unknown_resend':False})
    guard=OntologyGuard(load_snapshot(release.snapshot_path));owner=ReviewOrchestrator(repository,guard,_configured_validators(config),config=config)
    for name,candidate in candidates:
        with repository.connect() as connection:
            known=sum(sum(json.loads(r[0]or'{}').get(k)or 0 for k in ('input_tokens','output_tokens'))
                for r in connection.execute('SELECT result_json FROM request_attempts'))
        if known>=300000:raise ValueError('known token stop reached; no further dispatch')
        result=owner.audit_candidate(candidate['candidate_id'],1,document)
        audit=repository.get_audit(candidate['candidate_id'],result['audit_id'])
        write(output/(name+'_result.json'),result);write(output/(name+'_audit.json'),audit)
        print(json.dumps({'fixture':name,'original_responses':[{'voter':v['voter_id'],'status':v['response_status']} for v in audit['votes']]},ensure_ascii=False),flush=True)
    finalize(output,stage='initial')


def finalize(output,*,stage='receipt_projection'):
    """Zero-call readback of complete original receipts, preserving original exports."""
    from evaluation.review_samples import ReadOnlyRepository
    from review_agent.review_observability import outcome
    from review_agent.validators import vote_binding,_parse_content
    from review_agent.validator_contracts import validate_vote
    from review_agent.response_consistency import issues
    from runs.revalidate_v8 import committee
    repository=ReadOnlyRepository(output/'review.sqlite3');experiment=read(output/'experiment.json')
    rows=[]
    for name,candidate in read(output/'fixtures.json').items():
        original=read(output/(name+'_audit.json'))
        proposal={k:v for k,v in original['rounds'][-1].items() if k not in {'consensus','gaps'}}
        votes=[]
        for old in original['votes']:
            record=repository.request_attempt(old['attempt_id']);receipt=record.get('result')or{}
            member=next(m for m in proposal['committee_members'] if m['id']==old['voter_id'])
            if (record['metadata']['proposal']!=proposal or any(receipt.get(k)!=v for k,v in vote_binding({**member,'client':None},proposal).items())):
                raise ValueError('original receipt binding differs from frozen proposal')
            vote=old
            if old['response_status']!='ok' and record['state']=='completed' and receipt.get('response_status')=='ok':
                if validate_vote(receipt,proposal['response_contract_version']):raise ValueError('late receipt failed original response contract')
                vote={**receipt,'attempt_id':old['attempt_id'],'original_receipt_projection':True}
            votes.append(vote)
        derived=outcome(proposal,votes,committee(proposal),audit_status='completed')
        result={'review_outcome':derived,'research_operation':derived.get('research_operation'),'weight_snapshot':derived.get('weight_snapshot',{})}
        row=acceptance(name,result,{**original,'votes':votes},candidate)
        diagnostics=[]
        for vote in votes:
            try:raw=_parse_content(vote.get('corrected_response') or vote.get('raw_response'))
            except (ValueError,TypeError):raw=None
            diagnostics.append({'voter_id':vote['voter_id'],'response_status':vote['response_status'],'issues':issues(raw),
                                'material_counter_ids':[a['evidence_id'] for a in (raw or {}).get('evidence_assessments',[]) if a['role']=='counter']})
        row['consistency']=diagnostics;row['all_three_responses_consistent']=all(v['response_status']=='ok' and not v['issues'] for v in diagnostics)
        rows.append(row)
        write(output/(name+'_'+stage+'_projection.json'),{'proposal':proposal,'votes':votes,'derived_outcome':derived,'engineering_acceptance':row,
            'original_audit_hash':sha256_json(original),'original_decisions_changed':False,'new_model_calls':0})
    with repository.connect() as connection:
        requests=[dict(r) for r in connection.execute('SELECT attempt_id,state,result_json FROM request_attempts')]
        outbox=connection.execute('SELECT COUNT(*) FROM import_outbox').fetchone()[0]
    details=[{'attempt_id':r['attempt_id'],'state':r['state'],**{k:v.get(k) for k in
        ('input_tokens','output_tokens','response_status','client_request_id','error_code')}}
        for r in requests for v in [json.loads(r['result_json']or'{}')]]
    unchanged=hashlib.sha256(Path(experiment['source_database']).read_bytes()).hexdigest()==experiment['source_database_sha256']
    report={'stage':stage,'engineering_gate_passed':all(r['passed'] for r in rows),
        'all_responses_consistent':all(r['all_three_responses_consistent'] for r in rows),'fixtures':rows,'request_details':details,
        'new_requests':len(details),'new_unsettled_requests':sum(r['state'] in {'running','unknown'} for r in details),
        'known_input_tokens':sum(r.get('input_tokens')or 0 for r in details),'known_output_tokens':sum(r.get('output_tokens')or 0 for r in details),
        'price':None,'source_database_unchanged':unchanged,'isolated_import_outbox':outbox,'old_decisions_rewritten':False,
        'human_gold':False,'paper_accuracy':None,'production_written':False}
    write(output/(stage+'_report.json'),report)
    if len(details)>experiment['max_new_calls_including_bounded_field_corrections'] or not unchanged or outbox:raise ValueError('isolation or bounded request cap failed')
    print(json.dumps({k:report[k] for k in ('stage','engineering_gate_passed','all_responses_consistent','new_requests','new_unsettled_requests')},ensure_ascii=False),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--run',action='store_true');parser.add_argument('--finalize-only',action='store_true')
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(ROOT/'outputs'):raise ValueError('isolated outputs only')
    if args.finalize_only:finalize(output)
    else:run(output,args.run)


if __name__=='__main__':main()
