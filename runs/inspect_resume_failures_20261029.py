from collections import Counter
from pathlib import Path
import json
from review_agent.repository import ReviewRepository
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_resume_20261029'
start=json.loads((OUT/'status.json').read_text(encoding='utf-8'))['started_at_epoch']
repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3')
with repo.connect() as c:
    votes=[json.loads(r[0]) for r in c.execute("SELECT result_json FROM request_attempts WHERE started_at>=? AND state='failed'",(start,))]
    jobs=[{'job_id':r['job_id'],'progress':json.loads(r['progress_json'])} for r in c.execute("SELECT job_id,progress_json FROM jobs WHERE status='failed'")]
    audits=[json.loads(r[0] or '{}') for r in c.execute("SELECT result_json FROM ontology_audits WHERE status='completed'")]
result={'request_failures':[{'model':v.get('model_id'),'status':v.get('response_status'),'error_code':v.get('error_code'),
    'error':str(v.get('error',''))[:700]} for v in votes],'failed_jobs':jobs,
    'ontology_result_counts':dict(Counter(str(a.get('recommendation')) for a in audits))}
(OUT/'failure_diagnosis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
