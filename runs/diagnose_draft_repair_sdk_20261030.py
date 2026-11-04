"""Diagnose the isolated localhost SDK test, preserving only transport metadata."""
from pathlib import Path
import json
import sqlite3
import unittest
from tests.test_ontology_extension_agent import OntologyExtensionTests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/ontology_draft_repairs_20261030'


class DiagnosedTest(OntologyExtensionTests):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.save_transport)

    def save_transport(self):
        records=[]
        with self.repo.connect() as connection:
            for row in connection.execute('SELECT state,result_json,metadata_json FROM request_attempts ORDER BY rowid'):
                result=json.loads(row['result_json'] or '{}')
                meta=json.loads(row['metadata_json'] or '{}')
                records.append({'state':row['state'],'task_type':meta.get('task_type'),
                    'response_status':result.get('response_status'),'error':result.get('error'),
                    'error_code':result.get('error_code'),'outcome_unknown':result.get('outcome_unknown'),
                    'worker_error':result.get('worker_error')})
        (OUT/'sdk_diagnostic_transport.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    suite=unittest.TestSuite([DiagnosedTest('test_real_sdk_and_killable_worker_use_separate_ontology_contract')])
    with (OUT/'sdk_diagnostic.log').open('w',encoding='utf-8',buffering=1) as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    print(json.dumps({'successful':result.wasSuccessful(),'failures':len(result.failures),'errors':len(result.errors)}))
    print((OUT/'sdk_diagnostic_transport.json').read_text(encoding='utf-8'))
    raise SystemExit(not result.wasSuccessful())
