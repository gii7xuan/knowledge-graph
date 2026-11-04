"""Continue with individually read definitions and explicit scope corrections."""
from pathlib import Path
import json
from document.hierarchy_index import load_document_index
from evidence.hierarchical_retriever import context_sentences,document_sentence_evidence
from evidence.verification import make_evidence_bundle,verify_evidence_bundle
from review_agent.repository import ReviewRepository
from review_agent.proposal_resolver import selected_proposal
from review_agent.revision_patch import merge_triple_patch

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/candidate_revision_resolution_20261030'
repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');plans=[];deferred=[]


def add(identity,changes,evidence,reason,context_anchor=None):
    try:
        current=repo.get_candidate(identity);candidate=repo.get_revision(identity);decision=repo.latest_decision(identity)
        proposal,_=selected_proposal(repo,candidate,decision);audit=repo.get_audit(identity,decision['audit_id'])
        state=(audit.get('agent_state') or {}).get('state',{})
        archive={e['evidence_id']:e for p in audit['rounds'] for e in p['evidence_bundle']['items']}
        archive.update({e['evidence_id']:e for e in state.get('archive',[])})
        manifest=repo.get_run(candidate['run_id']);document=load_document_index(manifest['document_path'],candidate['source_index_hash'],require_verified=True)
        additional=[]
        if context_anchor:
            for sentence in context_sentences(document,archive[context_anchor],before=2,after=1):
                item=document_sentence_evidence(document,sentence,'adjacent_sentence')
                if item['evidence_id'] not in archive:additional.append(item)
        verified=verify_evidence_bundle(make_evidence_bundle(additional,['source_context_for_specific_revision']),document,candidate)
        if verified.get('rejected_items'):raise ValueError('supplemental context is not verified')
        protected=set(state.get('protected_ids',[]))|{eid for v in audit['votes'] if v.get('response_status')=='ok' for eid in v.get('counter_evidence_ids',[])}
        protected|={eid for issue in audit['context'].get('issue_history',[]) if issue.get('status','open')!='resolved' for eid in issue.get('evidence_ids',[])}
        archive.update({e['evidence_id']:e for e in verified['items']})
        if not protected<=set(archive) or not set(evidence)<=set(archive):raise ValueError('material evidence missing')
        if merge_triple_patch(candidate['triple'],changes,strict=True)==candidate['triple']:raise ValueError('no substantive change')
        plans.append({'candidate_id':identity,'base_revision':candidate['revision'],'expected_row_version':current['row_version'],
            'source_audit_id':audit['audit_id'],'source_proposal_digest':proposal['proposal_digest'],'changes':changes,
            'evidence_ids':evidence,'reason':reason,'protected_evidence_ids':sorted(protected),
            'additional_verified_evidence':verified['items'],'pilot':len(plans)<3,
            'requires_original_three_model_revision_approval':True,'requires_fresh_new_revision_review':True})
    except Exception as exc:deferred.append({'candidate_id':identity,'reason':str(exc),'error_type':type(exc).__name__})


positive_cases=[
 ('cand_07364ae53742e434e3d0',['ev_eace97008a1917d1206e']),
 ('cand_0b5c049758247f08eadc',['ev_b9da151192c2b2f7471b']),
 ('cand_0dcaf0852ede14b60419',['ev_ada05bba41d53aa0ad16']),
 ('cand_22c5626b36b51df8ff34',['ev_267a78b5abead56f6746']),
 ('cand_2865fb734cf8180f057f',['ev_b617bb0f27405e9df413']),
 ('cand_2cd7690d60efe95c709f',['ev_0e1b47909f453c31cc1a','ev_5c268a7204504407c9c5']),
 ('cand_35956325a17e5353cbf1',['ev_b617bb0f27405e9df413']),
 ('cand_374c28ba5ab48df4e982',['ev_fcb5968b00ade4ff51ec']),
 ('cand_538b9cced7f05cf5e3db',['ev_80d83de10d28c23f7032']),
 ('cand_59b0aa3bf19902e70d7d',['ev_80d83de10d28c23f7032']),
 ('cand_5fdf22b00633c6fdab5e',['ev_fbae504b0457f730dcde']),
 ('cand_732ab995054cb268dec8',['ev_3849b954479b29218997']),
 ('cand_7619a3990068828ab44a',['ev_fb32c0b835deace58bfa']),
 ('cand_8d3a0c00707062080011',['ev_1e26809c94d2f44a9b38']),
 ('cand_9974bf380d36704b8889',['ev_ada05bba41d53aa0ad16']),
 ('cand_9a551948f6e22e7d67bd',['ev_0d4e6270c83a423e0f1e']),
 ('cand_9cd44b875d803e850f49',['ev_05755ce07d3a6733daa1']),
 ('cand_acc8df0a5597573759d2',['ev_a242ff812cf58cb89c21']),
 ('cand_af1308ab3c8f4464383e',['ev_c0e90b81c90185ea5e1f']),
 ('cand_bd3d789e080742ba66be',['ev_2040410bc4a650f89596']),
 ('cand_d4e85d36ac58d4bc88a6',['ev_ade0f46b3b0ea2b856db']),
 ('cand_e5b3446c5db0e5799dee',['ev_3b77ca0c26121d6efb50']),
 ('cand_e923c79bb416ec0f8f6a',['ev_95a54453777be678f78a']),
 ('cand_ec429deeeb3552229e30',['ev_4aea6780870022e78cf3','ev_96b4dcfafa637ae61fc5']),
]
for identity,evidence in positive_cases:
    add(identity,{'qualifiers':{'polarity':'positive'},'qualifier_status':{'polarity':'extracted'}},evidence,
        '已逐条核对对应的直接定义、命名或肯定陈述，只提交极性修订；保留所有原有条件、模态、比较范围和原票。是否充分由原三席独立判断，不将 unknown 默认转正。')
add('cand_1f0fa29137bd460e525e',{'qualifiers':{'polarity':'positive','quantifier':'凡是'},'qualifier_status':{'polarity':'extracted','quantifier':'extracted'}},
    ['ev_fcb5968b00ade4ff51ec'],'补回原文明示的肯定极性与“凡是/都会”全称限定，完整保留原有超声速及局部气流条件。')
for identity,scope in [('cand_bc646ed2a183364b23c9','我国，文中上述低伸弹道表'),('cand_d8c43e8fee277759ed82','我国，文中上述地面火炮外弹道表')]:
    add(identity,{'qualifiers':{'polarity':'positive','applicability':scope},'qualifier_status':{'polarity':'extracted'}},
        ['ev_41fab59235dbcad2f457','ev_2f28bfed003541ab96d5'],
        '补回肯定极性，并明确“我国/上述”所指的文本内范围；不把文中指定表的编制依据扩大为所有同名表的普遍规律。')
add('cand_63f090f18b85f4c074b3',{'qualifiers':{'applicability':'接近弹丸表面，受空气黏性影响的一薄层空气'}},
    ['ev_bec8046b43e4c1ea2fa2','ev_97f493dd870d815d9c2d'],
    '补回原文“薄”的适用范围，保留既有表面、黏性和通常命名限定；额外核对前后原句，避免裁掉前提。',context_anchor='ev_bec8046b43e4c1ea2fa2')
add('cand_e701d8ef2859275576ea',{'qualifiers':{'modality':'can'}},['ev_5f04a1717f2ae5f320ad','ev_fa8269096aa5c7db9eeb'],
    '按原文“可表示为”保留表达能力模态，不改变原比例定义与数学内容，不增加计算步骤。')
(OUT/'continuation_revision_plan.json').write_text(json.dumps({'items':plans,'selected':len(plans),'deferred':deferred,
    'model_calls':0,'candidates_modified':False},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'selected':len(plans),'deferred':deferred,'model_calls':0,'candidates_modified':False},ensure_ascii=False))
