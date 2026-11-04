from pathlib import Path
import json
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
c=sqlite3.connect((ROOT/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=15)
votes=[json.loads(r[0]) for r in c.execute('SELECT vote_json FROM ontology_votes WHERE audit_id=?',('ontology_audit_51aa65a3e4af4a4b9cea53818e723d0a',))]
rows=[]
for vote in votes:
    rows.append({k:vote.get(k) for k in ('voter_id','response_status','decision','error','error_code','actual_calls','input_tokens','output_tokens')})
    if vote['voter_id']=='judge_b':
        rows[-1]['attempt_errors']=[{k:a.get(k) for k in ('error','response_status','http_status','finish_reason')} for a in vote.get('attempts',[])]
print(json.dumps(rows,ensure_ascii=False,indent=2));c.close()
