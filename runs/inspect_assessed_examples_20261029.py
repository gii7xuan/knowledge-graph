from collections import Counter
from pathlib import Path
import json
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_quality_checks/chapter1_resume_20261029'
summary=json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
c=sqlite3.connect(Path(summary['snapshot']).as_uri()+'?immutable=1',uri=True);c.row_factory=sqlite3.Row
onto=json.loads((OUT/'ontology_details.json').read_text(encoding='utf-8'))
examples=[]
for reason in summary['ontology']['stop_reasons']:
    a=next(a for a in onto if a['result'].get('stop_reason')==reason)
    steps=[{'role':r[0],'step_no':r[1],'record':json.loads(r[2])} for r in c.execute('SELECT role,step_no,record_json FROM ontology_steps WHERE audit_id=? ORDER BY step_no',(a['audit_id'],))]
    examples.append({'audit_id':a['audit_id'],'stop_reason':reason,'result':a['result'],'last_steps':steps[-2:]})
details=json.loads((OUT/'candidate_details.json').read_text(encoding='utf-8'))
fresh=json.loads((ROOT/'runs/review_resume_20261029/fresh_result.json').read_text(encoding='utf-8'))
fresh_ids={r['candidate_id'] for r in fresh['results']}
case_examples=[]
for r in [r for r in details if r['candidate_id'] in fresh_ids][:6]:
    p=r['proposals'][-1]
    case_examples.append({'candidate_id':r['candidate_id'],'independent_completed':r['independent_completed'],
        'triple':r['candidate']['triple'],'decision':r['decision'],'stop_reason':r['context'].get('agent_stop_reason'),
        'source_support_result':r['source_support_result'],'reason_codes':r['decision_record'].get('reason_codes'),
        'evidence':[{'evidence_id':e['evidence_id'],'text':e.get('text') or e.get('quote') or e.get('content'),
            'sentence_ids':e.get('sentence_ids')} for e in p['evidence_bundle']['items'][:3]],
        'votes':[{k:v.get(k) for k in ('voter_id','response_status','source_support','schema_status','schema_issue','requested_context','suggested_revision','error')} for v in r['selected_votes']]})
invalid=[]
for r in c.execute("SELECT result_json,metadata_json FROM request_attempts WHERE state='failed' AND json_extract(result_json,'$.response_status')='invalid_json'"):
    vote=json.loads(r[0]);meta=json.loads(r[1]);p=meta.get('proposal',{})
    if (p.get('candidate') or {}).get('candidate_id') not in fresh_ids: continue
    invalid.append({'model_id':vote.get('model_id'),'input_tokens':vote.get('input_tokens'),'output_tokens':vote.get('output_tokens'),
        'generation':meta.get('generation'),'raw_length':len(str(vote.get('raw_response',''))),'error':vote.get('error')})
drafts=[]
for r in c.execute('SELECT status,draft_json,result_json FROM revision_drafts'):
    draft=json.loads(r[1]);result=json.loads(r[2])
    drafts.append({'status':r[0],'candidate_id':draft['candidate_id'],'draft_id':draft['draft_id'],
        'changed_fields':draft['changed_fields'],'changes':draft['changes'],'approved_voter_ids':result.get('approved_voter_ids'),
        'field_reviews':[{k:v.get(k) for k in ('voter_id','response_status','decision','field_reviews')} for v in result.get('votes',[])]})
result={'ontology_failure_examples':examples,'candidate_examples':case_examples,'invalid_json_attempts':invalid,'revision_drafts':drafts}
(OUT/'examples.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
planner_errors=Counter()
for audit in onto:
    if audit['result'].get('stop_reason')!='ontology_planner_failed': continue
    rows=c.execute("SELECT record_json FROM ontology_steps WHERE audit_id=? AND role='planner' ORDER BY step_no DESC LIMIT 1",(audit['audit_id'],)).fetchall()
    for row in rows:
        record=json.loads(row[0])
        for attempt in record.get('attempts',[]):
            planner_errors[str(attempt.get('error') or attempt.get('errors') or attempt.get('response_status'))[:240]]+=1
compact={'ontology_planner_failure_attempt_errors':dict(planner_errors),
    'failure_examples':[{'audit_id':e['audit_id'],'stop_reason':e['stop_reason'],
        'planner_errors':[{k:a.get(k) for k in ('error','error_code','response_status','status','validation_errors')} for step in e['last_steps'] if step['role']=='planner' for a in step['record'].get('attempts',[])]} for e in examples],
    'invalid_json_attempt_count':len(invalid),'invalid_json_all_hit_4096_output_cap':all(v['output_tokens']==4096 and v['generation'].get('max_output_tokens')==4096 for v in invalid),
    'invalid_json_empty_raw_count':sum(v['raw_length']==0 for v in invalid),
    'candidate_examples':case_examples,
    'drafts':[{k:d[k] for k in ('candidate_id','draft_id','status','changed_fields','approved_voter_ids')} for d in drafts]}
(OUT/'example_findings.json').write_text(json.dumps(compact,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in compact.items() if k!='candidate_examples'},ensure_ascii=False,indent=2))
c.close()
