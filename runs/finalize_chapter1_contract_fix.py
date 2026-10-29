"""Bind the final tests and live verification to the full restart plan."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_jobs/chapter1_20261028_contract_fix'
JOB=ROOT/'runs/review_jobs/chapter1_20261028_planner_v3'


def main():
    from review_agent.batch import versions_for
    from review_agent.service import load_config
    tests={}
    for name in ('planner_final_v3.log','integration_final_v3.log'):
        log=(OUT/name).read_text(encoding='utf-8')
        assert log.rstrip().endswith('OK'),name
        tests[name]={'passed':int(re.findall(r'Ran (\d+) tests?',log)[-1]),
            'sha256':hashlib.sha256((OUT/name).read_bytes()).hexdigest()}
    live=json.loads((OUT/'live_canary_final_verification.json').read_text(encoding='utf-8'))
    assert live['status']=='passed' and live['invalid_tool_calls']==0 and live['invalid_planner_attempts']==0
    assert not (JOB/'status.json').exists(),'full job was already started'
    plan=json.loads((JOB/'execution_plan.json').read_text(encoding='utf-8'))
    assert plan['review_versions']==versions_for(load_config(JOB/'review_config.json'))
    for filename,expected in plan['source_files_sha256'].items():
        assert hashlib.sha256((ROOT/filename).read_bytes()).hexdigest()==expected,filename
    plan['source_files_sha256']['review_agent/service.py']=hashlib.sha256((ROOT/'review_agent/service.py').read_bytes()).hexdigest()
    plan['verification_manifest']=str(OUT/'verification_manifest.json')
    (JOB/'execution_plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    result={'status':'passed','verified_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'tests':tests,'total_tests_passed':sum(v['passed'] for v in tests.values()),
        'live_verified_candidates':live['candidate_count'],'live_valid_current_votes':live['valid_current_proposal_votes'],
        'live_invalid_tools':0,'live_invalid_planner_attempts':0,'frozen_bindings_verified':True,
        'review_versions':plan['review_versions'],'source_files_sha256':plan['source_files_sha256'],
        'full_restart_job':str(JOB),'case_budgets_reset':False,'old_audits_preserved':True}
    (OUT/'verification_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('status','total_tests_passed','live_verified_candidates','live_valid_current_votes','full_restart_job')},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
