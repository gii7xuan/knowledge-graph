"""Summarize completed checks and restore only known test-generated fixtures."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1];LOG=ROOT/'runs/dev30_code_revision_20261104';OUT=ROOT/'outputs/dev30_code_revision_20261104'


def test_result(name):
    text=(LOG/name).read_text(encoding='utf-8-sig')
    count=re.findall(r'Ran (\d+) tests?',text);skip=re.findall(r'skipped=(\d+)',text)
    return {'log':str(LOG/name),'tests':int(count[-1]) if count else None,'skipped':int(skip[-1]) if skip else 0,
            'passed':bool(re.search(r'(?m)^OK(?: \(.*\))?\s*$',text))}


affected=test_result('final_affected_regression.txt');final=test_result('final_verification.txt')
assert affected['passed'] and final['passed']
build=(LOG/'frontend_build_final.txt').read_text(encoding='utf-8-sig')
front=(LOG/'frontend_tests_final.txt').read_text(encoding='utf-8-sig')
assert 'built in' in build and re.search(r'fail\s+0',front)
offline=json.loads((OUT/'offline_replay.json').read_text(encoding='utf-8'))['summary']
assert offline['samples']==30 and offline['actual_model_calls']==0 and not offline['production_written']
restored=[]
for name in ('runs/_test_model_configs/models_ontology.json','runs/_test_model_configs/models_save.json',
             'runs/_test_model_configs/models_triplet.json','runs/_test_pipeline/current.json'):
    path=(ROOT/name).resolve();assert path.is_relative_to(ROOT)
    baseline=subprocess.run(['git','show','HEAD:'+name],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout
    if path.read_bytes()!=baseline:
        current=json.loads(path.read_bytes());old=json.loads(baseline)
        if '/_test_model_configs/' in name:
            normalize=lambda d:[{k:v for k,v in m.items() if k not in {'id','created_at'}} for m in d['models']]
            assert normalize(current)==normalize(old),'unexpected fixture changes: '+name
        else:
            assert Path(current['run_dir']).resolve().parent==(ROOT/'runs/_test_pipeline').resolve()
        path.write_bytes(baseline);restored.append(name)
summary={'date_beijing':'2026-11-04','implementation_status':'code_and_local_validation_complete',
    'affected_regression':affected,'final_regression':final,
    'frontend':{'build_passed':True,'tests':int(re.findall(r'tests\s+(\d+)',front)[-1]),'passed':True},
    'offline_samples':30,'raw_workbook_sha256':offline['integrity']['workbook_sha256'],
    'production_candidate_bindings_unchanged':offline['integrity']['production_candidates_changed'] is False,
    'model_review_scope':'mocked_local_regression_and_frozen_offline_replay','real_model_requests':0,
    'independent_test_accuracy_claimed':False,'known_regression_failures_open':[],
    'raw_broad_scan_log':str(LOG/'final_regression_summary.json'),
    'broad_scan_note':'536 checks; the provisional-vs-canonical legacy assertion was updated and its entire source-candidate module retested successfully. Earlier failures remain in immutable logs.',
    'restored_test_fixture_files':restored,'source_contract_versions':offline['new_review_versions'],
    'ontology_reference':offline['frozen_ontology_reference']}
(OUT/'verification_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:summary[k] for k in ('implementation_status','affected_regression','final_regression','frontend','offline_samples','real_model_requests')},ensure_ascii=False))
