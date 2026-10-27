"""Rehearse schema-6 migration and restore against a read-only production copy."""
import json
from pathlib import Path
from uuid import uuid4
from scripts.validate_review_upgrade_v4 import migration_rehearsal
from review_agent.repository import MIGRATION_VERSION


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / 'runs/review_validation' / ('admission_migration_' + uuid4().hex)
    output.mkdir(parents=True)
    report = migration_rehearsal(root/'runs/v3/review.sqlite3',output)
    report['migration_version'] = MIGRATION_VERSION
    report['scope'] = 'Only copies migrated and restored; production opened read only.'
    (output/'validation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(output/'validation_report.json'),'source_unchanged':report['source_unchanged'],
        'existing_rows_preserved':report['existing_rows_preserved'],'integrity_check':report['integrity_check'],
        'rollback_matches_original':report['rollback_matches_original']},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
