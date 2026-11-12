"""Complete the untouched independent endpoint; never resend gateway-unknown seats."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from evaluation.review_followup import read,write,digest
from review_agent.repository import ReviewRepository
from review_agent.cli import _configured_validators
from review_agent.service import load_config
from ontology_review.validators import judge_independently,consensus
from runs.formal_definitions_20261111 import code_hashes,now


def main():
    out=Path(__file__).resolve().parent;m=read(out/'prepared_manifest.json');p=read(out/'prepared_proposal.json')
    assert m['code_fingerprints']==code_hashes() and digest(out/'prepared_proposal.json')==m['proposal_sha256']
    marker=out/'deepseek_started.json'
    if marker.exists():raise ValueError('Do not resend this endpoint')
    repo=ReviewRepository(out/'review.sqlite3')
    with repo.connect() as c:
        rows=[(json.loads(r[0]),json.loads(r[1] or '{}')) for r in c.execute('SELECT metadata_json,result_json FROM request_attempts')]
    current=[(meta,r) for meta,r in rows if (meta.get('proposal') or {}).get('task_id')==m['extension_id']]
    assert len(current)==2 and all(meta['binding']['voter_id']!='judge_c' for meta,r in current)
    voter=next(v for v in _configured_validators(load_config()) if v['id']=='judge_c')
    write(marker,{'at':now(),'overall_call_count_before':2,'max_new_calls':1,'unknown_gateway_seats_untouched':True})
    write(out/'progress.json',{'active':True,'phase':'untouched_deepseek','at':now()})
    vote=judge_independently([{**voter,'ontology_delivery_version':'ontology_validator_payload_v3'}],p,repo,retries=0,
        timeout=float(voter.get('request_timeout_seconds',90)))[0]
    repo.ontology_vote(vote);write(out/'deepseek_vote.json',vote)
    audit=repo.ontology_audit(m['audit_id']);votes=[v for v in audit['votes'] if v['round_id']==p['round_id']]
    final=consensus(votes,p);task=repo.extension_status(m['extension_id'])
    repo.finish_ontology_audit(m['audit_id'],{'extension_id':m['extension_id'],'recommendation':final['result'],
        'task_status':'needs_human','selected_round_id':p['round_id'],'selected_proposal_digest':p['proposal_digest'],
        'consensus':final,'stop_reason':'All three original seats attempted once; gateway-unknown and invalid receipts held.'},task['row_version'])
    with repo.connect() as c:records=c.execute('SELECT * FROM request_attempts').fetchall()
    ledger=[]
    for r in records:
        meta=json.loads(r['metadata_json']);result=json.loads(r['result_json'] or '{}')
        if (meta.get('proposal') or {}).get('task_id')!=m['extension_id']:continue
        ledger.append({'attempt_id':r['attempt_id'],'voter_id':meta['binding']['voter_id'],'state':r['state'],
            'request_key':r['request_key'],'started_at':r['started_at'],'response_received_at':r['response_received_at'],
            'response_status':result.get('response_status'),'error_code':result.get('error_code'),'error':result.get('error'),
            'input_tokens':result.get('input_tokens'),'output_tokens':result.get('output_tokens'),
            'latency_ms':result.get('latency_ms'),'provider_request_id':r['provider_request_id'],'actual_receipt':result})
    assert len(ledger)==3
    if (out/'final_summary.json').exists():write(out/'continuation_summary_before_deepseek.json',read(out/'final_summary.json'))
    protected=read(out/'protected_inputs.json');assert all(digest(Path(k))==v for k,v in protected.items())
    summary={'at':now(),'definitions_prepared':8,'local_guard_checks_passed':8,'modeling_receipts':3,
        'modeling_consensus':final,'semantic_competency_executed':False,
        'semantic_not_executed_reason':'No complete valid modeling quorum; mandatory original chapter question was not bypassed.',
        'new_model_calls':3,'authorized_limit':6,'unknown_requests':[r['attempt_id'] for r in ledger if r['state'] in {'unknown','running'}],
        'production_written':False,'source_and_production_unchanged':True,'isolated_release_published':False,
        'votes':[{'voter_id':v['voter_id'],'response_status':v['response_status'],'decision':v.get('decision'),
            'error':v.get('error'),'error_code':v.get('error_code')} for v in votes]}
    write(out/'new_request_ledger.json',ledger);write(out/'final_summary.json',summary)
    write(out/'progress.json',{'active':False,**summary});print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
