"""Offline full regression with machine-readable follow-up evidence."""
from pathlib import Path
import hashlib
import json
import time
import unittest
import argparse
from validate_review_recovery_20261029 import MODULES,Result

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_followup_20261030'
OUT.mkdir(exist_ok=True)
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--supplement',action='store_true');args=parser.parse_args()
    prefix='post_reconciliation_' if args.supplement else ''
    started=time.time()
    sources=[p for folder in ('review_agent','ontology_review','prompts') for p in (ROOT/folder).glob('*') if p.suffix in {'.py','.txt'}]
    initial_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    selected=['tests.test_review_followup_v5','tests.test_review_recovery_v4','tests.test_review_enhancement_v4','tests.test_admission_graph_sync'] if args.supplement else ['tests.test_review_followup_v5',*MODULES]
    suite=unittest.defaultTestLoader.loadTestsFromNames(selected)
    with (OUT/(prefix+'regression.log')).open('w',encoding='utf-8',buffering=1) as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2,resultclass=Result).run(suite)
    final_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    summary={'tests_run':result.testsRun,'passed':sum(e['status']=='passed' for e in result.entries),
        'failed':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
        'elapsed_seconds':round(time.time()-started,2),'successful':result.wasSuccessful() and initial_hashes==final_hashes,
        'source_changed_during_run':initial_hashes!=final_hashes,
        'external_model_calls':0,'production_database_modified':False,'tests':result.entries,
        'source_hashes':final_hashes}
    (OUT/(prefix+'validation.json')).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k not in {'tests','source_hashes'}},ensure_ascii=False))
    raise SystemExit(not summary['successful'])
