"""Record actionable valid objections; preserve both approvals and original deltas."""
from pathlib import Path
import json
from ontology_review.draft_repair import repair_draft
from review_agent.repository import ReviewRepository

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/ontology_publication_20261031'
repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');receipts=[]
for identity,reuse in (
    ('ext_4272d42b83144e8c8da86d3530f76019',['R_1654fcf38913d94cb712','R_ed105514b06838a0a6ff']),
    ('ext_f9b004e2df2c4613aa4d060302238889',['R_12a1cf5c80d7e73a97a2','R_b204199d1589f60b3c39','C_4f620706279a1779b20b'])):
    task=repo.extension_status(identity);audit=repo.ontology_audit(task['latest_audit_id'])
    vote=next(v for v in audit['votes'] if v['voter_id']=='judge_a' and v['response_status']=='ok')
    if vote['decision']!='revise':raise RuntimeError('expected actual valid revise vote')
    evidence=sorted(set(vote['supporting_evidence_ids'])|set(vote['counter_evidence_ids']))
    result=repair_draft(repo,ROOT/'runs/ontology_releases',identity,expected_row_version=task['row_version'],
        repair_key='record-source-reuse-obligation-20261031',action='annotate',
        actor='codex_user_authorized_ontology_processing',
        reason='用户要求处理本体审核及发布；原三席已收齐，Qwen 有效 revise 与另外两席 approve 形成真实建模分歧。记录复用及约束范围义务，不将其解释为通过，也不删除另一方原票。',
        evidence_ids=evidence,review_references=[{'audit_id':audit['audit_id'],'round_id':vote['round_id'],'voter_id':'judge_a'}],
        resolution={'status':'unresolved_modeling_obligation','reuse_schema_ids':reuse,
            'required_work':['核验复用既有关系/组合表达是否保留原机制语义','若确需独立关系，提供其不可替代性及通用约束范围证据',
                '自然语言能力问题必须真实审核，不得将结构检查代作语义通过'],
            'candidate_type_prerequisite':{'candidate_id':'cand_22b9bb5d743bc11d392d','field':'head_type','status':'requires_independent_candidate_revision'} if identity.startswith('ext_f9') else None,
            'valid_approve_votes_preserved':True,'valid_revise_vote_preserved':True,'automatically_publishable':False,
            'operator_has_not_fabricated_a_reviewer_decision':True})
    receipts.append(result)
(OUT/'modeling_obligations.json').write_text(json.dumps({'model_calls':0,'items':receipts},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'annotated':len(receipts),'model_calls':0,'delta_changed':False,'original_votes_changed':False},ensure_ascii=False))
