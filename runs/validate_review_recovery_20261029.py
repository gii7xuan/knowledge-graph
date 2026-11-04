"""Offline release checks with machine-readable results; no production DB."""
import hashlib
import argparse
import json
from pathlib import Path
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_recovery_20261029'
OUT.mkdir(exist_ok=True)
MODULES=['tests.test_review_recovery_v4','tests.test_planner_contract_v3','tests.test_review_planner',
    'tests.test_review_deduplication','tests.test_review_model_settings','tests.test_review_enhancement_v4',
    'tests.test_ontology_extension_agent','tests.test_review_execution','tests.test_admission_graph_sync',
    'tests.test_owl_extension_publication','tests.test_review_agent_v3','tests.test_review_v2',
    'tests.test_review_progress','tests.test_review_module']


class Result(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.entries=[]
    def addSuccess(self,test):
        super().addSuccess(test);self.entries.append({'id':test.id(),'status':'passed'})
    def addSkip(self,test,reason):
        super().addSkip(test,reason);self.entries.append({'id':test.id(),'status':'skipped','reason':reason})
    def addFailure(self,test,error):
        super().addFailure(test,error);self.entries.append({'id':test.id(),'status':'failed'})
    def addError(self,test,error):
        super().addError(test,error);self.entries.append({'id':test.id(),'status':'error'})


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--r20-only',action='store_true')
    args=parser.parse_args()
    started=time.time()
    modules=['tests.test_review_recovery_v4.RevisionAndConsumerTests.test_R20_publication_does_not_rebind_a_revoked_subscription'] if args.r20_only else MODULES
    prefix='r20_' if args.r20_only else ''
    suite=unittest.defaultTestLoader.loadTestsFromNames(modules)
    with (OUT/(prefix+'regression.log')).open('w',encoding='utf-8',buffering=1) as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2,resultclass=Result).run(suite)
    sources=[p for folder in ('review_agent','ontology_review','prompts') for p in (ROOT/folder).glob('*')
             if p.suffix in {'.py','.txt'}]
    summary={'tests_run':result.testsRun,'passed':sum(e['status']=='passed' for e in result.entries),
        'failed':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
        'elapsed_seconds':round(time.time()-started,2),'successful':result.wasSuccessful(),
        'external_model_calls':0,'production_database_modified':False,'tests':result.entries,
        'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    (OUT/(prefix+'validation.json')).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k not in {'tests','source_hashes'}},ensure_ascii=False))
    sys.exit(not result.wasSuccessful())
