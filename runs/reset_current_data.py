"""One-off, recoverable reset of explicitly inventoried active project data."""
import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
DATABASE=ROOT/'runs/v3/review.sqlite3'


def safe(path):
    path=Path(path).resolve(strict=True)
    if path==ROOT or not path.is_relative_to(ROOT) or path.is_symlink(): raise ValueError('unsafe reset path: '+str(path))
    return path


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def counts(path):
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as connection:
        tables=[r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t:connection.execute('SELECT count(*) FROM "'+t.replace('"','""')+'"').fetchone()[0] for t in tables}


def prepare():
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup=ROOT/'backups/data_reset'/stamp;backup.mkdir(parents=True,exist_ok=False)
    with closing(sqlite3.connect(DATABASE.resolve().as_uri()+'?mode=ro',uri=True)) as source:
        active=source.execute("SELECT count(*) FROM jobs WHERE status IN ('running','queued')").fetchone()[0]
        if active: raise ValueError('active audit jobs must finish before reset')
        run_ids=[r[0] for r in source.execute('SELECT run_id FROM runs')]
        destination=backup/'review.sqlite3'
        with closing(sqlite3.connect(destination)) as target: source.backup(target)
    before=counts(DATABASE)
    if counts(destination)!=before: raise ValueError('database backup row counts differ')
    with closing(sqlite3.connect(destination)) as connection:
        if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('backup integrity failed')
    targets=[ROOT/p for p in ('runs/ontology_releases','runs/ontology_imports','runs/ontology','runs/v2')]
    for run_id in run_ids:
        if Path(run_id).name!=run_id: raise ValueError('unsafe run ID')
        targets.append(ROOT/'runs/v3'/run_id)
    targets.extend(ROOT/'ontology'/name for name in ('ontology.json','ontology_modules.json','topic_schema_mapping.json'))
    for directory,pattern in (('triplets','*.json'),('rejected_triplets','*.json'),('review_logs','*.json'),('evidence','*_evidence.md'),('ontologyBuild','ontology_*.json')):
        targets.extend((ROOT/directory).glob(pattern))
    targets=sorted(set(safe(p) for p in targets if p.exists()))
    entries=[]
    for path in targets:
        relative=path.relative_to(ROOT);out=backup/'files'/relative
        if path.is_dir():
            for child in path.rglob('*'):
                safe(child)
                if child.is_symlink(): raise ValueError('backup tree contains a link')
            shutil.copytree(path,out)
            files=[p for p in path.rglob('*') if p.is_file()]
        else:
            out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,out);files=[path]
        for original in files:
            copied=backup/'files'/original.relative_to(ROOT)
            sha=digest(original)
            if digest(copied)!=sha: raise ValueError('backup content mismatch: '+str(original))
            entries.append({'path':original.relative_to(ROOT).as_posix(),'sha256':sha,'bytes':original.stat().st_size})
    if counts(DATABASE)!=before: raise ValueError('database changed while backup was prepared')
    manifest={'status':'backup_verified','root':str(ROOT),'backup':str(backup),'database':str(DATABASE),
        'database_backup_sha256':digest(destination),'before_counts':before,'targets':[str(p) for p in targets],
        'files':entries,'neo4j_modified':False,'source_markdown_deleted':False}
    path=backup/'reset_manifest.json';path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(path))


def verify(manifest_path):
    manifest_path=safe(manifest_path)
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest['status']!='backup_verified' or manifest['root']!=str(ROOT): raise ValueError('reset backup is not ready')
    backup=safe(manifest['backup'])
    if digest(backup/'review.sqlite3')!=manifest['database_backup_sha256']: raise ValueError('database backup changed')
    for entry in manifest['files']:
        if digest(backup/'files'/entry['path'])!=entry['sha256']: raise ValueError('backup verification failed')
    # Data directories have been removed by the native PowerShell operation.
    from review_agent.repository import ReviewRepository,MIGRATION_VERSION
    repository=ReviewRepository(DATABASE);repository.initialize()
    for name in ('triplets','rejected_triplets','review_logs','runs/ontology_releases','runs/ontology_imports','runs/ontology','runs/v2'):
        (ROOT/name).mkdir(parents=True,exist_ok=True)
    after=counts(DATABASE)
    unexpected={t:n for t,n in after.items() if t!='schema_migrations' and n}
    if unexpected: raise ValueError('reset database is not empty: '+str(unexpected))
    with repository.connect() as connection:
        integrity=connection.execute('PRAGMA integrity_check').fetchone()[0]
        foreign=list(connection.execute('PRAGMA foreign_key_check'))
    from ontologyBuild.release_catalog import ReleaseCatalog
    if ReleaseCatalog(ROOT/'runs/ontology_releases').current() is not None: raise ValueError('current ontology still exists')
    if integrity!='ok' or foreign: raise ValueError('reset database integrity failed')
    manifest.update(status='completed',after_counts=after,migration_version=MIGRATION_VERSION,
        integrity_check=integrity,foreign_key_errors=len(foreign),current_ontology_release=None,
        raw_ontology_exists=(ROOT/'ontology/ontology.json').exists())
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:manifest[k] for k in ('status','backup','before_counts','after_counts','current_ontology_release','raw_ontology_exists','integrity_check','neo4j_modified')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','verify']);parser.add_argument('--manifest');args=parser.parse_args()
    if args.action=='prepare': prepare()
    else: verify(args.manifest)
