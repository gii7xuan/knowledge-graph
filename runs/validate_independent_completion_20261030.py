"""Focused final checks for feedback recovery and generation-independent fences."""
from pathlib import Path
import hashlib
import json
import unittest
from validate_review_recovery_20261029 import Result

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/independent_completion_20261030'
if __name__=='__main__':
    selected=['tests.test_recovery_contract_feedback',
        'tests.test_review_followup_v5.RequestAndBudgetTests.test_generation_change_cannot_escape_an_unknown_request',
        'tests.test_review_followup_v5.RequestAndBudgetTests.test_gateway_504_is_unknown_and_never_automatically_retried']
    sources=[ROOT/path for path in ('review_agent/validators.py','review_agent/recovery_service.py',
        'review_agent/request_cache.py','review_agent/request_identity.py','review_agent/request_execution.py')]
    before={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    with (OUT/'final_regression.log').open('w',encoding='utf-8',buffering=1) as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2,resultclass=Result).run(unittest.defaultTestLoader.loadTestsFromNames(selected))
    after={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    summary={'tests_run':result.testsRun,'passed':sum(item['status']=='passed' for item in result.entries),
        'failed':len(result.failures),'errors':len(result.errors),'successful':result.wasSuccessful() and before==after,
        'source_changed_during_run':before!=after,'source_hashes':after,'tests':result.entries,
        'external_model_calls':0,'production_database_modified':False}
    (OUT/'final_validation.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in summary.items() if key not in {'source_hashes','tests'}},ensure_ascii=False))
    raise SystemExit(not summary['successful'])
