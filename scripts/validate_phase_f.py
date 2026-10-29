"""Retain a label-blind controlled experiment demonstration and test evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import unittest
from uuid import uuid4
from scripts.validate_acceptance_t01_t40 import fingerprint_production

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output');args=parser.parse_args()
    output=Path(args.output or ROOT/'runs/phase_f_validation'/uuid4().hex).resolve();output.mkdir(parents=True,exist_ok=False)
    sys.path.insert(0,str(ROOT/'tests'));before=fingerprint_production()
    report={'scope':'phase F code completion, fixtures and Mock models only','real_model_calls':0,'model_quality_measured':False}
    try:
        with (output/'tests.log').open('w',encoding='utf-8') as log:
            result=unittest.TextTestRunner(stream=log,verbosity=2,buffer=True).run(unittest.defaultTestLoader.loadTestsFromName('tests.test_phase_f_completion'))
        report.update(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),success=result.wasSuccessful())
        if result.wasSuccessful():
            from tests.test_phase_f_completion import ExperimentTests
            case=ExperimentTests();case.fixture_output=output/'demo'
            # Do not run the cleanup queue: retain this isolated review fixture.
            case.test_all_methods_fixed_dynamic_and_ablation_use_one_frozen_pool()
            fixture,experiment,executed=case.flow
            report.update(experiment_directory=str(experiment),groups=len(executed['groups']),
                all_groups_same_population=len({g['population_hash'] for g in executed['groups']})==1,
                formal_admissions=sum(g['formal_admission_count'] for g in executed['groups']))
            report['artifact_sha256']={str(p.relative_to(output)):hashlib.sha256(p.read_bytes()).hexdigest() for p in experiment.rglob('*.json')}
    finally:
        report['production_unchanged']=before==fingerprint_production()
        (output/'validation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='artifact_sha256'},ensure_ascii=False,indent=2))
        print(str(output/'validation_report.json'))
    if not report.get('success') or not report['production_unchanged']: raise SystemExit(1)


if __name__=='__main__': main()
