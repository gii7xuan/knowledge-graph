"""Isolated regression for local draft repair and executable schema checks."""
from pathlib import Path
import hashlib
import json
import time
import unittest
from validate_review_recovery_20261029 import Result

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/ontology_draft_repairs_20261030'
OUT.mkdir(exist_ok=True)


if __name__=='__main__':
    sources=[path for folder in ('review_agent','ontology_review','ontologyBuild','schema','prompts')
             for path in (ROOT/folder).glob('*') if path.suffix in {'.py','.txt'}]
    hashes=lambda:{str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    before=hashes();started=time.time()
    suite=unittest.defaultTestLoader.loadTestsFromNames([
        'tests.test_ontology_draft_repair','tests.test_ontology_extension_agent',
        'tests.test_owl_extension_publication','tests.test_review_followup_v5',
        'tests.test_review_enhancement_v4','tests.test_review_agent_v3',
        'tests.test_ontology_import','tests.test_admission_graph_sync'])
    with (OUT/'regression.log').open('w',encoding='utf-8',buffering=1) as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2,resultclass=Result).run(suite)
    after=hashes()
    summary={'tests_run':result.testsRun,'passed':sum(item['status']=='passed' for item in result.entries),
        'failed':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
        'elapsed_seconds':round(time.time()-started,2),'source_changed_during_run':before!=after,
        'successful':result.wasSuccessful() and before==after,'source_hashes':after,
        'external_model_calls':0,'production_database_modified':False,'tests':result.entries}
    (OUT/'validation.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in summary.items() if key not in {'tests','source_hashes'}},ensure_ascii=False))
    raise SystemExit(not summary['successful'])
