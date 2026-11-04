from pathlib import Path
import json
from review_agent.repository import ReviewRepository
from review_agent.service import load_config
from review_agent.cli import _configured_validators
from review_agent.recovery_service import probe_services

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_followup_20261030'
if __name__=='__main__':
    path=ROOT/'config/review_agent.json'
    config=json.loads(path.read_text(encoding='utf-8-sig'))
    for voter in config['validators']:
        if voter['family']=='gemma': voter['stream_responses']=True
    path.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    repository=ReviewRepository(ROOT/'runs/v3/review.sqlite3')
    quarantined=repository.quarantine_gateway_timeouts('Nginx 504 does not establish the upstream model stopped; retain uncertain cost and prevent blind resend')
    voters=[v for v in _configured_validators(load_config()) if v['model_family']=='gemma']
    result=probe_services(repository,voters)
    result['quarantined_gateway_attempts']=quarantined
    (OUT/'gemma_stream_probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
