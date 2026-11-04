"""Read-only follow-up execution summary, without exposing source or credentials."""
from pathlib import Path
import json
import sqlite3
import time
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_followup_20261030'
if (OUT/'stream_v3/status.json').exists(): OUT=OUT/'stream_v3'
elif (OUT/'delivery_v2/status.json').exists(): OUT=OUT/'delivery_v2'
state=json.loads((OUT/'status.json').read_text(encoding='utf-8'))
c=sqlite3.connect((ROOT/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=15)
c.row_factory=sqlite3.Row
requests=[]
for r in c.execute('SELECT state,started_at,result_json,metadata_json FROM request_attempts WHERE started_at>=?',(state['started_at_epoch'],)):
    vote=json.loads(r['result_json'] or '{}');meta=json.loads(r['metadata_json'])
    requests.append({'state':r['state'],'task_type':meta.get('task_type'),'model_id':meta.get('binding',{}).get('model_id'),
        'response_status':vote.get('response_status'),'error_code':vote.get('error_code'),'error':str(vote.get('error',''))[:300],
        'output_tokens':vote.get('output_tokens'),'reasoning_tokens':vote.get('reasoning_tokens'),'finish_reason':vote.get('finish_reason'),
        'generation':meta.get('generation'),'age_seconds':round(time.time()-r['started_at'],1)})
jobs=[dict(r) for r in c.execute("SELECT job_type,status,COUNT(*) count FROM jobs WHERE json_extract(payload_json,'$.planning_version')='ontology_extension_planner_v2' GROUP BY job_type,status")]
audits=[dict(r) for r in c.execute("SELECT status,COUNT(*) count FROM ontology_audits WHERE json_extract(context_json,'$.versions.planner_version')='ontology_extension_planner_v2' GROUP BY status")]
result={'checked_at_epoch':time.time(),'controller':state,'jobs':jobs,'ontology_audits':audits,'requests':requests}
(OUT/'live_status.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2));c.close()
