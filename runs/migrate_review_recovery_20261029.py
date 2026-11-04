"""Additive v7 migration with table fingerprints and no inference/consumer startup."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from review_agent.repository import ReviewRepository,MIGRATION_VERSION
from review_agent.cli import _configured_validators
from review_agent.service import load_config
from review_agent.recovery_service import seed_service_pauses

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_recovery_20261029'


def fingerprints(repository, projection=None):
    result={}
    with repository.connect() as c:
        if projection is None:
            projection={r[0]:[col[1] for col in c.execute('PRAGMA table_info("'+r[0].replace('"','""')+'")')]
                        for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
                        if r[0]!='schema_migrations'}
        for table,columns in projection.items():
            quote=lambda value:'"'+value.replace('"','""')+'"'
            digest=hashlib.sha256();count=0
            for row in c.execute('SELECT '+','.join(quote(k) for k in columns)+' FROM '+quote(table)+' ORDER BY rowid'):
                digest.update(json.dumps(list(row),ensure_ascii=False,separators=(',',':'),default=str).encode('utf-8'))
                digest.update(b'\n');count+=1
            result[table]={'rows':count,'sha256':digest.hexdigest()}
    return result,projection


def backup(source_path):
    path=OUT/('before_apply_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
    source=sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True,timeout=15)
    try:
        destination=sqlite3.connect(path)
        try: source.backup(destination)
        finally: destination.close()
    finally: source.close()
    return path


def main(apply=False):
    OUT.mkdir(exist_ok=True)
    if apply:
        validation=json.loads((OUT/'validation.json').read_text(encoding='utf-8'))
        if not validation['successful']: raise RuntimeError('successful final regression required before production migration')
        extra_validation=json.loads((OUT/'r20_validation.json').read_text(encoding='utf-8'))
        if not extra_validation['successful']: raise RuntimeError('successful R20 supplementary check required')
        database=ROOT/'runs/v3/review.sqlite3'
        saved=backup(database)
    else:
        saved=Path(json.loads((OUT/'queue_diagnosis.json').read_text(encoding='utf-8'))['snapshot'])
        database=OUT/('migration_dry_run_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
        if database.exists(): raise RuntimeError('dry-run output already exists; preserve previous migration evidence')
        shutil.copy2(saved,database)
    repository=ReviewRepository(database)
    before,projection=fingerprints(repository)
    repository.initialize()
    after,_=fingerprints(repository,projection)
    if before!=after: raise RuntimeError('migration changed pre-existing business rows')
    all_once,all_projection=fingerprints(repository)
    repository.initialize()
    all_twice,_=fingerprints(repository,all_projection)
    if all_once!=all_twice: raise RuntimeError('second initialization was not idempotent')
    original_cases=before.get('review_cases')
    seed_service_pauses(repository,_configured_validators(load_config()))
    repaired=repository.repair_extension_jobs()
    with repository.connect() as c:
        pauses=[{'service_key':r[0],'status':r[1]} for r in c.execute('SELECT service_key,status FROM service_pauses')]
        jobs=[dict(r) for r in c.execute('SELECT job_type,status,COUNT(*) count FROM jobs GROUP BY job_type,status')]
        attempt_count=c.execute('SELECT COUNT(*) FROM request_attempts').fetchone()[0]
        family_count=c.execute('SELECT COUNT(*) FROM validation_request_families').fetchone()[0]
        versions=[r[0] for r in c.execute('SELECT migration_version FROM schema_migrations ORDER BY migration_version')]
        integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
    if integrity!='ok': raise RuntimeError('SQLite integrity check failed: '+integrity)
    final,_=fingerprints(repository,projection)
    if final.get('review_cases')!=original_cases: raise RuntimeError('case usage/limits unexpectedly changed during migration')
    result={'mode':'apply' if apply else 'dry_run','database':str(database),'backup':str(saved),
        'migration_version':MIGRATION_VERSION,'schema_versions':versions,'existing_business_rows_preserved':before==after,
        'initialization_idempotent':all_once==all_twice,'case_budget_preserved':True,'repaired_missing_jobs':repaired,
        'service_pauses':pauses,'jobs':jobs,'historic_attempts_imported':attempt_count,
        'semantic_request_bindings':family_count,'integrity_check':integrity,'model_calls':0,
        'consumer_started':False,'before':before,'after_migration':after}
    name='migration_receipt.json' if apply else 'migration_dry_run.json'
    (OUT/name).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'before','after_migration'}},ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true')
    main(parser.parse_args().apply)
