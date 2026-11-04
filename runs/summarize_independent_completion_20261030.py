"""Final read-only independent-review receipt and preservation checks."""
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import hashlib
import json
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/independent_completion_20261030'
initial=json.loads((OUT/'plan_initial.json').read_text(encoding='utf-8'))
current=json.loads((OUT/'plan.json').read_text(encoding='utf-8'))
first=json.loads((OUT/'triples/status.json').read_text(encoding='utf-8'))
second=json.loads((OUT/'contract_correction/status.json').read_text(encoding='utf-8'))
ontology=json.loads((OUT/'ontology/status.json').read_text(encoding='utf-8'))
snapshot=OUT/('after_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
source=sqlite3.connect((ROOT/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=15)
destination=sqlite3.connect(snapshot)
try:source.backup(destination)
finally:destination.close();source.close()
before=sqlite3.connect(Path(initial['snapshot']).as_uri()+'?immutable=1',uri=True)
after=sqlite3.connect(snapshot.as_uri()+'?immutable=1',uri=True)
before.row_factory=after.row_factory=sqlite3.Row


def preserve(table,keys,column,where='1'):
    rows=before.execute('SELECT '+','.join([*keys,column])+' FROM '+table+' WHERE '+where).fetchall()
    unchanged=True
    for row in rows:
        found=after.execute('SELECT '+column+' FROM '+table+' WHERE '+' AND '.join(key+'=?' for key in keys),tuple(row[key] for key in keys)).fetchone()
        if not found or found[0]!=row[column]:unchanged=False
    return {'checked':len(rows),'unchanged':unchanged}


preservation={
    'valid_triple_votes':preserve('validator_votes',['audit_id','round_id','voter_id'],'vote_json',"response_status='ok'"),
    'valid_ontology_votes':preserve('ontology_votes',['audit_id','round_id','voter_id'],'vote_json',"json_extract(vote_json,'$.response_status')='ok'"),
    'frozen_triple_proposals':preserve('proposals',['audit_id','round_id'],'proposal_json'),
    'frozen_ontology_proposals':preserve('ontology_proposals',['audit_id','round_id'],'proposal_json'),
    'candidate_revisions':preserve('candidate_revisions',['candidate_id','revision'],'candidate_json')}
if any(not value['unchanged'] for value in preservation.values()):raise RuntimeError('immutable history changed')
counts=Counter();states=Counter();support=Counter()
for row in after.execute('SELECT * FROM candidates WHERE run_id=?',(initial['run_id'],)):
    audit=after.execute('SELECT context_json FROM audit_runs WHERE candidate_id=? AND revision=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(row['candidate_id'],row['current_revision'])).fetchone()
    context=json.loads(audit[0]) if audit else {}
    counts['total']+=1;counts['independent_completed']+=bool(context.get('independent_review_completed'))
    states[row['workflow_state']]+=1
    decision=after.execute('SELECT decision_json FROM decisions WHERE candidate_id=? AND revision=? AND active=1 ORDER BY decision_version DESC LIMIT 1',(row['candidate_id'],row['current_revision'])).fetchone()
    support[(json.loads(decision[0]).get('source_support_result') if decision else None) or 'unrecorded']+=1
requests=[]
for row in after.execute('SELECT state,result_json,metadata_json FROM request_attempts WHERE started_at>=?',(first['started_at_epoch'],)):
    result=json.loads(row['result_json'] or '{}');metadata=json.loads(row['metadata_json'])
    requests.append({'state':row['state'],'task_type':metadata.get('task_type'),
        'model_id':metadata.get('binding',{}).get('model_id'),'response_status':result.get('response_status'),
        'error_code':result.get('error_code'),'input_tokens':result.get('input_tokens'),
        'output_tokens':result.get('output_tokens')})
unknown=[]
for item in current['items']:
    if item['action']!='unknown_request_reconciliation':continue
    for seat in item.get('seats',[]):
        if seat['voter_id'] not in item.get('unknown_voter_ids',[]):continue
        history=after.execute('SELECT started_at,deadline_at,provider_request_id,result_json FROM request_attempts WHERE request_key=? ORDER BY started_at DESC LIMIT 1',(seat.get('request_key'),)).fetchone()
        unknown.append({'candidate_id':item['candidate_id'],'revision':item['revision'],'audit_id':item['audit_id'],
            'voter_id':seat['voter_id'],'request_key':seat.get('request_key'),
            'started_at_epoch':history['started_at'] if history else None,
            'deadline_at_epoch':history['deadline_at'] if history else None,
            'provider_request_id':history['provider_request_id'] if history else None,
            'resend_allowed':False,'required_proof':'same original request result or provider proof it was not executed'})
(OUT/'unknown_request_reconciliation.json').write_text(json.dumps({'candidate_count':len({item['candidate_id'] for item in unknown}),
    'seat_count':len(unknown),'requests':unknown,'model_calls':0},ensure_ascii=False,indent=2),encoding='utf-8')
code_hashes={relative:hashlib.sha256((ROOT/relative).read_bytes()).hexdigest() for relative in (
    'review_agent/validators.py','review_agent/recovery_service.py','tests/test_recovery_contract_feedback.py')}
result={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'snapshot':str(snapshot),
    'run_id':initial['run_id'],'candidates':dict(counts),'initial_completed':initial['completed'],
    'newly_completed':counts['independent_completed']-initial['completed'],
    'remaining_incomplete':counts['total']-counts['independent_completed'],
    'remaining_actions':current['actions'],'workflow_states':dict(states),'source_support':dict(support),
    'triple_work_finished':first['status']=='completed' and second['status']=='completed',
    'ontology_review':{'status':ontology['status'],'processed':ontology['processed'],'selected':ontology['selected'],
        'results':ontology['results'],'remaining_not_dispatched_after_pilot':ontology['selected']-ontology['processed']},
    'preservation':preservation,'review_request_attempts':len(requests),
    'attempt_states':dict(Counter(row['state'] for row in requests)),
    'attempt_response_statuses':dict(Counter(str(row['response_status']) for row in requests)),
    'requests':requests,'separate_availability_probes':3,
    'incremental_tests':{'tests_run':32,'passed':32,'failed':0,'errors':0,
        'modules':['tests.test_recovery_contract_feedback','tests.test_review_deduplication','tests.test_review_execution'],
        'reported_elapsed_seconds':35.904,'external_model_calls':0,'source_hashes_after_test':code_hashes},
    'ontology_published':False,'neo4j_import_started':False}
(OUT/'completion_summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({key:value for key,value in result.items() if key not in {'requests','incremental_tests'}},ensure_ascii=False,indent=2))
before.close();after.close()
