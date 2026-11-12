"""One bounded recovery for known contract failures; unknown requests are fenced."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from evaluation.review_followup import read,write
from review_agent.repository import ReviewRepository
from review_agent.service import load_config
from review_agent.cli import _configured_validators
from ontology_review.recovery import recover_frozen_ontology
from runs.formal_definitions_20261111 import now


def main():
    out=Path(__file__).resolve().parent;m=read(out/'prepared_manifest.json');repo=ReviewRepository(out/'review.sqlite3')
    if (out/'field_recovery_started.json').exists():raise ValueError('One correction only; do not relaunch')
    audit=repo.ontology_audit(m['audit_id']);votes={v['voter_id']:v for v in audit['votes']}
    assert set(votes)=={'judge_a','judge_b','judge_c'}
    assert votes['judge_a']['response_status']==votes['judge_c']['response_status']=='invalid_contract'
    for seat in ('judge_a','judge_c'):
        entry=repo.validation_request(votes[seat]['request_key']);assert entry['status']=='failed'
        assert votes[seat]['raw_response']['decision']=='approve'
    assert votes['judge_b']['outcome_unknown'] is True
    assert repo.validation_request(votes['judge_b']['request_key'])['status']=='unknown'
    write(out/'field_recovery_started.json',{'at':now(),'selected':['judge_a','judge_c'],'max_new_calls':2,
        'authorized_total_limit':6,'prior_actual_calls':3,'unknown_judge_b_untouched':True,
        'source_proposal_unchanged':True,'preserve_substantive_judgment':True})
    write(out/'progress.json',{'active':True,'phase':'bounded_known_field_correction','at':now()})
    result=recover_frozen_ontology(repo,out/'releases',_configured_validators(load_config()),load_config(),m['audit_id'],
        reason='在用户已授权最多6次调用内，对Qwen两处非法可选evidence_scope及DeepSeek一处证据ID拼写错误各作一次原模型纠正；原始实质判断和来源不变，Gemma的504未知票不重发，不以纠正代替整章能力审核。',
        extra_calls=2,extra_tokens=450000,extra_seconds=420)
    write(out/'field_recovery_result.json',result)
    current=repo.ontology_audit(m['audit_id'])
    write(out/'final_modeling_votes_after_field_recovery.json',current['votes'])
    write(out/'progress.json',{'active':False,'phase':'held_pending_unknown_gateway','at':now(),'recovery':result})
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
