"""Inspect frozen review recovery options; no model requests or audit changes."""
from collections import Counter
from pathlib import Path
import json
from review_agent.agent_state import AgentState
from review_agent.repository import ReviewRepository
from review_agent.validation_recovery import recovery_plan

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_resume_20261029'
RUN='run_chapter1_20261028_resume1'


def plan(repository):
    candidates=repository.list_candidates(RUN,limit=1000000)
    rows=[]
    for candidate in candidates:
        item={k:candidate.get(k) for k in ('candidate_id','revision','human_status','workflow_state')}
        try:
            audit=repository.get_audit(candidate['candidate_id'])
        except Exception as exc:
            from review_agent.repository import NotFoundError
            if not isinstance(exc,NotFoundError): raise
            item['action']='new_review'
            rows.append(item);continue
        item.update(audit_id=audit['audit_id'],audit_status=audit['status'],
                    independent_review_completed=audit['context'].get('independent_review_completed',False))
        if candidate.get('human_status')!='pending':
            item['action']='human_completed'
        elif candidate.get('workflow_state') in {'awaiting_ontology_review','awaiting_ontology_release'}:
            item['action']='ontology_queue'
        elif audit['context'].get('independent_review_completed'):
            item['action']='independent_completed'
        elif audit.get('agent_state',{}).get('state'):
            state=AgentState(**audit['agent_state']['state'])
            if state.current_validation():
                item.update(recovery_plan(state,repository,allow_permanent_failure=True))
                item['action']='recover_failed_seats' if item['recoverable_voter_ids'] else 'waiting_reconciliation' if item['unknown_voter_ids'] else 'no_failed_seats'
            elif audit.get('rounds'):
                item['action']='recover_partial_round'
            else:
                item['action']='new_review'
        elif audit.get('rounds'):
            item['action']='checkpoint_missing'
        else:
            item['action']='new_review'
        rows.append(item)
    with repository.connect() as connection:
        jobs=[dict(r) for r in connection.execute('SELECT job_type,status,COUNT(*) count FROM jobs GROUP BY job_type,status')]
        pauses=[dict(r) for r in connection.execute('SELECT service_key,status FROM service_pauses')]
    return {'run_id':RUN,'counts':dict(Counter(r['action'] for r in rows)),
            'candidates':rows,'jobs':jobs,'service_pauses':pauses,'model_calls':0}


if __name__=='__main__':
    repository=ReviewRepository(ROOT/'runs/v3/review.sqlite3')
    result=plan(repository)
    OUT.mkdir(exist_ok=True)
    (OUT/'resume_plan.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='candidates'},ensure_ascii=False,indent=2))
