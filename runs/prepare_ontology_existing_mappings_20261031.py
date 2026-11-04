"""Find unique compatible declared properties; review mappings instead of inventing gaps."""
from pathlib import Path
import json
from copy import deepcopy
from review_agent.repository import ReviewRepository
from review_agent.proposal_resolver import selected_proposal
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from ontologyBuild.release_service import load_release
from ontology_review.publisher import current_release_id
from schema.relation_registry import RelationRegistry

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/ontology_publication_20261031'
repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');base=load_release(ROOT/'runs/ontology_releases'/current_release_id(ROOT/'runs/ontology_releases'))
registry=RelationRegistry(base.ontology);guard=OntologyGuard(load_snapshot(base.ontology));plans=[];dossiers=[]
for current in repo.list_candidates('run_chapter1_20261028_resume1',limit=1000000):
    candidate=repo.get_revision(current['candidate_id']);decision=repo.latest_decision(current['candidate_id']) or {}
    if decision.get('source_support_result')!='supported' or current['human_status']!='pending' or candidate.get('ontology_release_id')!=base.manifest['release_id']:continue
    matched=registry.resolve(candidate['triple']['surface_relation'])
    identities=matched.get('matched_ids',[]) or ([matched['relation_id']] if matched.get('relation_id') else [])
    compatible=[]
    for identity in identities:
        probe=deepcopy(candidate);probe['triple']['relation_id']=identity
        if guard.check(probe)['status']=='pass':compatible.append(identity)
    if len(compatible)!=1 or compatible[0]==candidate['triple'].get('relation_id'):continue
    try:
        proposal,votes=selected_proposal(repo,candidate,decision)
        if len(votes)!=3:raise ValueError('current independent source committee is incomplete')
        audit=repo.get_audit(current['candidate_id'],decision['audit_id']);state=(audit.get('agent_state') or {}).get('state',{})
        archive={e['evidence_id']:e for p in audit['rounds'] for e in p['evidence_bundle']['items']}
        archive.update({e['evidence_id']:e for e in state.get('archive',[])})
        protected=set(state.get('protected_ids',[]))|{eid for v in audit['votes'] if v.get('response_status')=='ok' for eid in v.get('counter_evidence_ids',[])}
        protected|={eid for issue in audit['context'].get('issue_history',[]) if issue.get('status','open')!='resolved' for eid in issue.get('evidence_ids',[])}
        if not protected<=set(archive):raise ValueError('protected source material missing')
        evidence=sorted({eid for v in votes for eid in v.get('supporting_evidence_ids',[])} & set(archive))
        if not evidence:raise ValueError('no current verified source support')
        plans.append({'candidate_id':current['candidate_id'],'base_revision':candidate['revision'],'expected_row_version':current['row_version'],
            'source_audit_id':audit['audit_id'],'source_proposal_digest':proposal['proposal_digest'],'changes':{'relation_id':compatible[0]},
            'evidence_ids':evidence,'protected_evidence_ids':sorted(protected),'additional_verified_evidence':[],
            'reason':'完整正式本体检索后，原文关系的声明标签/别名有且仅有一个与当前已有实体类型相容的属性。仅提交正式关系映射修订；附真实类和属性定义，由原三席判断语义是否适配。不得从 domain/range 猜类型，不新增同名属性，也不复用旧版批准。',
            'requires_original_three_model_revision_approval':True,'requires_fresh_new_revision_review':True})
        dossiers.append({'candidate_id':current['candidate_id'],'head':candidate['triple']['head'],'relation':candidate['triple']['surface_relation'],
            'tail':candidate['triple']['tail'],'old_relation_id':candidate['triple'].get('relation_id'),'proposed_relation_id':compatible[0]})
    except Exception as exc:dossiers.append({'candidate_id':current['candidate_id'],'held':str(exc)})
(OUT/'existing_mapping_plan.json').write_text(json.dumps({'items':plans,'selected':len(plans),'model_calls':0,'candidates_modified':False,'dossiers':dossiers},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'selected':len(plans),'dossiers':dossiers,'model_calls':0},ensure_ascii=False))
