"""Pilot current, unticketed prepared deltas without touching unknown/valid seats."""
from pathlib import Path
import importlib.util,json,time,os,argparse
from review_agent.repository import ReviewRepository
from review_agent.service import load_config
from review_agent.cli import _configured_validators
from review_agent.progress import write_status_snapshot

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/ontology_publication_20261031'
spec=importlib.util.spec_from_file_location('prepared_ontology_operator',ROOT/'runs/complete_prepared_ontology_reviews_20261030.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.OUT=OUT
module.REASON='用户要求处理本体扩展审核及发布；先对无冻结投票的当前草案执行真实工具检查和原三模型独立审核；保留已有有效票、反对票与未知请求，不伪造能力检查或自动准入'

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--lossless-sequential',action='store_true');parser.add_argument('--only');parser.add_argument('--status');args=parser.parse_args()
    repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');config=load_config()
    for slot in config['validators']:
        if slot.get('family','').casefold()=='gemma':slot.update(request_timeout_seconds=300,http_timeout_seconds=300)
    config['ontology_extension']['budget'].update(per_call_timeout_seconds=300,total_timeout_seconds=600)
    voters=_configured_validators(config)
    identities=['ext_da6ddb7f3eeb40db9c3a0b314301e71d','ext_4272d42b83144e8c8da86d3530f76019','ext_f9b004e2df2c4613aa4d060302238889']
    status_name='prepared_review_status.json'
    if args.lossless_sequential:
        from ontology_review import runtime as runtime_module
        independent=runtime_module.judge_independently
        def sequential(voters,proposal,repository,**kwargs):
            results={}
            for voter in sorted(voters,key=lambda v:{'qwen':0,'deepseek':1,'gemma':2}.get(v['model_family'].casefold(),3)):
                vote=independent([voter],proposal,repository,**kwargs)[0];results[voter['id']]=vote
            return [results[v['id']] for v in voters]
        runtime_module.judge_independently=sequential
        voters=[{**v,'ontology_delivery_version':'ontology_validator_payload_v3'} for v in voters]
        identities=identities[1:];status_name='lossless_sequential_review_status.json'
    if args.only:
        if args.only not in identities:raise ValueError('unexpected prepared ontology task')
        identities=[args.only]
    if args.status:status_name=args.status
    state={'status':'running','pid':os.getpid(),'started_at_epoch':time.time(),'selected':len(identities),'results':[],
        'lossless_sequential':args.lossless_sequential,'same_frozen_proposal_for_each_voter':True,'other_model_votes_supplied':False}
    for identity in identities:
        state.update(active=identity,updated_at_epoch=time.time());write_status_snapshot(OUT/status_name,state)
        try:result=module.review(repo,config,voters,identity)
        except Exception as exc:result={'extension_id':identity,'status':'review_error','error':str(exc),'independent_review_completed':False}
        state['results'].append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
        if not result.get('independent_review_completed'):
            state.update(status='pilot_blocked');break
    else:state.update(status='completed')
    state.update(active=None,finished_at_epoch=time.time());write_status_snapshot(OUT/status_name,state)
