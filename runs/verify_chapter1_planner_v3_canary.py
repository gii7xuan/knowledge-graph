"""Validate real committee tickets and action ordering before the full restart."""
from datetime import datetime,timezone
import argparse
import json
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
JOB=ROOT/'runs/review_jobs/chapter1_20261028_planner_v3_canary'
OUT=ROOT/'runs/review_jobs/chapter1_20261028_contract_fix'


def main():
    global JOB
    parser=argparse.ArgumentParser()
    parser.add_argument('--job')
    parser.add_argument('--report-name',default='live_canary_verification.json')
    args=parser.parse_args()
    if args.job: JOB=Path(args.job).resolve()
    from review_agent.repository import ReviewRepository
    result=json.loads((JOB/'batch_result.json').read_text(encoding='utf-8'))
    assert result['status']=='completed' and result['completed']==result['selected'] and result['selected']>0 and result['failed']==0
    assert result['independent_review_completed']==result['selected'] and result['review_incomplete']==0
    repository=ReviewRepository(ROOT/'runs/v3/review.sqlite3')
    rows=[]
    for completed in result['results']:
        record=completed['result']
        audit=repository.get_audit(completed['candidate_id'],completed['audit_id'])
        assert audit['context']['planner_contract_version']=='planner_action_v3'
        assert audit['context']['review_completion_status']=='independent_review_completed'
        round_id=record['final_round_id']
        proposal=next(p for p in audit['rounds'] if p['round_id']==round_id)
        votes=[v for v in audit['votes'] if v['round_id']==round_id]
        assert {v['voter_id'] for v in votes}=={'judge_a','judge_b','judge_c'}
        assert len({v['model_family'] for v in votes})==3
        allowed={i['evidence_id'] for i in proposal['evidence_bundle']['items']}
        for vote in votes:
            assert vote['response_status']=='ok'
            assert vote['proposal_digest']==proposal['proposal_digest']
            assert vote['evidence_hash']==proposal['evidence_bundle']['evidence_hash']
            assert vote['object_revision']==completed['revision']
            assert vote['task_id']==completed['candidate_id'] and vote['task_type']=='triple_review'
            assert set(vote.get('supporting_evidence_ids',[])+vote.get('counter_evidence_ids',[]))<=allowed
        calls=audit['tool_calls']
        assert not any(t['status']=='invalid_action' for t in calls)
        ontology_step=min(t['step_no'] for t in calls if t['tool']=='inspect_ontology' and t['status']=='ok')
        validation_step=min(t['step_no'] for t in calls if t['tool']=='run_validation' and t['status']=='ok')
        assert ontology_step<validation_step
        for call in calls:
            if call['tool'] in {'finish_review','request_ontology_extension'}:
                assert call['step_no']>validation_step
        rows.append({'candidate_id':completed['candidate_id'],'audit_id':completed['audit_id'],
            'review_completion_status':record['review_completion_status'],
            'tools':[{'tool':t['tool'],'status':t['status']} for t in calls],
            'committee_models':[v['model_id'] for v in votes],
            'valid_votes':len(votes),'source_support':record['source_support_result'],
            'workflow_state':record['workflow_state'],'decision':record['decision'],
            'stop_reason':record.get('stop_reason'),
            'invalid_planner_attempts':sum(a.get('response_status')=='invalid_action'
                for step in audit['planner_steps'] for a in step.get('attempts',[])),
            'budget':record['budget']})
    with repository.connect() as connection:
        with sqlite3.connect(OUT/'after_canary.sqlite3') as backup: connection.backup(backup)
    summary={'status':'passed','checked_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'candidate_count':result['selected'],'skipped_waiting_or_healthy':result['skipped'],
        'valid_current_proposal_votes':result['selected']*3,'distinct_model_families':3,
        'invalid_tool_calls':0,'invalid_planner_attempts':sum(r['invalid_planner_attempts'] for r in rows),
        'ontology_before_validation':True,'validation_before_finish_or_extension':True,
        'frozen_vote_bindings_verified':True,'review_completion_distinguished_from_execution':True,
        'independent_review_completed':result['selected'],'cases':rows}
    (OUT/args.report_name).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='cases'},ensure_ascii=False,indent=2))
    print(json.dumps([{k:r[k] for k in ('candidate_id','tools','source_support','workflow_state','decision','stop_reason','invalid_planner_attempts')} for r in rows],ensure_ascii=False,indent=2))


if __name__=='__main__': main()
