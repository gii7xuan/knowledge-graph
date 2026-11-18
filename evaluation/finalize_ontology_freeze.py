"""Finalize complete immutable audit receipts with source-grounded vetoes; zero model calls."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED

from .freeze_shared_ontology import ROOT,SOURCE,read,write,freeze
from ontology_review.definition_audit import aggregate
from review_agent.contracts import sha256_json


def apply_blockers(rows,proposals,blockers):
    items={r['relation_id']:r for p in proposals for r in p['items']}
    mapped={r['relation_id']:deepcopy(r) for r in rows}
    for finding in blockers['findings']:
        identity=finding['relation_id'];item=items[identity]
        evidence={e['evidence_id']:e['quote'] for e in item['evidence']}
        if (finding.get('blocking') is not True or not finding.get('reason') or finding.get('human_confirmed') is not False
                or not finding['evidence_ids'] or not set(finding['evidence_ids'])<=evidence.keys()
                or [evidence[e] for e in finding['evidence_ids']]!=finding['source_quotes']):
            raise ValueError('source semantic blocker must bind exact local evidence')
        row=mapped[identity];row['original_machine_status']=row['status'];row['status']='quarantined'
        row.setdefault('source_semantic_blockers',[]).append(deepcopy(finding))
    return [mapped[r['relation_id']] for r in rows]


def recover_last_batch(output):
    """Use a proved late original receipt plus one known malformed-seat repair."""
    from review_agent.repository import ReviewRepository
    from review_agent.case_budget import CaseBudget
    from review_agent.cli import _configured_validators
    from runs.regress_v8 import configuration
    from ontology_review.definition_audit import binding,errors,review
    if (output/'last_batch_recovery_started.json').exists():raise ValueError('one bounded recovery already started; inspect its receipts')
    previous=read(output/'definition_batch_09.json');proposal=previous['proposal']
    repo=ReviewRepository(output/'review.sqlite3');votes=[];failed=[]
    for vote in previous['votes']:
        record=repo.request_attempt(vote['attempt_id']);receipt=record.get('result')or{}
        if vote.get('outcome_unknown'):
            member=next(m for m in proposal['committee_members'] if m['id']==vote['voter_id'])
            if (record['state']!='completed' or receipt.get('response_status')!='ok' or not receipt.get('late_response')
                    or record['metadata']['proposal']!=proposal or errors({'assessments':receipt.get('assessments')},proposal)
                    or any(receipt.get(k)!=v for k,v in binding(member,proposal).items())):
                raise ValueError('original unknown seat has no complete, bound late receipt; never resend')
            votes.append({**receipt,'attempt_id':vote['attempt_id'],'late_original_receipt_recovery':True,'new_model_calls':0})
        elif vote.get('response_status')=='ok':votes.append(vote)
        elif (vote.get('response_status')=='invalid_contract' and record['state']=='failed'
                and not receipt.get('outcome_unknown') and 'required' in receipt.get('error','')):failed.append(vote['voter_id'])
        else:raise ValueError('recovery restricted to complete late receipt and known missing-field error')
    if len(failed)!=1 or len(votes)!=2:raise ValueError('expected exactly one malformed seat')
    write(output/'last_batch_recovery_started.json',{'max_new_calls':1,'unknown_resends':0,
        'parent_batch_hash':sha256_json(previous),'recovered_original_late_attempt_ids':[v['attempt_id'] for v in votes if v.get('late_original_receipt_recovery')]})
    config=configuration(profile='weighted_diagnostic',directory=output)
    config['budget'].update(max_llm_calls_per_candidate=1,max_total_tokens=50000,total_timeout_seconds=240)
    candidate=read(SOURCE/'contract_recovery_completed_candidate.json')
    budget=CaseBudget(config['budget'],repo,[repo.ensure_case(candidate['candidate_id'])],task_type='ontology_preparation',task_id=output.name)
    selected=[v for v in _configured_validators(config) if v['id'] in failed]
    repaired=review(repo,proposal,selected,budget)
    # Charge known tokens on the now-settled original seat once; it was previously unknown.
    for vote in votes:
        if vote.get('late_original_receipt_recovery'):budget.tokens(vote)
    combined={'status':'late_original_receipt_and_one_known_contract_repair','proposal':proposal,
        'parent_batch_hash':sha256_json(previous),'votes':votes+repaired['votes'],'budget':budget.snapshot()}
    write(output/'definition_batch_09_receipt_recovery.json',combined)
    print(json.dumps({'status':combined['status'],'votes':[{k:v.get(k) for k in ('voter_id','response_status')} for v in combined['votes']]},ensure_ascii=False),flush=True)


def finalize(output):
    from ontologyBuild.release_service import load_release
    from evaluation.review_samples import ReadOnlyRepository
    if (output/'frozen_release.json').exists():raise ValueError('freeze already exists; inspect instead of republishing')
    prep=read(output/'preparation.json');materials=read(SOURCE/'definition_materials.json')
    proposals=read(output/'definition_packets.json');original_audit=read(output/'definition_audit.json')
    if sha256_json(materials)!=prep['materials_hash']:raise ValueError('materials binding changed')
    base=load_release(prep['base_release']['ontology_release_path'])
    repo=ReadOnlyRepository(output/'review.sqlite3')
    with repo.connect() as connection:
        receipts={r['attempt_id']:dict(r) for r in connection.execute('SELECT attempt_id,state,metadata_json,result_json FROM request_attempts')}
    rows=[];original_rows=[]
    for i,proposal in enumerate(proposals,1):
        path=output/f'definition_batch_{i:02}_contract_repair.json'
        result=read(path if path.exists() else output/f'definition_batch_{i:02}.json')
        original_rows.extend(aggregate(proposal,result['votes'],proposal['committee_members']))
        recovery=output/f'definition_batch_{i:02}_receipt_recovery.json'
        if recovery.exists():
            recovered=read(recovery)
            if recovered['parent_batch_hash']!=sha256_json(result):raise ValueError('supplemental receipt parent changed')
            result=recovered
        if result['proposal']!=proposal:raise ValueError('frozen batch mismatch')
        for vote in result['votes']:
            record=receipts[vote['attempt_id']];receipt=json.loads(record['result_json']or'{}')
            if (json.loads(record['metadata_json'])['proposal']!=proposal
                    or receipt.get('raw_response')!=vote.get('raw_response')):raise ValueError('vote differs from durable source receipt')
            if vote.get('local_validation_recovery'):
                from review_agent.validators import _parse_content
                from ontology_review.definition_audit import errors
                raw=_parse_content(receipt['raw_response'])
                if (record['state']!='failed' or receipt.get('outcome_unknown')
                        or receipt.get('response_status')!='invalid_contract'
                        or sha256_json(receipt)!=vote['local_validation_recovery']['original_receipt_hash']
                        or 'unsupported local schema keyword' not in receipt.get('error','')
                        or raw!={'assessments':vote.get('assessments')} or errors(raw,proposal)):
                    raise ValueError('invalid local receipt recovery')
            elif any(receipt.get(k)!=vote.get(k) for k in ('response_status','assessments','proposal_digest','model_identity','returned_model_id')):
                raise ValueError('vote or binding changed after durable completion')
        rows.extend(aggregate(proposal,result['votes'],proposal['committee_members']))
    if original_rows!=original_audit['rows'] or len(rows)!=prep['definition_count']:raise ValueError('audit aggregate is incomplete or stale')
    blockers=read(output/'source_semantic_blockers.json')
    final=apply_blockers(rows,proposals,blockers)
    audit={**original_audit,'version':'definition_audit_final_with_source_veto_v1','rows':final,
           'original_model_aggregate_hash':sha256_json(original_audit),'source_semantic_blockers':blockers,
           'late_receipt_and_known_contract_supplement':(sha256_json(read(output/'definition_batch_09_receipt_recovery.json'))
                if (output/'definition_batch_09_receipt_recovery.json').exists() else None),
           'policy':{**original_audit['policy'],'additional_veto':'unresolved source-grounded semantic findings block inclusion; votes preserved'},
           'human_confirmed':False}
    write(output/'final_definition_audit.json',audit)
    write(output/'final_gaps.json',{'quarantined':[r for r in final if r['status']=='quarantined'],
        'restoration_unresolved':materials['unresolved'],'remaining_candidate_mapping_gaps':read(output/'gaps.json')['candidate_mapping_gaps'],
        'class_hierarchy_semantic_review':'not_complete','paper_human_gold':'not_created'})
    release=freeze(output,base,materials,final,audit_name='final_definition_audit.json')
    with repo.connect() as connection:
        requests=[dict(r) for r in connection.execute('SELECT attempt_id,state,metadata_json,result_json FROM request_attempts')
                  if json.loads(r['metadata_json'])['task_type']=='ontology_definition_audit_v1']
        outbox=connection.execute('SELECT COUNT(*) FROM import_outbox').fetchone()[0]
    details=[{'attempt_id':r['attempt_id'],'state':r['state'],**{k:v.get(k) for k in
        ('input_tokens','output_tokens','client_request_id','response_id','response_status','error_code')}}
        for r in requests for v in [json.loads(r['result_json']or'{}')]]
    unchanged=hashlib.sha256(Path(prep['source_database']).read_bytes()).hexdigest()==prep['source_database_sha256']
    result={'status':'research_subset_frozen','frozen_release_path':str(release.path) if release else None,
        'definition_count':len(final),'model_unanimous_pass':sum(r['status']=='machine_approved' for r in rows),
        'final_included':sum(r['status']=='machine_approved' for r in final),'final_quarantined':sum(r['status']=='quarantined' for r in final),
        'source_semantic_blockers':len(blockers['findings']),'new_requests':details,
        'known_input_tokens':sum(r.get('input_tokens')or 0 for r in details),'known_output_tokens':sum(r.get('output_tokens')or 0 for r in details),
        'new_unknown_requests':sum(r['state'] in {'running','unknown'} for r in details),
        'local_validation_recovery_calls':0,'unknown_requests_resent':0,'price':None,
        'original_failed_receipts_preserved':True,'source_database_unchanged':unchanged,'isolated_import_outbox':outbox,
        'production_written':False,'human_confirmed':False,'class_hierarchy_semantically_recertified':False}
    write(output/'audit_result.json',result)
    files=[Path(__file__),ROOT/'evaluation/freeze_shared_ontology.py',ROOT/'ontology_review/definition_audit.py',ROOT/'tests/test_definition_audit.py']
    write(output/'finalization_code_fingerprints.json',{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    with ZipFile(output/'finalization_execution_code.zip','x',ZIP_DEFLATED) as archive:
        for p in files:archive.write(p,p.relative_to(ROOT).as_posix())
    if not unchanged or len(requests)>prep['max_new_calls'] or outbox:raise ValueError('freeze isolation or request cap failed')
    print(json.dumps({k:result[k] for k in ('status','final_included','final_quarantined','new_unknown_requests')},ensure_ascii=False),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--recover-last-batch-contract',action='store_true')
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(ROOT/'outputs'):raise ValueError('isolated outputs only')
    if args.recover_last_batch_contract:recover_last_batch(output)
    else:finalize(output)


if __name__=='__main__':main()
