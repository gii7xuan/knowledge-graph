from pathlib import Path
import json,argparse
from review_agent.repository import ReviewRepository
from review_agent.service import load_config
from review_agent.cli import _configured_validators
from ontology_review.recovery import recover_frozen_ontology
from review_agent.contracts import sha256_json

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/ontology_publication_20261031'
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--extension',default='ext_f9b004e2df2c4613aa4d060302238889');parser.add_argument('--sequential',action='store_true');parser.add_argument('--calls',type=int,default=1);parser.add_argument('--status',default='contract_recovery.json');args=parser.parse_args()
    repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');config=load_config();identity=args.extension
    if identity not in {'ext_f9b004e2df2c4613aa4d060302238889','ext_4272d42b83144e8c8da86d3530f76019'}:raise ValueError('unexpected recovery target')
    if args.sequential:
        import ontology_review.recovery as recovery_module
        independent=recovery_module.judge_independently
        def sequential(voters,proposal,repository,**kwargs):
            found={}
            for voter in sorted(voters,key=lambda v:{'qwen':0,'deepseek':1,'gemma':2}.get(v['model_family'].casefold(),3)):
                vote=independent([voter],proposal,repository,**kwargs)[0];found[voter['id']]=vote
            return [found[v['id']] for v in voters]
        recovery_module.judge_independently=sequential
    task=repo.extension_status(identity);audit_id=task['latest_audit_id'];before=repo.ontology_audit(audit_id)
    protected={v['voter_id']:sha256_json(v) for v in before['votes'] if v.get('response_status')=='ok'}
    result=recover_frozen_ontology(repo,ROOT/'runs/ontology_releases',_configured_validators(config),config,audit_id,
        reason='用户要求继续其余可推进工作；仅恢复已确认的原席位契约失败，澄清原枚举/JSON 约束，不请求批准结论，保留有效票与原提案，不重发任何未知请求。',
        extra_calls=args.calls,extra_tokens=100000*args.calls,extra_seconds=180*args.calls)
    after=repo.ontology_audit(audit_id)
    if not all(next(sha256_json(v) for v in after['votes'] if v['voter_id']==key)==digest for key,digest in protected.items()):raise RuntimeError('original valid ontology vote changed')
    receipt={'result':result,'original_valid_votes_unchanged':True,'seats':[{k:v.get(k) for k in ('voter_id','response_status','decision','error_code','outcome_unknown')} for v in after['votes']]}
    (OUT/args.status).write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'recommendation':result.get('recommendation'),'original_valid_votes_unchanged':True,'seats':receipt['seats']},ensure_ascii=False))
