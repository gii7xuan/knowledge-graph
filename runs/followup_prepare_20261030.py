"""Read-only plan and migration rehearsal for explicitly authorized follow-up."""
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import json
import shutil
import sqlite3
from migrate_review_recovery_20261029 import fingerprints
from review_agent.repository import ReviewRepository,MIGRATION_VERSION

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_followup_20261030'
OUT.mkdir(exist_ok=True)
source_path=ROOT/'runs/v3/review.sqlite3'
stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
saved=OUT/('before_v8_'+stamp+'.sqlite3')
source=sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True,timeout=15)
target=sqlite3.connect(saved)
try: source.backup(target)
finally: target.close();source.close()
dry=OUT/('migration_dry_'+stamp+'.sqlite3')
shutil.copy2(saved,dry)
repo=ReviewRepository(dry)
before,projection=fingerprints(repo)
repo.initialize()
after,_=fingerprints(repo,projection)
if before!=after: raise RuntimeError('migration changed existing rows')
once,all_projection=fingerprints(repo);repo.initialize();twice,_=fingerprints(repo,all_projection)
if once!=twice: raise RuntimeError('migration not idempotent')
c=sqlite3.connect(saved.as_uri()+'?immutable=1',uri=True);c.row_factory=sqlite3.Row
tasks=[]
for row in c.execute("SELECT * FROM ontology_extension_tasks WHERE status IN ('proposed','needs_human')"):
    task=json.loads(row['task_json'])
    if any(e.get('action')=='reject' for e in task.get('human_events',[])): continue
    if c.execute('SELECT COUNT(*) FROM ontology_proposals p JOIN ontology_audits a ON a.audit_id=p.audit_id WHERE a.extension_id=?',(row['extension_id'],)).fetchone()[0]: continue
    subs=c.execute("SELECT s.* FROM ontology_extension_subscriptions s JOIN candidates c ON s.candidate_id=c.candidate_id WHERE s.extension_id=? AND s.status='active' AND s.revision=c.current_revision AND s.expected_row_version=c.row_version AND c.human_status='pending'",(row['extension_id'],)).fetchall()
    if not subs: continue
    previous=c.execute('SELECT result_json FROM ontology_audits WHERE extension_id=? ORDER BY rowid DESC LIMIT 1',(row['extension_id'],)).fetchone()
    stop=json.loads(previous[0] or '{}').get('stop_reason') if previous else None
    tasks.append({'extension_id':row['extension_id'],'row_version':row['row_version'],'status':row['status'],
        'change_kind':task['change_kind'],'description':task['description'],'subscriber_ids':[s['candidate_id'] for s in subs],
        'material_count':len(task.get('review_evidence',[])),
        'last_stop_reason':stop})
tasks.sort(key=lambda t:(len(t['subscriber_ids'])>1,t['material_count'],t['extension_id']))
pilot=[]
for kind in ('add_class','add_object_property','add_data_property','add_alias'):
    candidate=next((t for t in tasks if t['change_kind']==kind),None)
    if candidate: pilot.append(candidate)
    if len(pilot)==3: break
for task in tasks:
    if len(pilot)==3: break
    if task not in pilot: pilot.append(task)
result={'backup':str(saved),'dry_database':str(dry),'migration_version':MIGRATION_VERSION,
    'existing_rows_preserved':before==after,'idempotent':once==twice,'eligible_tasks':len(tasks),
    'task_kinds':dict(Counter(t['change_kind'] for t in tasks)),'pilot':pilot,'remaining_tasks':[t for t in tasks if t not in pilot],
    'model_calls':0,'production_database_modified':False,'config_sha256':__import__('hashlib').sha256((ROOT/'config/review_agent.json').read_bytes()).hexdigest()}
(OUT/'continuation_plan.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='remaining_tasks'},ensure_ascii=False,indent=2))
c.close()
