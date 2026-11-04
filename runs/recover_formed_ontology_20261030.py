"""Recover only the two known failed seats of the already frozen RDF proposal."""
from pathlib import Path
import json
from review_agent.cli import _configured_validators
from review_agent.repository import ReviewRepository
from review_agent.service import load_config
from ontology_review.recovery import recover_frozen_ontology

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_followup_20261030/frozen_recovery'
OUT.mkdir(exist_ok=True)
if __name__=='__main__':
    config=load_config();repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3')
    aid='ontology_audit_51aa65a3e4af4a4b9cea53818e723d0a'
    before=repo.ontology_audit(aid)
    (OUT/'before.json').write_text(json.dumps(before,ensure_ascii=False,indent=2),encoding='utf-8')
    result=recover_frozen_ontology(repo,ROOT/'runs/ontology_releases',_configured_validators(config),config,aid,
        reason='用户要求继续本体扩展；保留原成功票，仅对已知上下文超限/服务失败席位使用图哈希相同的无损交付恢复，原提案和原契约不变',
        extra_calls=4,extra_tokens=400000,extra_seconds=600)
    after=repo.ontology_audit(aid)
    valid_before={v['voter_id']:v for v in before['votes'] if v['response_status']=='ok'}
    valid_after={v['voter_id']:v for v in after['votes'] if v['response_status']=='ok'}
    receipt={'result':result,'valid_original_votes_unchanged':all(valid_after.get(k)==v for k,v in valid_before.items()),
        'current_vote_statuses':[{k:v.get(k) for k in ('voter_id','response_status','decision','error_code')} for v in after['votes']]}
    (OUT/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in receipt.items() if k!='result'},ensure_ascii=False,indent=2))
    print(json.dumps({k:result.get(k) for k in ('recommendation','selected_round_id','task_status','recovery_grant_id')},ensure_ascii=False))
