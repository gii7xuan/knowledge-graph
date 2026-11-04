"""Pilot the no-repeat planner and streaming on tasks with no independent tickets."""
from pathlib import Path
import hashlib
import json
import shutil
import sqlite3
import run_followup_20261030 as controller

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'runs/review_followup_20261030'
OUT=BASE/'stream_v3'
OUT.mkdir(exist_ok=True)
if __name__=='__main__':
    original=json.loads((BASE/'continuation_plan.json').read_text(encoding='utf-8'))
    c=sqlite3.connect((ROOT/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=15)
    tasks=[]
    for task in [*original['pilot'],*original['remaining_tasks']]:
        if c.execute('SELECT COUNT(*) FROM ontology_proposals p JOIN ontology_audits a ON a.audit_id=p.audit_id WHERE a.extension_id=?',(task['extension_id'],)).fetchone()[0]==0:
            tasks.append(task)
    identities=('ext_0813e33a152048bc87c8fb055e1b0507','ext_4a9a96a9450145e596202e196fd0c124','ext_47b2102b6ba646a1a4f40587e83c0561')
    pilot=[t for identity in identities for t in tasks if t['extension_id']==identity]
    if len(pilot)!=3: raise RuntimeError('pilot baseline changed; replan without rerolling existing tickets')
    plan={**original,'pilot':pilot,'remaining_tasks':[t for t in tasks if t not in pilot],
        'eligible_tasks':len(tasks),'excluded_tasks_with_frozen_proposals':len(original['pilot'])+len(original['remaining_tasks'])-len(tasks),
        'delivery_version':'ontology_validator_payload_v2','planner_version':'ontology_extension_planner_v3',
        'config_sha256':hashlib.sha256((ROOT/'config/review_agent.json').read_bytes()).hexdigest()}
    (OUT/'continuation_plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    for name in ('validation.json','migration_receipt.json'):
        shutil.copy2(BASE/name,OUT/name)
    controller.OUT=OUT;controller.ONTOLOGY_ONLY=True;controller.GRANT_SCOPE='ontology_stream_v3'
    controller.REASON+='；无损 RDF 交付、完整目录与禁止重复完整检索规划 v3，Gemma 使用流式接收；旧有独立提案及未知票均排除'
    controller.main()
