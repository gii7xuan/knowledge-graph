"""Backup and commit an already approved revision under its frozen CAS contract."""
from datetime import datetime,timezone
from pathlib import Path
import json
import sqlite3
from review_agent.repository import ReviewRepository
from review_agent.revision_service import RevisionService

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_followup_20261030'
OUT.mkdir(exist_ok=True)
database=ROOT/'runs/v3/review.sqlite3'
backup=OUT/('before_followup_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
source=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=15)
destination=sqlite3.connect(backup)
try: source.backup(destination)
finally: destination.close();source.close()
repo=ReviewRepository(database)
service=RevisionService(repo)
draft=service.draft('draft_3bcb38875ddb42e294442a57213f4bb6')
if draft['status']=='approved':
    result=service.apply(draft['draft_id'],draft['draft_hash'])
elif draft['status']=='applied':
    result={'status':'already_applied','draft_id':draft['draft_id']}
else:
    raise RuntimeError('draft no longer has independent approval; do not bypass')
receipt={'backup':str(backup),'revision':result,'model_calls':0,'ontology_published':False}
(OUT/'revision_submission.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(receipt,ensure_ascii=False,indent=2))
