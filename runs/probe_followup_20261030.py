from pathlib import Path
import json
from review_agent.cli import _configured_validators
from review_agent.repository import ReviewRepository
from review_agent.service import load_config
from review_agent.recovery_service import probe_services

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_followup_20261030'
if __name__=='__main__':
    result=probe_services(ReviewRepository(ROOT/'runs/v3/review.sqlite3'),_configured_validators(load_config()))
    (OUT/'service_probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
