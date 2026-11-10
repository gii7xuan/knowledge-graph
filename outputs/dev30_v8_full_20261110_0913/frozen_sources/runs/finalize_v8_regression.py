"""Read-only final accounting and preservation checks for a completed V8 regression."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
from evaluation.review_samples import ReadOnlyRepository
from review_agent.contracts import sha256_json
from review_agent.review_observability import assess_vote
from review_agent.consensus import aggregate_votes
from runs.revalidate_v8 import committee
from runs.regress_v8 import fingerprints,ROOT
from runs.revalidate_v8 import read,save,digest


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True);args=parser.parse_args()
    out=args.directory.resolve();progress=read(out/'progress.json');result=read(out/'smoke_report.json')
    if progress.get('status')=='running' or progress.get('active'):raise ValueError('execution still active')
    repo=ReadOnlyRepository(out/'review.sqlite3');bindings=read(out/'sample_bindings.json');protected=read(out/'preserved_inputs.json')
    changed_files=[name for name,h in protected['sha256'].items() if digest(ROOT/name)!=h]
    production=ReadOnlyRepository(ROOT/'runs/v3/review.sqlite3')
    changed_candidates=[cid for cid,h in protected['production_candidate_hashes'].items() if sha256_json(production.get_revision(cid))!=h]
    code_ok=read(out/'code_fingerprints.json')==fingerprints()
    checks={'historical_file_hashes_unchanged':not changed_files,'changed_historical_files':changed_files,
        'production_candidate_hashes_unchanged':not changed_candidates,'changed_production_candidates':changed_candidates,
        'execution_code_unchanged':code_ok,'production_written':False}
    save(out,'protection_verification.json',checks)
    if changed_files or changed_candidates or not code_ok:raise ValueError('preservation/version check failed; inspect protection_verification.json')
    with repo.connect() as c:
        attempts=[(r['state'],json.loads(r['metadata_json'] or '{}'),json.loads(r['result_json'] or '{}'))
            for r in c.execute('SELECT state,metadata_json,result_json FROM request_attempts')]
        audit_counts=dict(c.execute('SELECT status,COUNT(*) FROM audit_runs GROUP BY status'))
        outbox=c.execute('SELECT COUNT(*) FROM import_outbox').fetchone()[0]
    cases=result['cases'];breakdown=Counter();by_seat=Counter();unavailable=[];execution_rows=[];late_rows=[];late_packets=[]
    for row in cases:
        decision=repo.latest_decision(row['candidate_id']) or {}
        o=row['outcome'];execution_rows.append({'sample_id':row['sample_id'],'candidate_id':row['candidate_id'],
            'audit_id':row['audit_id'],'execution_status':o['execution_status'],'execution_reason':o['execution_reason'],
            'selected_proposal_digest':decision.get('selected_proposal_digest'),
            'independent_review_completed':bool(decision.get('independent_review_completed')),
            'whole_vote_eligible_voter_ids':o['whole_vote_eligible_voter_ids'],'dimension_results':o['dimension_results'],
            'review_completed':o['review_completed'],'recommendation':o['recommendation'],'action_taken':o['action_taken'],
            'blocking_reasons':o['blocking_reasons']})
        for voter,validation in o['vote_validation'].items():
            for error in validation['errors']:breakdown[error['category']]+=1;by_seat[voter+':'+error['category']]+=1
            if not validation['whole_vote_eligible']:unavailable.append({'sample_id':row['sample_id'],'voter_id':voter,'errors':validation['errors']})
        if row['audit_id'] and decision.get('selected_proposal_digest'):
            audit=repo.get_audit(row['candidate_id'],row['audit_id'])
            proposal=next(p for p in audit['rounds'] if p['proposal_digest']==decision['selected_proposal_digest'])
            original=[v for v in audit['votes'] if v['proposal_digest']==proposal['proposal_digest']]
            recovered=deepcopy(original);accepted=[]
            for i,vote in enumerate(original):
                if vote.get('response_status')!='timeout' or not vote.get('attempt_id'):continue
                attempt=repo.request_attempt(vote['attempt_id']);receipt=attempt.get('result') or {}
                assessment=assess_vote(receipt,proposal)
                bound=attempt.get('metadata',{}).get('binding') or {}
                binding_ok=bool(bound) and all(receipt.get(k)==value for k,value in bound.items())
                usable=attempt['state']=='completed' and receipt.get('late_response') is True and binding_ok and assessment['whole_vote_eligible']
                late_rows.append({'sample_id':row['sample_id'],'candidate_id':row['candidate_id'],'audit_id':row['audit_id'],
                    'proposal_digest':proposal['proposal_digest'],'voter_id':vote['voter_id'],'attempt_id':vote['attempt_id'],
                    'selected_status':vote['response_status'],'selected_latency_ms':vote.get('latency_ms'),
                    'receipt_state':attempt['state'],'receipt_status':receipt.get('response_status'),
                    'late_response':receipt.get('late_response'),'receipt_latency_ms':receipt.get('latency_ms'),
                    'raw_response_hash':assessment['raw_response_hash'],'binding_verified':binding_ok,
                    'eligible_for_offline_reaggregation':usable,'validation_errors':assessment['errors'],
                    'raw_reference':str(out/'review.sqlite3')+'#request_attempts/'+vote['attempt_id']+'/result_json'})
                if usable:recovered[i]=deepcopy(receipt);accepted.append(vote['voter_id'])
            if accepted:
                fixed=aggregate_votes(recovered,committee(proposal),proposal=proposal,proposal_digest=proposal['proposal_digest'])
                late_packets.append({'sample_id':row['sample_id'],'audit_id':row['audit_id'],'proposal_digest':proposal['proposal_digest'],
                    'original_eligible_voter_ids':o['whole_vote_eligible_voter_ids'],'late_eligible_voter_ids':accepted,
                    'reaggregated_eligible_voter_ids':fixed['eligible_voter_ids'],'review_axes':fixed.get('review_axes'),
                    'all_required_seats_complete':set(fixed['eligible_voter_ids'])==set(committee(proposal).voter_ids)})
    usage={key:sum(r.get(key) or 0 for _,_,r in attempts) for key in ('input_tokens','output_tokens')}
    usage['requests_with_missing_usage']=sum(any(type(r.get(k)) is not int for k in ('input_tokens','output_tokens')) for _,_,r in attempts)
    usage['scope']='durable committee request attempts; planner usage not included'
    old=read(ROOT/'outputs/dev30_v8_revalidated_20261109_180621/summary.json')
    old_bindings=read(ROOT/'outputs/dev30_v8_rerun_20261109/sample_bindings.json')
    population_key=lambda rows:sorted((r['candidate_id'],r['revision'],r['candidate_content_hash']) for r in rows)
    same_population=population_key(bindings)==population_key(old_bindings)
    full=sum(len(r['whole_vote_eligible_voter_ids'])==len(read(out/'settings.json')['committee_members']) for r in execution_rows)
    value={'mode':'fresh_complete_pipeline','population':len(bindings),'audits_started':sum(bool(r['audit_id']) for r in cases),
        'audit_status_counts':audit_counts,'derived_execution_counts':dict(Counter(r['execution_status'] for r in execution_rows)),
        'independent_review_completed':sum(r['independent_review_completed'] for r in execution_rows),
        'full_committee_packets':full,'four_dimensions_judgment_complete':sum(r['review_completed'] for r in execution_rows),
        'dimension_complete':{a:sum(r['dimension_results'][a]['judgment_complete'] for r in execution_rows) for a in ('source','expression','mapping','scope')},
        'recommendations':dict(Counter(r['recommendation'] for r in execution_rows)),
        'actual_actions':dict(Counter(r['action_taken'] for r in execution_rows)),
        'request_attempts':len(attempts),'request_states':dict(Counter(s for s,_,_ in attempts)),
        'request_phases':dict(Counter((m.get('feedback') or {}).get('kind') or 'initial' for _,m,_ in attempts)),
        'planner_calls':result['planner_calls'],'cache_hits':sum(bool(r.get('cached')) for _,_,r in attempts),'usage':usage,
        'reported_models':{v:sorted({r['returned_model_id'] for _,m,r in attempts if (m.get('binding') or {}).get('voter_id')==v and r.get('returned_model_id')})
            for v in ('judge_a','judge_b','judge_c')},'backend_weights_pinned':False,
        'engineering_gate_passed':result['engineering_gate_passed'],'known_trace_defect_recurred':result['known_trace_defect_recurred'],
        'late_receipts':{'selected_timeout_seats':len(late_rows),'later_valid_seats':sum(r['eligible_for_offline_reaggregation'] for r in late_rows),
            'additional_full_committee_packets':sum(r['all_required_seats_complete'] and len(r['original_eligible_voter_ids'])<len(r['reaggregated_eligible_voter_ids']) for r in late_packets),
            'offline_only':True,'extra_model_calls':0,'original_votes_changed':False,'original_decisions_changed':False},
        'import_outbox':outbox,'human_labels':0,'quality_evaluable':False,'production_written':False,
        'comparison':{'same_candidate_population':same_population,'prior_original_independent_complete':old['historical_independent_review_completed'],
            'prior_offline_full_committee_packets':old['full_roster_selected_candidates'],'current_fresh_full_committee_packets':full,
            'note':'different execution budgets/clarification policy; no causal accuracy claim; offline revalidation is not a fresh call'}}
    save(out,'summary.json',value)
    save(out,'late_response_reconciliation.json',{'mode':'offline_revalidation_of_existing_late_receipts','model_calls':0,
        'original_votes_changed':False,'original_decisions_changed':False,
        'not_counted_as_within_original_wait_deadline':True,'receipts':late_rows,'derived_packets':late_packets})
    save(out,'decision_results.jsonl',execution_rows,jsonl=True)
    save(out,'failure_breakdown.json',{'unit':'error occurrences, not distinct failed candidates','by_category':dict(breakdown),'by_seat_category':dict(by_seat),
        'unavailable_recorded_seats':unavailable,'missing_seats':{r['sample_id']:r['dimension_results']['scope']['missing_voter_ids'] for r in execution_rows if r['dimension_results']['scope']['missing_voter_ids']}})
    old_completion=read(out/'completion.json') if (out/'completion.json').exists() else {}
    save(out,'completion.json',{'completed_at':old_completion.get('completed_at') or datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'status':progress['status'],'summary':value,'protection':checks,'report_script_sha256':digest(Path(__file__)),
        'report_generator_sha256':digest(ROOT/'runs/report_v8_regression.py')})
    print(json.dumps(value,ensure_ascii=False))


if __name__=='__main__':main()
