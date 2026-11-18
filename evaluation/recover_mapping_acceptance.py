"""Bounded field-only recovery of engineering fixtures, retaining original decisions."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .freeze_shared_ontology import ROOT,read,write
from .mapping_acceptance import acceptance
from review_agent.contracts import sha256_json


def main():
    from review_agent.repository import ReviewRepository
    from review_agent.case_budget import CaseBudget
    from review_agent.cli import _configured_validators
    from review_agent.validators import judge_validator,model_request_count,vote_binding
    from review_agent.request_cache import validation_request_key
    from review_agent.review_observability import outcome
    from runs.regress_v8 import configuration
    from runs.revalidate_v8 import committee
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--freeze',type=Path,required=True)
    parser.add_argument('--continue-preflight',action='store_true',help='Continue a stopped local preflight only when no new request was sent')
    args=parser.parse_args();output=args.output.resolve();frozen=args.freeze.resolve()
    if not output.is_relative_to(ROOT/'outputs'):raise ValueError('isolated output only')
    original_report=read(output/'acceptance_report.json');fixtures=read(output/'fixtures.json')
    preparation=read(frozen/'preparation.json');audit_cost=read(frozen/'audit_result.json')
    repo=ReviewRepository(output/'review.sqlite3')
    if (output/'recovery_started.json').exists():
        with repo.connect() as connection:count=connection.execute('SELECT COUNT(*) FROM request_attempts').fetchone()[0]
        if (not args.continue_preflight or count!=len(original_report['request_details'])
                or any(output.glob('*_recovery.json'))):raise ValueError('bounded recovery already dispatched; inspect instead of repeating')
        write(output/'recovery_preflight_continued.json',{'new_calls_before_continuation':0,'reason':'remove persisted consensus/gaps annotations from original frozen request view'})
    else:
        write(output/'recovery_started.json',{'max_new_calls':9,'known_contract_corrections_per_seat':1,
            'original_report_hash':sha256_json(original_report),'unknown_resends':0,'valid_votes_changed':False})
    config=configuration(profile='weighted_diagnostic',directory=output)
    config['budget'].update(max_llm_calls_per_candidate=3,max_total_tokens=100000,total_timeout_seconds=360)
    voters={v['id']:v for v in _configured_validators(config)}
    rows=[]
    for name,candidate in fixtures['candidates'].items():
        original=read(output/(name+'_audit.json'))
        proposal={k:v for k,v in original['rounds'][-1].items() if k not in {'consensus','gaps'}}
        previous=read(output/(name+'_result.json'));votes=[]
        case=repo.ensure_case(candidate['candidate_id'])
        recoverable=sum((repo.request_attempt(v['attempt_id']).get('result')or{}).get('response_status')
                        in {'invalid_contract','invalid_reference','invalid_json'} for v in original['votes'])
        if recoverable:
            repo.authorize_case_budget(candidate['candidate_id'],{'llm_calls':recoverable,'tokens':100000},
                reason='One field-only correction per known malformed engineering-fixture response; no semantic reroll or unknown resend',
                actor='codex_user_authorized',authorization_key=output.name+':field-only:'+name)
        budget=CaseBudget(config['budget'],repo,[case],task_type='mapping_acceptance_field_correction',task_id=name)
        for old in original['votes']:
            record=repo.request_attempt(old['attempt_id']);receipt=record.get('result')or{}
            voter=voters[old['voter_id']]
            if old.get('response_status')=='ok':
                votes.append(old);continue
            if record['metadata']['proposal']!=proposal:raise ValueError('receipt proposal changed')
            if record['state']=='completed' and receipt.get('response_status')=='ok':
                if any(receipt.get(k)!=v for k,v in vote_binding(voter,proposal).items()):raise ValueError('late receipt binding mismatch')
                vote={**receipt,'attempt_id':old['attempt_id'],'late_original_receipt_recovery':True}
                budget.tokens(vote);votes.append(vote);continue
            if (old.get('outcome_unknown') and record['state']=='failed' and receipt.get('raw_response')
                    and not receipt.get('outcome_unknown')):
                if any(receipt.get(k)!=v for k,v in vote_binding(voter,proposal).items()):raise ValueError('late failed receipt binding mismatch')
                old={**receipt,'attempt_id':old['attempt_id'],'request_key':old['request_key'],
                     'late_original_receipt_recovery':True}
                budget.tokens(old)
            if old.get('outcome_unknown') or record['state'] in {'running','unknown'}:
                votes.append(old);continue
            if old.get('response_status') not in {'invalid_contract','invalid_reference','invalid_json'}:
                votes.append(old);continue
            cap=record['metadata']['generation']['max_output_tokens']
            voter={**voter,'case_ids':[case],'max_output_tokens':cap,'correction_token_budget':100000}
            if validation_request_key(voter,proposal)!=old['request_key']:raise ValueError('recovery would change the frozen request key')
            with repo.connect() as connection:
                count=connection.execute('SELECT COUNT(*) FROM request_attempts').fetchone()[0]
                known=sum(sum(json.loads(r[0]or'{}').get(k)or 0 for k in ('input_tokens','output_tokens')) for r in connection.execute('SELECT result_json FROM request_attempts'))
            if count+len(audit_cost['new_requests'])>=preparation['max_new_calls'] or known+audit_cost['known_input_tokens']+audit_cost['known_output_tokens']>=preparation['max_known_tokens']:
                votes.append(old);continue
            # targeted_validation reserves a conservative UTF-8 input/output upper bound.
            # Pass the remaining shared allowance rather than a fresh per-seat token cap.
            voter['correction_token_budget']=max(0,preparation['max_known_tokens']-known
                -audit_cost['known_input_tokens']-audit_cost['known_output_tokens'])
            if not budget.reserve_calls(1):votes.append(old);continue
            vote=judge_validator(voter,proposal,max_retries=0,per_call_timeout_seconds=120,
                                 repository=repo,deadline_epoch=budget.deadline_epoch)
            budget.refund_calls(1-model_request_count(vote))
            for attempt in vote.get('attempts')or[vote]:budget.tokens(attempt)
            votes.append(vote)
        derived=outcome(proposal,votes,committee(proposal),audit_status='completed')
        projected={**previous,'review_outcome':derived,'research_operation':derived.get('research_operation'),
                   'weight_snapshot':derived.get('weight_snapshot',{})}
        audit={**original,'votes':votes}
        write(output/(name+'_recovery.json'),{'original_audit_hash':sha256_json(original),'proposal':proposal,
            'votes':votes,'projected_outcome':derived,'original_decision_changed':False,'budget':budget.snapshot()})
        row=acceptance(name,projected,audit,candidate);rows.append(row)
        print(json.dumps({'fixture':name,'passed':row['passed'],'dimensions':row['dimension_statuses']},ensure_ascii=False),flush=True)
    with repo.connect() as connection:
        requests=[dict(r) for r in connection.execute('SELECT attempt_id,state,result_json FROM request_attempts')]
        outbox=connection.execute('SELECT COUNT(*) FROM import_outbox').fetchone()[0]
    details=[{'attempt_id':r['attempt_id'],'state':r['state'],**{k:v.get(k) for k in
        ('input_tokens','output_tokens','response_status','error_code','client_request_id')}} for r in requests for v in [json.loads(r['result_json']or'{}')]]
    known=sum((r.get('input_tokens')or 0)+(r.get('output_tokens')or 0) for r in details)
    source_unchanged=hashlib.sha256(Path(preparation['source_database']).read_bytes()).hexdigest()==preparation['source_database_sha256']
    write(output/'recovered_acceptance_report.json',{'passed':all(r['passed'] for r in rows) and original_report['additional_structural_type_conflict_detected'],
        'fixtures':rows,'additional_structural_type_conflict_detected':original_report['additional_structural_type_conflict_detected'],
        'request_details':details,'new_field_correction_calls':len(requests)-len(original_report['request_details']),
        'mapping_known_tokens':known,'cumulative_preparation_requests':len(requests)+len(audit_cost['new_requests']),
        'cumulative_known_preparation_tokens':known+audit_cost['known_input_tokens']+audit_cost['known_output_tokens'],
        'known_token_stop_overshoot':max(0,known+audit_cost['known_input_tokens']+audit_cost['known_output_tokens']-preparation['max_known_tokens']),
        'new_unsettled_requests':sum(r['state'] in {'running','unknown'} for r in details),
        'source_database_unchanged':source_unchanged,'isolated_import_outbox':outbox,'production_written':False,
        'original_decisions_changed':False,'human_gold':False,'paper_accuracy':None})
    write(output/'recovery_code_fingerprints.json',{str(Path(__file__).relative_to(ROOT)):hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    if len(requests)+len(audit_cost['new_requests'])>preparation['max_new_calls'] or outbox or not source_unchanged:
        raise ValueError('recovery isolation or cumulative request cap failed')


if __name__=='__main__':main()
