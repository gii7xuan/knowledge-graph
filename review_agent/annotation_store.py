"""Append-only human annotation import; never changes candidate/production labels."""
import json
from .annotation_contract import import_annotation, training_record
from .contracts import canonical_json


def save_annotation(repository, raw, candidate, proposal, adjudication=None, ontology=None):
    record = import_annotation(raw,candidate,proposal,adjudication=adjudication,ontology=ontology)
    with repository.transaction(immediate=True) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS human_annotation_imports(annotation_hash TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,revision INTEGER NOT NULL,record_json TEXT NOT NULL)')
        conn.execute('INSERT OR IGNORE INTO human_annotation_imports VALUES(?,?,?,?)',
            (record['annotation_hash'],candidate['candidate_id'],candidate['revision'],canonical_json(record)))
    return record


def export_training(repository, dimension):
    with repository.connect() as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='human_annotation_imports'").fetchone()
        rows = conn.execute('SELECT record_json FROM human_annotation_imports ORDER BY rowid').fetchall() if exists else []
    latest = {}
    for row in rows:
        record=json.loads(row[0]); b=record['binding']
        latest[(b['candidate_id'],b['revision'],b['proposal_digest'],b['evidence_hash'])]=record
    return [r for a in latest.values() if (r:=training_record(a,dimension)) is not None]
