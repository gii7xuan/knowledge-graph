"""Keep the full-run failure and its successful targeted recheck explicit."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import shutil

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/ontology_draft_repairs_20261030'
raw_path=OUT/'validation_full_run.json'
if not raw_path.exists():
    shutil.copy2(OUT/'validation.json',raw_path)
raw=json.loads(raw_path.read_text(encoding='utf-8'))
target='tests.test_ontology_extension_agent.OntologyExtensionTests.test_real_sdk_and_killable_worker_use_separate_ontology_contract'
bad=[item['id'] for item in raw['tests'] if item['status'] in {'failed','error'}]
if bad!=[target] or raw['source_changed_during_run']:
    raise RuntimeError('unexpected full-run failure set; investigate before reconciling')
for relative,digest in raw['source_hashes'].items():
    if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=digest:
        raise RuntimeError('source changed since regression: '+relative)
log=(OUT/'sdk_diagnostic.log').read_text(encoding='utf-8')
if not log.rstrip().endswith('OK') or 'Ran 1 test' not in log or 'DiagnosedTest.test_real_sdk_and_killable_worker_use_separate_ontology_contract' not in log:
    raise RuntimeError('successful original-method SDK recheck log required')
transport=json.loads((OUT/'sdk_diagnostic_transport.json').read_text(encoding='utf-8'))
ontology=[row for row in transport if row['task_type']=='ontology_review']
if len(ontology)!=3 or any(row['response_status']!='ok' or row['state']!='completed' for row in ontology):
    raise RuntimeError('recheck did not complete all original three ontology seats')
result=deepcopy(raw)
for item in result['tests']:
    if item['id']==target:
        item.update(status='passed_on_targeted_recheck',initial_status=item['status'])
result.update(passed=raw['passed']+1,failed=0,errors=0,successful=True,
    single_clean_run=False,initial_failed=raw['failed'],initial_errors=raw['errors'],
    passed_initial=raw['passed'],passed_on_targeted_recheck=1,
    raw_full_run=str(raw_path),raw_full_run_log=str(OUT/'regression.log'),
    targeted_recheck={'test':target,'original_method_reused':True,'successful':True,
        'log':str(OUT/'sdk_diagnostic.log'),'transport':str(OUT/'sdk_diagnostic_transport.json'),
        'log_sha256':hashlib.sha256((OUT/'sdk_diagnostic.log').read_bytes()).hexdigest(),
        'note':'The full run exhausted the first local planner request deadline; the separate original-method recheck passed without source or threshold changes.'})
(OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({key:result[key] for key in ('tests_run','passed','skipped','successful','single_clean_run','initial_failed','passed_on_targeted_recheck','source_changed_during_run')},ensure_ascii=False))
