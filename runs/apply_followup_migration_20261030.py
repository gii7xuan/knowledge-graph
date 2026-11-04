"""Apply only after the offline regression passes; preserve all existing rows."""
from datetime import datetime,timezone
from pathlib import Path
import json
import sqlite3
from migrate_review_recovery_20261029 import fingerprints
from review_agent.repository import ReviewRepository,MIGRATION_VERSION

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_followup_20261030'
if __name__=='__main__':
    validation=json.loads((OUT/'validation.json').read_text(encoding='utf-8'))
    if not validation['successful']: raise RuntimeError('successful regression required')
    database=ROOT/'runs/v3/review.sqlite3'
    saved=OUT/('before_apply_v8_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
    source=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=15)
    target=sqlite3.connect(saved)
    try: source.backup(target)
    finally: target.close();source.close()
    repo=ReviewRepository(database)
    before,projection=fingerprints(repo);repo.initialize();after,_=fingerprints(repo,projection)
    if before!=after: raise RuntimeError('migration changed pre-existing rows')
    with repo.connect() as c: integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
    if integrity!='ok': raise RuntimeError('integrity check failed')
    result={'migration_version':MIGRATION_VERSION,'backup':str(saved),'existing_rows_preserved':True,
        'case_usage_preserved':True,'integrity_check':integrity,'model_calls':0}
    (OUT/'migration_receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
