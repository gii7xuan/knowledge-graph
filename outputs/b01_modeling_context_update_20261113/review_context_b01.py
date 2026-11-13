"""One explicitly authorized three-seat modeling pass for frozen B01 modeling context v3."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
from ontology_review.contracts import proposal_digest, RESPONSE_SCHEMA
from ontology_review.modeling_context import verify as verify_modeling_context
from ontology_review.publisher import current_release_id
from ontology_review.review_payload import validator_payload
from ontology_review.validators import judge, consensus, prompt_for, response_errors
from review_agent.cli import _configured_validators
from review_agent.contracts import sha256_json
from review_agent.repository import ReviewRepository
from review_agent.case_budget import CaseBudget
from review_agent.service import load_config

PRIOR_TOTAL, MAX_CALLS, TOKEN_ENVELOPE = 19, 3, 300000
SCOPE = 'b01_v3_context_three_modeling_calls'


def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def write(name, value): (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
def now(): return datetime.now(timezone.utc).isoformat()


def scoped_attempts(repo, p):
    with repo.connect() as c: rows = c.execute('SELECT attempt_id,state,metadata_json,result_json FROM request_attempts').fetchall()
    result = []
    for row in rows:
        b = json.loads(row['metadata_json']).get('binding', {})
        if all(b.get(k) == p.get(k) for k in ('task_id','audit_id','round_id','proposal_digest')):
            vote = json.loads(row['result_json'] or '{}')
            result.append({'attempt_id':row['attempt_id'],'state':row['state'],'voter_id':b.get('voter_id'),
                'counted_calls':0 if vote.get('actual_calls')==0 else 1,'gateway_request_id':vote.get('gateway_request_id')})
    return result


def preflight():
    m = read(OUT/'prepared_manifest.json'); verification = read(OUT/'delivery_verification.json')
    assert all(digest(ROOT/path)==value for path,value in m['code_fingerprints'].items()), 'Frozen runtime/config changed'
    assert digest(OUT/'prepare.py')==m['preparation_script_sha256']
    assert all(digest(OUT/path)==value for path,value in verification['artifact_sha256'].items()), 'Frozen draft evidence or report changed'
    assert all(digest(Path(path))==value for path,value in read(OUT/'protected_inputs.json').items()), 'Protected original reviews changed'
    p = read(OUT/'prepared_proposal.json')
    assert proposal_digest(p)==p['proposal_digest']==m['proposal_digest']
    assert len(p['operations'])==9 and len(p['evidence'])==18 and p['object_revision']==m['object_revision']==3
    verify_modeling_context(p)
    assert p['versions']['prompt_version']=='ontology_extension_validator_v3_modeling_context_v1'
    assert sum(o['kind']=='revise_constraint' for o in p['operations'])==3
    assert not any(o['target']=='C_88ecbdb51d6e44007aec' for o in p['operations'])
    assert p['check_records']['regression']['status']=='pass' and p['check_records']['regression']['deferred_hierarchy_target_not_claimed_fixed']
    repo_path = Path(m['database']).resolve(); release_root = Path(m['release_root']).resolve()
    assert repo_path.is_relative_to(OUT) and release_root.is_relative_to(OUT)
    assert current_release_id(ROOT/'runs/ontology_releases')==current_release_id(release_root)==p['base_release_id']
    repo = ReviewRepository(repo_path); audit = repo.ontology_audit(p['audit_id'])
    assert next(x for x in audit['proposals'] if x['round_id']==p['round_id'])==p
    assert not any(v['round_id']==p['round_id'] for v in audit['votes']) and not scoped_attempts(repo,p)
    task = repo.extension_status(p['task_id'])
    assert task['delta_revision']==p['object_revision'] and task['delta']['delta_hash']==p['delta_hash']
    voters = _configured_validators(load_config())
    roster = [{k:v[k] for k in ('id','model_id','model_family')} | {'model_identity':str(v.get('model_identity') or v['model_id']).casefold()} for v in voters]
    assert roster==p['committee_members'] and len(voters)==3
    payload = validator_payload(p,delivery_version='ontology_validator_payload_v3')
    payload.update(output_contract=RESPONSE_SCHEMA,allowed_evidence_ids=[e['evidence_id'] for e in p['evidence']])
    with repo.connect() as c:
        cases = [r[0] for r in c.execute('SELECT case_id FROM review_cases WHERE candidate_id IN (SELECT candidate_id FROM ontology_extension_subscriptions WHERE extension_id=? AND status=?)',(p['task_id'],'active'))]
        case_candidates = {r[0]:r[1] for r in c.execute('SELECT case_id,candidate_id FROM review_cases') if r[0] in cases}
    assert cases and len(case_candidates)==len(cases)
    accounts = [repo.case_status(case) for case in cases]
    additions = {a['case_id']:{'tokens':max(0,a['usage']['tokens']+TOKEN_ENVELOPE-a['limits']['tokens']),
                             'llm_calls':max(0,a['usage']['llm_calls']+MAX_CALLS-a['limits']['llm_calls'])} for a in accounts}
    settings = []
    for v in voters:
        client=v['client']; assert client.base_url.rstrip('/') in {'https://43.143.151.41:3000/v1','https://api.deepseek.com'}
        assert int(v.get('max_output_tokens',4096))<=8192
        settings.append({k:v.get(k) for k in ('id','model_id','model_family','max_output_tokens','request_timeout_seconds','http_timeout_seconds')} |
                        {'base_url':client.base_url,'stream':client.stream_responses,'retry_count':0})
    ready = {'client_date':'2026-11-13','observed_at_utc':now(),'status':'ready_for_additional_authorization',
        'proposal_digest':p['proposal_digest'],'audit_id':p['audit_id'],'round_id':p['round_id'],
        'operations':9,'evidence_spans':18,'request_payload_sha256':sha256_json(payload),'prompt_sha256':sha256_json(prompt_for(p)),
        'request_json_utf8_bytes':len(json.dumps(payload,ensure_ascii=False).encode()),'runtime_settings':settings,
        'case_ids':cases,'case_candidates':case_candidates,'case_budget_additions':additions,
        'case_accounts_before':accounts,'token_envelope_for_this_pass':TOKEN_ENVELOPE,
        'previous_cumulative_calls':PRIOR_TOTAL,'max_new_calls':MAX_CALLS,'requested_cumulative_limit':PRIOR_TOTAL+MAX_CALLS,
        'stop_on_unknown':True,'no_automatic_retry':True,'runner_sha256':digest(Path(__file__)),
        'whole_chapter_or_sample_calls_implemented':False,'publication_implemented':False,'model_calls_executed':0}
    return m,p,repo,voters,ready


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    m,p,repo,voters,ready=preflight()
    if not args.execute:
        write('review_preflight_20261113.json',ready);print(json.dumps(ready,ensure_ascii=False),flush=True);return
    path=OUT/'user_authorization_20261113.json'
    if not path.exists():raise ValueError('No explicit additional authorization for this revised proposal')
    grant=read(path)
    assert grant.get('approved') is True and grant.get('source')=='explicit_user_reply_in_chat'
    assert grant.get('scope')==SCOPE and grant.get('proposal_digest')==p['proposal_digest']
    assert grant.get('max_new_calls')==MAX_CALLS and grant.get('previous_cumulative_calls')==PRIOR_TOTAL and grant.get('cumulative_limit')==PRIOR_TOTAL+MAX_CALLS
    assert grant.get('case_token_envelope')==TOKEN_ENVELOPE and grant.get('case_budget_adjustment_authorized') is True
    sealed=read(OUT/'dispatch_manifest_20261113.json')
    assert sealed['runner_sha256']==digest(Path(__file__)) and sealed['prepared_manifest_sha256']==digest(OUT/'prepared_manifest.json')
    assert sealed['preflight_material_hash']==sha256_json({k:v for k,v in ready.items() if k not in {'observed_at_utc','status'}})
    if (OUT/'execution_started_20261113.json').exists():raise ValueError('Never relaunch this pass; reconcile existing requests first')
    write('execution_started_20261113.json',{'observed_at_utc':now(),'authorization_sha256':digest(path),'proposal_digest':p['proposal_digest'],'max_new_calls':3,'prior_cumulative_calls':19})
    receipts=[]
    for case_id,addition in ready['case_budget_additions'].items():
        addition={k:v for k,v in addition.items() if v}
        if addition:
            receipts.append(repo.authorize_case_budget(ready['case_candidates'][case_id],addition,
                reason='Explicitly authorized revised B01 three-seat review, bounded to three calls and a 300000-token pass budget with the new frozen v3 prompt and context.',
                actor='codex_authorized_b01v3_review',authorization_key='b01v3:'+p['proposal_digest']+':'+case_id))
    write('case_budget_authorization_receipts_20261113.json',receipts)
    budget=CaseBudget({'max_llm_calls_per_candidate':3,'max_total_tokens':TOKEN_ENVELOPE,'max_retries_per_request':0,
        'total_timeout_seconds':420},repo,ready['case_ids'],task_type='ontology_extension',task_id=p['audit_id'])
    votes=[];stop='fixed_three_seats_attempted_once'
    for v in sorted(voters,key=lambda x:{'qwen':0,'deepseek':1,'gemma':2}.get(x['model_family'].casefold(),3)):
        assert len(scoped_attempts(repo,p))<MAX_CALLS
        if not budget.reserve_calls(1):stop='case_or_task_budget_exhausted';break
        write('progress_20261113.json',{'active':True,'phase':'B01_v3_context_modeling_only','voter_id':v['id'],'observed_at_utc':now()})
        vote=judge({**v,'ontology_delivery_version':'ontology_validator_payload_v3'},p,repo,retries=0,
            timeout=float(v.get('request_timeout_seconds',180)),deadline_epoch=budget.deadline_epoch)
        votes.append(vote);repo.ontology_vote(vote);write('modeling_votes_20261113.json',votes)
        if not vote.get('actual_calls'):budget.refund_calls(1)
        for attempt in vote.get('attempts') or []:budget.tokens(attempt)
        print(json.dumps({'voter_id':v['id'],'response_status':vote['response_status'],'decision':vote.get('decision'),
            'outcome_unknown':vote.get('outcome_unknown',False),'gateway_request_id':vote.get('gateway_request_id')},ensure_ascii=False),flush=True)
        if vote.get('outcome_unknown') or vote.get('pause_job'):stop='unknown_outcome_or_account_pause_preserved';break
    decision=consensus(votes,p)
    valid_approvals=[v for v in votes if v.get('response_status')=='ok' and v.get('decision')=='approve' and not response_errors({k:v[k] for k in RESPONSE_SCHEMA['required']},p)]
    modeling_pass=(len(valid_approvals)==3 and len({v['model_family'] for v in valid_approvals})==3 and decision['counts']['approve']==3 and len(decision['eligible_voter_ids'])==3)
    task=repo.extension_status(p['task_id'])
    repo.finish_ontology_audit(p['audit_id'],{'extension_id':p['task_id'],'recommendation':decision['result'],'task_status':'needs_human',
        'selected_round_id':p['round_id'],'selected_proposal_digest':p['proposal_digest'],'consensus':decision,
        'stop_reason':stop,'full_chapter_review_executed':False},task['row_version'])
    attempts=scoped_attempts(repo,p);count=sum(a['counted_calls'] for a in attempts)
    assert count<=MAX_CALLS and len(attempts)<=MAX_CALLS
    summary={'observed_at_utc':now(),'scope':SCOPE,'new_model_calls':count,'cumulative_model_calls':PRIOR_TOTAL+count,
        'authorized_cumulative_limit':22,'modeling_approvals':len(valid_approvals),'modeling_three_seats_approve':modeling_pass,
        'consensus_including_all_publication_gates':decision,'stop_reason':stop,'attempts':attempts,'budget':budget.snapshot(),
        'full_chapter_review_executed':False,'sample_model_review_executed':False,'published':False,'production_written':False,
        'old_reviews_and_protected_inputs_unchanged':all(digest(Path(path))==value for path,value in read(OUT/'protected_inputs.json').items())}
    assert summary['old_reviews_and_protected_inputs_unchanged']
    write('modeling_summary_20261113.json',summary);write('progress_20261113.json',{'active':False,**summary})
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
