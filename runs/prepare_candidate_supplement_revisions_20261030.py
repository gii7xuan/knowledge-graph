"""Curate exact missing definitions and retain all old source material."""
from pathlib import Path
import json
from document.hierarchy_index import load_document_index
from evidence.hierarchical_retriever import document_sentence_evidence
from evidence.verification import make_evidence_bundle,verify_evidence_bundle
from review_agent.repository import ReviewRepository
from review_agent.proposal_resolver import selected_proposal
from review_agent.revision_patch import merge_triple_patch

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/candidate_revision_resolution_20261030'
repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');plans=[]


def add(identity,patch,source_ids,reason,new_texts=()):
    current=repo.get_candidate(identity);candidate=repo.get_revision(identity);decision=repo.latest_decision(identity)
    proposal,_=selected_proposal(repo,candidate,decision);audit=repo.get_audit(identity,decision['audit_id'])
    state=(audit.get('agent_state') or {}).get('state',{});manifest=repo.get_run(candidate['run_id'])
    document=load_document_index(manifest['document_path'],candidate['source_index_hash'],require_verified=True)
    new=[]
    for text in new_texts:
        matches=[s for s in document['sentences'] if s['text']==text]
        if len(matches)!=1:raise RuntimeError('new source sentence must be exact and unique')
        new.append(document_sentence_evidence(document,matches[0],'parent_section'))
    bundle=verify_evidence_bundle(make_evidence_bundle(new,['operator_targeted_source_context']),document,candidate)
    if bundle.get('rejected_items'):raise RuntimeError('invalid supplemental anchor')
    evidence=list(source_ids)+[e['evidence_id'] for e in bundle['items']]
    archive={e['evidence_id']:e for p in audit['rounds'] for e in p['evidence_bundle']['items']}
    archive.update({e['evidence_id']:e for e in state.get('archive',[])});archive.update({e['evidence_id']:e for e in bundle['items']})
    protected=set(state.get('protected_ids',[]))|{eid for v in audit['votes'] if v.get('response_status')=='ok' for eid in v.get('counter_evidence_ids',[])}
    protected|={eid for issue in audit['context'].get('issue_history',[]) if issue.get('status','open')!='resolved' for eid in issue.get('evidence_ids',[])}
    protected|={e['evidence_id'] for e in bundle['items']}
    if not set(evidence)<=set(archive) or not protected<=set(archive):raise RuntimeError('missing protected evidence')
    if merge_triple_patch(candidate['triple'],patch,strict=True)==candidate['triple']:raise RuntimeError('no-op')
    plans.append({'candidate_id':identity,'base_revision':candidate['revision'],'expected_row_version':current['row_version'],
        'source_audit_id':audit['audit_id'],'source_proposal_digest':proposal['proposal_digest'],'changes':patch,'evidence_ids':evidence,
        'reason':reason,'protected_evidence_ids':sorted(protected),'additional_verified_evidence':bundle['items'],
        'pilot':len(plans)<3,'requires_original_three_model_revision_approval':True,'requires_fresh_new_revision_review':True})


add('cand_c4ca4af2f9fe7df47915',{'qualifiers':{'polarity':'positive'},'qualifier_status':{'polarity':'extracted'}},[],
    '补充此前遗漏的直接定义原句后，核验肯定极性；不以另一个近似句代替标准弹这一关键限定，保留此前支持不足意见。',
    ['标准弹的阻力系数  $C_{x0}$  与  $Ma$  的函数关系称为空气阻力定律。'])
for identity in ('cand_2b5fdd8e561c6e3c9661','cand_3b412a707f4f4fa04a2a'):
    add(identity,{'head':'弹丸的空气阻力','qualifiers':{'polarity':'positive','applicability':'攻角 δ=0° 的弹丸',
        'condition':'式（1-30）所述横截面积、动能及空气密度的联合正比关系'},
        'qualifier_status':{'polarity':'extracted','applicability':'extracted','condition':'extracted'}},
        ['ev_e4b2e909823e320b540d','ev_3b7f7d23438fd0e699dc','ev_96b4dcfafa637ae61fc5'],
        '补回原文弹丸范围、肯定极性和多因素联合限定，不将单项正比断言为无条件独立规律；是否足以表达联合关系由三席独立核验。')
add('cand_f796e51209e35106f7c1',{'qualifiers':{'quantifier':'i43<1.0 时相对43年标准弹较小，i43>1.0 时相反'},'qualifier_status':{'quantifier':'extracted'}},[],
    '补充原文相邻两个分支句，保留原有两个不等式条件与比较基准，增加原文比较方向，交由三席判断原“关联”是否仍过于笼统。',
    ['当  $i_{43} < 1.0$  时，说明该弹的弹形比43年标准弹的弹形好，它所受的空气阻力比43年标准弹的要小；','当  $i_{43} > 1.0$  时，则相反。'])
for identity in ('cand_1fbbeb8170f164a64347','cand_9f1d68a89a00599271f3','cand_cd33157f0f9114f8875b'):
    add(identity,{'tail':'摩擦阻力系数 Cxf、涡阻系数 Cxb、波阻系数 Cxw 的和',
        'qualifiers':{'polarity':'positive','modality':'can','quantifier':'三项之和'},
        'qualifier_status':{'polarity':'extracted','modality':'extracted','quantifier':'extracted'}},
        ['ev_1e2d9a3b5ae59338992d','ev_e1cfb2635d4854b6e4e6'],
        '针对单项范围不完整及“包含分量”并非原文的有效反对，保持原“分为”关系并将尾项实质补全为原文整体三项之和；不重试原方案，不改旧反对票。三条如均通过，会形成同一完整候选表述，后续完成规范身份映射后再按事实键去重，不重复计数。')
(OUT/'supplement_revision_plan.json').write_text(json.dumps({'items':plans,'selected':len(plans),'model_calls':0,'candidates_modified':False},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'selected':len(plans),'model_calls':0,'candidates_modified':False},ensure_ascii=False))
