from pathlib import Path
import json,time,os,argparse
from ontology_review.semantic_competency import review,verified_result
from review_agent.repository import ReviewRepository
from review_agent.service import load_config
from review_agent.cli import _configured_validators
from review_agent.progress import write_status_snapshot

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/ontology_publication_20261031'
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--only');parser.add_argument('--status',default='semantic_competency_status.json');args=parser.parse_args()
    repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');config=load_config()
    for slot in config['validators']:
        if slot.get('family','').casefold()=='gemma':slot.update(request_timeout_seconds=300,http_timeout_seconds=300)
    voters=_configured_validators(config);items=['ext_4272d42b83144e8c8da86d3530f76019','ext_f9b004e2df2c4613aa4d060302238889']
    if args.only:
        if args.only not in items:raise ValueError('unexpected competency task')
        items=[args.only]
    state={'status':'running','pid':os.getpid(),'started_at_epoch':time.time(),'results':[]}
    for identity in items:
        state['active']=identity;write_status_snapshot(OUT/args.status,state)
        task=repo.extension_status(identity);audit=repo.ontology_audit(task['latest_audit_id'])
        parent=next(p for p in audit['proposals'] if p['proposal_digest']==audit['result']['selected_proposal_digest'])
        try:
            result=review(repo,ROOT/'runs/ontology_releases',parent,voters,
                reason='用户要求完成其余可推进的本体审核及发布前置工作；独立核验完整章节与完整 RDF 的自然语言能力问题，不覆盖原本体修订票、不重复未知原请求。')
            proof=verified_result(repo,ROOT/'runs/ontology_releases',parent)
            item={'extension_id':identity,'assessment':result['assessment'],'verified_result':proof,
                'seats':[{k:v.get(k) for k in ('voter_id','response_status','error_code','outcome_unknown')} for v in result['votes']]}
        except Exception as exc:item={'extension_id':identity,'error_type':type(exc).__name__,'error':str(exc)}
        state['results'].append(item);write_status_snapshot(OUT/args.status,state)
        print(json.dumps(item,ensure_ascii=False),flush=True)
        if any(v.get('outcome_unknown') for v in item.get('seats',[])):
            state['status']='provider_reconciliation_required';break
    else:state['status']='completed'
    state.update(active=None,finished_at_epoch=time.time());write_status_snapshot(OUT/args.status,state)
