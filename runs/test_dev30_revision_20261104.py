"""Run all test modules in three independent processes; models remain mocked."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/dev30_code_revision_20261104'
modules=['tests.'+p.stem for p in sorted((ROOT/'tests').glob('test*.py'))]
groups=[modules[i::3] for i in range(3)]
start=time.monotonic()
def run(pair):
    i,group=pair;path=OUT/f'final_regression_{i+1}.txt'
    with path.open('w',encoding='utf-8') as log:
        result=subprocess.run([sys.executable,'-m','unittest','-q',*group],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
    text=path.read_text(encoding='utf-8');count=re.findall(r'Ran (\d+) tests?',text)
    skipped=re.findall(r'skipped=(\d+)',text)
    return {'group':i+1,'modules':group,'exit_code':result.returncode,'tests':int(count[-1]) if count else None,
        'skipped':int(skipped[-1]) if skipped else 0,'log':str(path)}
with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(run,enumerate(groups)))
summary={'module_count':len(modules),'groups':results,'tests':sum(r['tests'] or 0 for r in results),
    'skipped':sum(r['skipped'] for r in results),'all_passed':all(r['exit_code']==0 for r in results),
    'seconds':round(time.monotonic()-start,2),'real_model_requests':0}
(OUT/'final_regression_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False),flush=True)
sys.exit(0 if summary['all_passed'] else 1)
