"""Run relevant unittest suites with inherited Windows workspace temp permissions.

Some Windows sandbox tokens cannot reopen directories made by Python's mode-0700
mkdtemp. Use unique, workspace-confined directories with inherited permissions;
all test assertions and TemporaryDirectory cleanup stay unchanged.
"""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def workspace_mkdtemp(suffix=None,prefix=None,dir=None):
    parent=Path(dir).resolve() if dir is not None else ROOT/'outputs'/'weighting_test_temp'
    if not parent.is_relative_to(ROOT):raise ValueError('test temporary directory must stay inside workspace')
    parent.mkdir(parents=True,exist_ok=True)
    target=(parent/((prefix or 'tmp')+uuid4().hex+(suffix or ''))).resolve()
    if not target.is_relative_to(parent):raise ValueError('unsafe test temporary path')
    target.mkdir() # inherited Windows ACL; avoids mode-0700 token restriction
    return str(target)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('modules',nargs='*',default=['tests.test_vote_weighting','tests.test_review_four_axes',
        'tests.test_agent_resolution','tests.test_claim_disputes','tests.test_review_observability',
        'tests.test_review_planner','tests.test_review_monitor','tests.test_admission_graph_sync',
        'tests.test_resolution_workflows','tests.test_review_agent_v3'])
    parser.add_argument('--log',default='outputs/vote_weighting_20261116/checks.log')
    args=parser.parse_args()
    target=(ROOT/args.log).resolve()
    if not target.is_relative_to(ROOT):raise ValueError('test log must stay inside workspace')
    target.parent.mkdir(parents=True,exist_ok=True)
    original=tempfile.mkdtemp
    if os.name=='nt':tempfile.mkdtemp=workspace_mkdtemp
    try:
        suite=unittest.defaultTestLoader.loadTestsFromNames(args.modules)
        with target.open('w',encoding='utf-8') as stream:
            result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
        print({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
               'skipped':len(result.skipped),'passed':result.wasSuccessful(),'log':str(target)})
        return 0 if result.wasSuccessful() else 1
    finally:
        tempfile.mkdtemp=original


if __name__=='__main__':raise SystemExit(main())
