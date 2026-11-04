"""Bounded same-model availability probes; no chapter text is sent."""
from pathlib import Path
import json
import time
from review_agent.cli import _configured_validators
from review_agent.execution import api_error_fields
from review_agent.repository import ReviewRepository
from review_agent.request_execution import service_key
from review_agent.service import load_config

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/independent_completion_20261030'


if __name__=='__main__':
    repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3')
    results=[]
    for voter in _configured_validators(load_config()):
        started=time.time()
        try:
            response=voter['client'].complete_json({'task_type':'service_availability_probe','instruction':'Return {"ok":true}.'},
                system_prompt='Service availability check.',max_tokens=64,timeout_seconds=20)
            if not response.response_id or str(response.returned_model_id or '').casefold()!=voter['model_id'].casefold():
                raise ValueError('exact configured model identity required')
            row={'service_key':service_key(voter),'model_id':voter['model_id'],'response_status':'ok',
                'response_id':response.response_id,'input_tokens':response.input_tokens,'output_tokens':response.output_tokens}
            repo.resume_model_service(service_key(voter),row)
        except Exception as exc:
            row={'model_id':voter['model_id'],'response_status':'probe_failed','error_type':type(exc).__name__,
                 'error':str(exc)[:500],**api_error_fields(exc)}
        row['elapsed_seconds']=round(time.time()-started,2)
        results.append(row)
        (OUT/'service_probe.json').write_text(json.dumps({'items':results,'all_healthy':len(results)==3 and all(item['response_status']=='ok' for item in results),
            'short_probe_does_not_prove_long_context_health':True},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(row,ensure_ascii=False),flush=True)
