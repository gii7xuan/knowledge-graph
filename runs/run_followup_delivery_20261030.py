"""New delivery pilot uses untouched tasks; old independent tickets stay frozen."""
from pathlib import Path
import json
import shutil
import run_followup_20261030 as controller

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'runs/review_followup_20261030'
OUT=BASE/'delivery_v2'
OUT.mkdir(exist_ok=True)
if __name__=='__main__':
    original=json.loads((BASE/'continuation_plan.json').read_text(encoding='utf-8'))
    # Do not revisit the three already tried objects or their valid/unknown tickets.
    remaining=original['remaining_tasks']
    pilot=[t for t in remaining if t['change_kind']=='add_object_property'][:3]
    plan={**original,'pilot':pilot,'remaining_tasks':[t for t in remaining if t not in pilot],
        'previous_pilot_preserved':[t['extension_id'] for t in original['pilot']],
        'delivery_version':'ontology_validator_payload_v2'}
    (OUT/'continuation_plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    for name in ('validation.json','migration_receipt.json'):
        shutil.copy2(BASE/name,OUT/name)
    controller.OUT=OUT
    controller.ONTOLOGY_ONLY=True
    controller.GRANT_SCOPE='ontology_payload_v2'
    controller.REASON+='；完整 RDF 无损压缩与身份索引可逆去重交付 v2，只调查尚无独立提案的新任务'
    controller.main()
