"""Read-only live status of the resumed review and ontology queue."""
from pathlib import Path
import json
import time
from review_agent.repository import ReviewRepository
from review_agent.progress import write_status_snapshot

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_resume_20261029'
repository=ReviewRepository(ROOT/'runs/v3/review.sqlite3')
status=json.loads((OUT/'status.json').read_text(encoding='utf-8'))
with repository.connect() as c:
    jobs=[dict(r) for r in c.execute('SELECT job_type,status,COUNT(*) count FROM jobs GROUP BY job_type,status')]
    audits=[dict(r) for r in c.execute('SELECT status,COUNT(*) count FROM ontology_audits GROUP BY status')]
    attempts=[dict(r) for r in c.execute('SELECT state,COUNT(*) count FROM request_attempts WHERE started_at>=? GROUP BY state',(status['started_at_epoch'],))]
    pauses=[dict(r) for r in c.execute("SELECT service_key,status FROM service_pauses WHERE status='paused'")]
result={'checked_at_epoch':time.time(),'resumed_review':status,'jobs':jobs,'ontology_audits':audits,
        'requests_since_resumed_batch':attempts,'active_service_pauses':pauses}
write_status_snapshot(OUT/'live_status.json',result)
print(json.dumps(result,ensure_ascii=False,indent=2))
