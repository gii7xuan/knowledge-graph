"""Read-only production snapshot and queue diagnosis; performs no model calls."""
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3

root=Path(__file__).resolve().parents[1]
out=root/'runs/review_recovery_20261029'
out.mkdir(exist_ok=True)
snapshot=out/('before_migration_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'.sqlite3')
source=sqlite3.connect((root/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=15)
try:
    target=sqlite3.connect(snapshot)
    try: source.backup(target)
    finally: target.close()
finally: source.close()
c=sqlite3.connect(snapshot.as_uri()+'?immutable=1',uri=True)
c.row_factory=sqlite3.Row
tasks=[dict(r) for r in c.execute("SELECT t.extension_id,t.status,(SELECT COUNT(*) FROM jobs j WHERE j.job_type='ontology_extension' AND json_extract(j.payload_json,'$.extension_id')=t.extension_id AND j.status IN ('queued','running')) active_jobs,(SELECT COUNT(*) FROM ontology_audits a WHERE a.extension_id=t.extension_id) audit_count FROM ontology_extension_tasks t WHERE t.status='proposed'")]
jobs=dict(Counter((r['job_type'],r['status']) for r in c.execute('SELECT job_type,status FROM jobs')))
result={'snapshot':str(snapshot),'checked_at_utc':datetime.now(timezone.utc).isoformat(),
        'proposed_extensions':len(tasks),'proposed_with_no_active_job':sum(not t['active_jobs'] for t in tasks),
        'proposed_without_audit':sum(not t['audit_count'] for t in tasks),
        'job_counts':[{'job_type':k[0],'status':k[1],'count':v} for k,v in jobs.items()],
        'extension_tasks':tasks,'production_database_modified':False,'model_calls':0}
c.close()
(out/'queue_diagnosis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='extension_tasks'},ensure_ascii=False,indent=2))
