from pathlib import Path
from copy import deepcopy
import json
from review_agent.repository import ReviewRepository
from review_agent.proposal_resolver import selected_proposal
from ontologyBuild.release_service import load_release
from ontology_review.publisher import current_release_id
from ontology_review.delta import catalog
from review_agent.ontology_guard import OntologyGuard,load_snapshot

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/ontology_publication_20261031'
repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');task=repo.extension_status('ext_4272d42b83144e8c8da86d3530f76019')
identity=task['trigger_candidates'][-1]['candidate_id'];candidate=repo.get_revision(identity);current=repo.get_candidate(identity)
decision=repo.latest_decision(identity);proposal,votes=selected_proposal(repo,candidate,decision)
if len(votes)!=3 or decision.get('source_support_result')!='supported':raise ValueError('full current source support required')
base=load_release(ROOT/'runs/ontology_releases'/current_release_id(ROOT/'runs/ontology_releases'));target='R_1654fcf38913d94cb712'
probe=deepcopy(candidate);probe['triple']['relation_id']=target
if OntologyGuard(load_snapshot(base.ontology)).check(probe)['status']!='pass':raise ValueError('existing declared types are incompatible with the proposed reused relation')
audit=repo.get_audit(identity,decision['audit_id']);state=(audit.get('agent_state') or {}).get('state',{})
protected=set(state.get('protected_ids',[]))|{eid for v in audit['votes'] if v.get('response_status')=='ok' for eid in v.get('counter_evidence_ids',[])}
protected|={eid for issue in audit['context'].get('issue_history',[]) if issue.get('status','open')!='resolved' for eid in issue.get('evidence_ids',[])}
evidence=sorted({eid for v in votes for eid in v.get('supporting_evidence_ids',[])})
plan={'candidate_id':identity,'base_revision':candidate['revision'],'expected_row_version':current['row_version'],
    'source_audit_id':audit['audit_id'],'source_proposal_digest':proposal['proposal_digest'],'changes':{'relation_id':target},
    'evidence_ids':evidence,'protected_evidence_ids':sorted(protected),'additional_verified_evidence':[],
    'reason':'本体 Qwen 有效修订票要求优先复用既有横风影响弹道关系。本次只提交 R_1654fcf38913d94cb712 正式映射，保留完整原表面关系“通过改变空气阻力的方向来影响”及全部限定，不把它声明为泛化关系的同义别名。请核验映射是否保留原文事实；若机制语义无法由该映射及保留的原文说明表达，应反对而非批准。后续定义说明仍另经本体三席审核，不预先视为通过。',
    'requires_original_three_model_revision_approval':True,'requires_fresh_new_revision_review':True}
(OUT/'crosswind_reuse_plan.json').write_text(json.dumps({'items':[plan],'selected':1,'model_calls':0,'candidate':candidate,'existing_property':catalog(base.ontology)[target]},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'candidate_id':identity,'base_revision':candidate['revision'],'triple':candidate['triple'],'existing_property':catalog(base.ontology)[target],'model_calls':0},ensure_ascii=False))
