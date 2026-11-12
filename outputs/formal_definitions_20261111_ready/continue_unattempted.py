"""Reconcile the one saved late response, then call only untouched fixed seats."""
from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from evaluation.review_followup import read,write,digest
from review_agent.repository import ReviewRepository
from review_agent.service import load_config
from review_agent.cli import _configured_validators
from ontology_review.validators import judge_independently,consensus
from ontology_review.publisher import OntologyPublisher
from runs.formal_definitions_20261111 import roster,now,code_hashes


def main():
    out=Path(__file__).resolve().parent;m=read(out/'prepared_manifest.json');p=read(out/'prepared_proposal.json')
    assert m['code_fingerprints']==code_hashes()
    assert digest(out/'prepared_proposal.json')==m['proposal_sha256']
    if (out/'continuation_started.json').exists():raise ValueError('Never relaunch this continuation')
    repo=ReviewRepository(out/'review.sqlite3');voters=_configured_validators(load_config());assert roster(voters)==m['roster']
    def attempts():
        with repo.connect() as c:rows=c.execute('SELECT * FROM request_attempts').fetchall()
        return [{**dict(r),'metadata':json.loads(r['metadata_json']),'result':json.loads(r['result_json'] or '{}')}
            for r in rows if (json.loads(r['metadata_json']).get('proposal') or {}).get('task_id')==m['extension_id']]
    original=attempts();assert len(original)==1 and original[0]['state'] in {'completed','failed'}
    late=original[0]['result'];assert late['voter_id']=='judge_a' and late['response_status']=='invalid_contract'
    for key in ('audit_id','round_id','proposal_digest','task_id','object_revision','delta_hash','evidence_hash'):
        assert late[key]==p[key]
    write(out/'late_response_reconciliation.json',{'at':now(),'attempt_id':original[0]['attempt_id'],
        'original_caller_vote':read(out/'modeling_votes.json')[0],'actual_saved_response':late,
        'new_model_calls':0,'format_or_judgment_modified':False})
    repo.ontology_recovered_vote(late)
    by_id={late['voter_id']:late}
    write(out/'continuation_started.json',{'at':now(),'unattempted_voter_ids':['judge_b','judge_c'],
        'new_call_limit':2,'overall_call_limit':6,'Qwen_resend':False})
    write(out/'progress.json',{'active':True,'phase':'remaining_original_modeling_seats','at':now()})
    for v in voters:
        if v['id'] in by_id:continue
        assert not any(a['metadata']['binding']['voter_id']==v['id'] for a in attempts())
        batch=judge_independently([{**v,'ontology_delivery_version':'ontology_validator_payload_v3'}],p,repo,
            retries=0,timeout=float(v.get('request_timeout_seconds',180)))
        vote=batch[0];by_id[v['id']]=vote;repo.ontology_vote(vote)
        write(out/'continued_modeling_votes.json',list(by_id.values()))
        print(json.dumps({'voter':v['id'],'status':vote['response_status'],'decision':vote.get('decision')},ensure_ascii=False),flush=True)
        if vote.get('outcome_unknown') or vote.get('pause_job'):break
    final=consensus(list(by_id.values()),p)
    task=repo.extension_status(m['extension_id'])
    result={'extension_id':m['extension_id'],'recommendation':final['result'],'task_status':'needs_human',
        'selected_round_id':p['round_id'],'selected_proposal_digest':p['proposal_digest'],'consensus':final,
        'stop_reason':'All originally untouched seats attempted once; invalid original response not rerolled.'}
    repo.finish_ontology_audit(m['audit_id'],result,task['row_version'])
    rows=attempts();assert len(rows)<=3
    protected=read(out/'protected_inputs.json');assert all(digest(Path(k))==v for k,v in protected.items())
    summary={'at':now(),'definitions_prepared':8,'local_guard_checks_passed':8,
        'modeling_receipts':len(by_id),'modeling_consensus':final,'semantic_competency_executed':False,
        'semantic_not_executed_reason':'Complete valid modeling quorum was not reached; original chapter question remains mandatory.',
        'new_model_calls':len(rows),'authorized_limit':6,'remaining_authorization_is_not_a_reason_to_reroll':True,
        'unknown_requests':[r['attempt_id'] for r in rows if r['state'] in {'unknown','running'}],
        'production_written':False,'source_and_production_unchanged':True,'isolated_release_published':False,
        'votes':[{'voter_id':v['voter_id'],'response_status':v['response_status'],'decision':v.get('decision'),
            'error':v.get('error'),'error_code':v.get('error_code')} for v in by_id.values()]}
    write(out/'final_summary.json',summary);write(out/'progress.json',{'active':False,**summary})
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
