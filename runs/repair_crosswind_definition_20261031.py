"""Follow the valid reuse objection with one sourced definition annotation."""
from pathlib import Path
from types import SimpleNamespace
import json
from review_agent.repository import ReviewRepository
from review_agent.proposal_resolver import selected_proposal
from review_agent.agent_state import AgentState
from review_agent.tools import AgentTools
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from ontologyBuild.release_service import load_release
from ontology_review.delta import catalog
from ontology_review.draft_repair import repair_draft

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/ontology_publication_20261031'
repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');identity='ext_4272d42b83144e8c8da86d3530f76019'
task=repo.extension_status(identity);candidate=repo.get_revision('cand_cc7f508e0ee31b09d27d');current=repo.get_candidate(candidate['candidate_id'])
decision=repo.latest_decision(candidate['candidate_id']);proposal,votes=selected_proposal(repo,candidate,decision)
if candidate['triple']['relation_id']!='R_1654fcf38913d94cb712' or len(votes)!=3 or decision['source_support_result']!='supported':raise RuntimeError('reviewed source-preserving reuse mapping required')
base=load_release(ROOT/'runs/ontology_releases'/task['base_release_id']);guard=OntologyGuard(load_snapshot(base.ontology))
if current.get('pending_extension_id') not in {None,identity}:raise RuntimeError('candidate belongs to another task')
if not current.get('pending_extension_id'):
    state=AgentState(candidate,{},proposal['audit_id'])
    runtime=SimpleNamespace(state=state,orchestrator=SimpleNamespace(ontology_guard=guard),issue=lambda *a,**k:None)
    lookup=AgentTools(runtime).inspect_ontology(relation_id=candidate['triple']['relation_id'],surface_relation=candidate['triple']['surface_relation'],
        issue='原本体有效修订票要求复用既有关系；本次绑定当前已独立审核的候选来源')['existing_schema_search']
    # Reattach only to the exact original gap, never silently create a new task.
    from review_agent.contracts import sha256_json
    binding={k:candidate[k] for k in ('ontology_release_id','ontology_version','profile_hash')}
    gap=sha256_json([binding,task['change_kind'],' '.join(candidate['triple']['surface_relation'].split()).casefold(),candidate['triple']['head_type'],candidate['triple']['tail_type']])
    with repo.connect() as c:existing=c.execute('SELECT extension_id FROM ontology_extension_tasks WHERE gap_key=?',(gap,)).fetchone()
    if not existing or existing[0]!=identity:raise RuntimeError('original gap identity changed; explicit new task required')
    result=repo.create_extension_request(candidate,expected_row_version=current['row_version'],proposal=proposal,change_kind=task['change_kind'],
        description=task['description'],proposed_changes=task.get('proposed_changes',{}),evidence_ids=['ev_c8bc5b0bf7eca019fc99'],full_lookup=lookup)
    if result['task']['extension_id']!=identity:raise RuntimeError('unexpected extension identity')
task=repo.extension_status(identity)
key='replace-duplicate-crosswind-property-with-definition-20261031'
if any(h['repair_key']==key for h in task.get('draft_repair_history',[])):
    print(json.dumps({'extension_id':identity,'already_repaired':True},ensure_ascii=False));raise SystemExit(0)
audit=repo.ontology_audit(task['latest_audit_id']);qwen=next(v for v in audit['votes'] if v['voter_id']=='judge_a' and v['response_status']=='ok')
if qwen['decision']!='revise':raise RuntimeError('original valid reuse objection required')
definition='横风对弹道的影响。教材说明，横风通过改变空气阻力的方向来影响弹道。'
if catalog(base.ontology)['R_1654fcf38913d94cb712'].get('description'):raise RuntimeError('existing definition must be preserved explicitly before replacement')
receipt=repair_draft(repo,ROOT/'runs/ontology_releases',identity,expected_row_version=task['row_version'],repair_key=key,
    action='replace',actor='codex_user_authorized_ontology_processing',
    reason='落实有效 revise 的优先复用方案：撤下未发布的专用新属性草案，给既有横风影响弹道属性补充原文机制说明。不把机制短语作为同义别名，不改变 domain/range/类层级或增加关系全称断言；修订差异须重新三席审核。',
    evidence_ids=['ev_c8bc5b0bf7eca019fc99'],
    operations=[{'kind':'revise_definition','target':'R_1654fcf38913d94cb712','changes':{'definition':definition,'definition_language':'zh'},'evidence_ids':['ev_c8bc5b0bf7eca019fc99']}],
    review_references=[{'audit_id':audit['audit_id'],'round_id':qwen['round_id'],'voter_id':'judge_a'}],
    resolution={'status':'substantive_reuse_correction_requires_fresh_review','reuse_schema_ids':['R_1654fcf38913d94cb712','R_ed105514b06838a0a6ff'],
        'candidate_mapping_reviewed_independently':True,'source_mechanism_retained':True,'domain_range_unchanged':True,'published':False})
task=repo.extension_status(identity)
(OUT/'crosswind_definition_repair.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'extension_id':identity,'delta_revision':task['delta_revision'],'operations':task['delta']['operations'],
    'added_rdf':task['delta']['owl_patch']['added'],'removed_rdf':task['delta']['owl_patch']['removed'],
    'checks':{k:v['status'] for k,v in receipt['repair']['checks'].items()},'model_calls':0},ensure_ascii=False))
