"""Compare immutable snapshots and saved runs without sending model requests."""
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
import json
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_quality_checks/chapter1_resume_20261029'
BASE=ROOT/'runs/review_recovery_20261029/before_apply_20261029_101209_833304.sqlite3'
RUN='run_chapter1_20261028_resume1'


def connect(path):
    c=sqlite3.connect(path.as_uri()+'?immutable=1',uri=True)
    c.row_factory=sqlite3.Row
    return c


def cohort(c):
    result=[]
    for row in c.execute('SELECT c.*,r.candidate_json FROM candidates c JOIN candidate_revisions r ON c.candidate_id=r.candidate_id AND c.current_revision=r.revision WHERE c.run_id=? ORDER BY c.candidate_id',(RUN,)).fetchall():
        a=c.execute('SELECT * FROM audit_runs WHERE candidate_id=? AND revision=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(row['candidate_id'],row['current_revision'])).fetchone()
        ctx=json.loads(a['context_json']) if a else {}
        decision=c.execute('SELECT decision_json FROM decisions WHERE candidate_id=? AND active=1 ORDER BY decision_version DESC LIMIT 1',(row['candidate_id'],)).fetchone()
        d=json.loads(decision[0]) if decision else {}
        result.append({'candidate_id':row['candidate_id'],'revision':row['current_revision'],'workflow_state':row['workflow_state'],
            'audit_id':a['audit_id'] if a else None,'audit_status':a['status'] if a else 'not_audited',
            'independent_completed':bool(ctx.get('independent_review_completed')),
            'completion_status':ctx.get('review_completion_status'),'decision':d.get('decision'),
            'source_support_result':d.get('source_support_result'),'context':ctx,'candidate':json.loads(row['candidate_json']),
            'decision_record':d})
    return result


def summarize(rows):
    return {'total':len(rows),'independent_completed':sum(r['independent_completed'] for r in rows),
        'completion_rate':round(sum(r['independent_completed'] for r in rows)/len(rows),4),
        **{k:dict(Counter(str(r.get(k)) for r in rows)) for k in ('audit_status','workflow_state','decision','source_support_result')}}


def audit_details(c,item):
    aid=item['audit_id']
    proposals=[json.loads(r[0]) for r in c.execute('SELECT proposal_json FROM proposals WHERE audit_id=? ORDER BY round_id',(aid,))]
    votes=[json.loads(r[0]) for r in c.execute('SELECT vote_json FROM validator_votes WHERE audit_id=? ORDER BY round_id,voter_id',(aid,))]
    tools=[{'tool':r[0],'status':r[1],**json.loads(r[2])} for r in c.execute('SELECT tool_name,status,record_json FROM agent_tool_calls WHERE audit_id=? ORDER BY step_no',(aid,))]
    selected=item['decision_record'].get('selected_round_id') or (proposals[-1]['round_id'] if proposals else None)
    current=[v for v in votes if v.get('round_id')==selected]
    return {**item,'proposals':proposals,'votes':votes,'selected_votes':current,'tool_calls':tools}


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    snapshot=OUT/('snapshot_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
    original=sqlite3.connect((ROOT/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=15)
    target=sqlite3.connect(snapshot)
    try: original.backup(target)
    finally: target.close();original.close()
    c=connect(snapshot);b=connect(BASE)
    before=cohort(b);after=cohort(c)
    fresh=json.loads((ROOT/'runs/review_resume_20261029/fresh_result.json').read_text(encoding='utf-8'))
    state=json.loads((ROOT/'runs/review_resume_20261029/status.json').read_text(encoding='utf-8'))
    new_ids={r['candidate_id'] for r in fresh['results']}
    recovery_ids={r['candidate_id'] for r in state['recovery_results']}|{state['initial_recovered_candidate']}
    details=[audit_details(c,r) for r in after if r['candidate_id'] in new_ids|recovery_ids]
    (OUT/'candidate_details.json').write_text(json.dumps(details,ensure_ascii=False,indent=2),encoding='utf-8')
    new=[r for r in details if r['candidate_id'] in new_ids]
    new_statuses=Counter(v['response_status'] for r in new for v in r['selected_votes'])
    new_model_status=defaultdict(Counter)
    for r in new:
        for v in r['selected_votes']: new_model_status[v['model_id']][v['response_status']]+=1
    new_reasons=Counter(reason for r in new for reason in r['decision_record'].get('reason_codes',[]))
    new_stops=Counter(str(r['context'].get('agent_stop_reason')) for r in new)
    new_tools=Counter((t['tool'],t['status']) for r in new for t in r['tool_calls'])
    start=state['started_at_epoch']
    attempts=[{**dict(r),'result':json.loads(r['result_json'] or '{}'),'metadata':json.loads(r['metadata_json'])}
        for r in c.execute('SELECT * FROM request_attempts WHERE started_at>=?',(start,))]
    # Imported historic rows are excluded by started_at; each record is one attempt, not one vote.
    request_counts=defaultdict(Counter)
    for a in attempts:
        request_counts[a['metadata'].get('task_type','unknown')][a['state']]+=1
    ontology=[]
    for a in c.execute('SELECT * FROM ontology_audits ORDER BY rowid'):
        result=json.loads(a['result_json'] or '{}')
        checkpoint=json.loads(a['checkpoint_json'] or '{}')
        ontology.append({'audit_id':a['audit_id'],'extension_id':a['extension_id'],'status':a['status'],
            'result':result,'checkpoint':checkpoint,
            'proposal_count':c.execute('SELECT COUNT(*) FROM ontology_proposals WHERE audit_id=?',(a['audit_id'],)).fetchone()[0],
            'votes':[json.loads(r[0]) for r in c.execute('SELECT vote_json FROM ontology_votes WHERE audit_id=?',(a['audit_id'],))]})
    (OUT/'ontology_details.json').write_text(json.dumps(ontology,ensure_ascii=False,indent=2),encoding='utf-8')
    immutable=[]
    recovered=[]
    for row in after:
        if row['candidate_id'] not in recovery_ids: continue
        old=next(v for v in before if v['candidate_id']==row['candidate_id'])
        old_votes=[json.loads(r[0]) for r in b.execute("SELECT vote_json FROM validator_votes WHERE audit_id=? AND response_status='ok'",(old['audit_id'],))]
        for vote in old_votes:
            current=c.execute('SELECT vote_json FROM validator_votes WHERE audit_id=? AND round_id=? AND voter_id=?',(old['audit_id'],vote['round_id'],vote['voter_id'])).fetchone()
            immutable.append({'candidate_id':row['candidate_id'],'voter_id':vote['voter_id'],'unchanged':bool(current and json.loads(current[0])==vote)})
        recovered.append({'candidate_id':row['candidate_id'],'before_completed':old['independent_completed'],'after_completed':row['independent_completed']})
    summary={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'baseline':str(BASE),'snapshot':str(snapshot),
        'before':summarize(before),'after':summarize(after),'fresh':{k:fresh[k] for k in ('selected','completed','failed','independent_review_completed','review_incomplete','revised','skipped')},
        'fresh_selected_vote_statuses':dict(new_statuses),'fresh_model_statuses':{k:dict(v) for k,v in new_model_status.items()},
        'fresh_reason_codes':dict(new_reasons),'fresh_stop_reasons':dict(new_stops),
        'fresh_tool_statuses':[{'tool':k[0],'status':k[1],'count':v} for k,v in new_tools.items()],
        'recovery':{'candidates':len(recovered),'newly_completed':sum(r['after_completed'] and not r['before_completed'] for r in recovered),
            'valid_old_votes_checked':len(immutable),'valid_old_votes_unchanged':all(r['unchanged'] for r in immutable)},
        'requests_since_batch_start':{k:dict(v) for k,v in request_counts.items()},
        'ontology':{'audits':len(ontology),'statuses':dict(Counter(r['status'] for r in ontology)),
            'recommendations':dict(Counter(str(r['result'].get('recommendation')) for r in ontology)),
            'stop_reasons':dict(Counter(str(r['result'].get('stop_reason')) for r in ontology)),
            'with_proposals':sum(r['proposal_count']>0 for r in ontology),'votes':sum(len(r['votes']) for r in ontology),
            'jobs':[dict(r) for r in c.execute('SELECT job_type,status,COUNT(*) count FROM jobs GROUP BY job_type,status')],
            'task_statuses':[dict(r) for r in c.execute('SELECT status,COUNT(*) count FROM ontology_extension_tasks GROUP BY status')]},
        'revision_drafts':[dict(r) for r in c.execute('SELECT status,COUNT(*) count FROM revision_drafts GROUP BY status')],
        'external_model_calls_for_assessment':0,'production_database_modified':False}
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    b.close();c.close()


if __name__=='__main__': main()
