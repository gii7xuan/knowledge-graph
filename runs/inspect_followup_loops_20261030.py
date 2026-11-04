from pathlib import Path
import json
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
c=sqlite3.connect((ROOT/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=15);c.row_factory=sqlite3.Row
stage=json.loads((ROOT/'runs/review_followup_20261030/delivery_v2/status.json').read_text(encoding='utf-8'))
out=[]
for item in stage['pilot_results']:
    steps=[]
    for r in c.execute("SELECT step_no,role,record_json FROM ontology_steps WHERE audit_id=? ORDER BY step_no DESC LIMIT 6",(item['audit_id'],)):
        rec=json.loads(r['record_json']);steps.append({'step':r['step_no'],'role':r['role'],'action':rec.get('action'),
            'status':rec.get('status'),'error':rec.get('error'),'query':rec.get('query'),'returned_items':len(rec.get('items',[]))})
    out.append({'extension_id':item['extension_id'],'steps':steps})
first='ontology_audit_51aa65a3e4af4a4b9cea53818e723d0a'
votes=[]
for r in c.execute('SELECT vote_json FROM ontology_votes WHERE audit_id=?',(first,)):
    vote=json.loads(r[0]);entry=c.execute('SELECT status FROM validation_requests WHERE request_key=?',(vote.get('request_key'),)).fetchone()
    votes.append({k:vote.get(k) for k in ('voter_id','response_status','error_code','outcome_unknown','decision') }|{'request_status':entry[0] if entry else None})
result={'recent_loop_examples':out,'first_formed_proposal_votes':votes}
(ROOT/'runs/review_followup_20261030/loop_diagnosis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2));c.close()
