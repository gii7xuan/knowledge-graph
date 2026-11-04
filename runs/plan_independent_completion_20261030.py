"""Snapshot current independent-review gaps and classify exact frozen seats."""
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import json
import sqlite3

from review_agent.agent_state import AgentState
from review_agent.cli import _configured_validators
from review_agent.repository import ReviewRepository
from review_agent.service import load_config
from review_agent.validation_recovery import recovery_plan

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/independent_completion_20261030'
OUT.mkdir(exist_ok=True)
RUN='run_chapter1_20261028_resume1'


if __name__=='__main__':
    saved=OUT/('before_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
    source=sqlite3.connect((ROOT/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=15)
    target=sqlite3.connect(saved)
    try: source.backup(target)
    finally: target.close();source.close()
    repo=ReviewRepository(saved)
    members=[{key:v[key] for key in ('id','model_id','model_family')} for v in _configured_validators(load_config())]
    rows=[]
    for candidate in repo.list_candidates(RUN,limit=1000000):
        row={key:candidate.get(key) for key in ('candidate_id','revision','row_version','workflow_state','human_status')}
        audit=repo.get_audit(candidate['candidate_id'])
        row.update(audit_id=audit['audit_id'],audit_revision=audit['revision'],
            independent_review_completed=bool(audit['context'].get('independent_review_completed')),
            rounds=len(audit['rounds']),contract=audit['context'].get('response_contract_version'))
        if audit['revision']!=candidate['revision']:
            row['action']='fresh_current_revision_review'
        elif row['independent_review_completed']:
            row['action']='already_completed'
        elif not audit['rounds']:
            row['action']='fresh_no_vote_review'
        elif (audit.get('agent_state') or {}).get('state'):
            state=AgentState(**audit['agent_state']['state'])
            plan=recovery_plan(state,repo,allow_permanent_failure=True)
            row.update(plan)
            row['action']='recover_exact_frozen_seats' if plan['recoverable_voter_ids'] else 'unknown_request_reconciliation' if plan['unknown_voter_ids'] else 'partial_checkpoint_reconciliation' if not state.current_validation() else 'existing_votes_reconciliation'
            selected=state.current_validation()
            if selected:
                row['seats']=[{key:vote.get(key) for key in ('voter_id','response_status','error_code','outcome_unknown','request_key')} for vote in selected['votes']]
                for seat in row['seats']:
                    entry=repo.validation_request(seat['request_key']) if seat.get('request_key') else None
                    seat['request_status']=entry['status'] if entry else None
                    if seat.get('request_key'):
                        history=repo.request_history(seat['request_key'])
                        seat['latest_request_error']=(history[-1].get('result') or {}).get('error_code') if history else None
        else:
            row['action']='missing_checkpoint_reconciliation'
        rows.append(row)
    with repo.connect() as connection:
        running=[dict(row) for row in connection.execute("SELECT job_type,status,COUNT(*) n FROM jobs WHERE status IN ('running','queued') GROUP BY job_type,status")]
        request_states=[dict(row) for row in connection.execute('SELECT status,COUNT(*) n FROM validation_requests GROUP BY status')]
    result={'run_id':RUN,'snapshot':str(saved),'checked_at_utc':datetime.now(timezone.utc).isoformat(),
        'total':len(rows),'completed':sum(row['independent_review_completed'] and row['revision']==row['audit_revision'] for row in rows),
        'actions':dict(Counter(row['action'] for row in rows)),'committee':members,
        'running_jobs':running,'request_states':request_states,'model_calls':0,'production_database_modified':False,'items':rows}
    (OUT/'plan.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in result.items() if key!='items'},ensure_ascii=False,indent=2))
