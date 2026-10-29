"""Run all tests, retaining pre-existing bytes of generated tracked outputs."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
paths=['runs/_test_model_configs/models_ontology.json','runs/_test_model_configs/models_save.json',
       'runs/_test_model_configs/models_triplet.json','runs/_test_pipeline/current.json']
original={name:(root/name).read_bytes() for name in paths}
report={'generated_input_sha256':{p:hashlib.sha256(data).hexdigest() for p,data in original.items()}}
log=root/'runs/phase_f_regression.log'
try:
    with log.open('w',encoding='utf-8') as output:
        result=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p','test_*.py','-b'],cwd=root,
            env={**os.environ,'PYTHONPATH':os.pathsep.join([str(root/'.runtime-packages'),str(root),str(root/'tests')]),'PYTHONUTF8':'1'},
            stdout=output,stderr=subprocess.STDOUT)
    report['success']=result.returncode==0
finally:
    for name,data in original.items(): (root/name).write_bytes(data)
    report['generated_outputs_restored']=True
    text=log.read_text(encoding='utf-8')
    match=re.search(r'Ran (\d+) tests? in ([\d.]+)s',text)
    if match: report.update(tests=int(match[1]),duration_seconds=float(match[2]))
    skipped=re.search(r'skipped=(\d+)',text)
    report['skipped_real_neo4j_tests']=int(skipped[1]) if skipped else 0
    (root/'runs/phase_f_regression.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
if not report.get('success'): raise SystemExit(1)
