"""Address a missing-symbol objection with real definitions and omitted quantifiers."""
from pathlib import Path
import json
from document.hierarchy_index import load_document_index
from evidence.hierarchical_retriever import document_sentence_evidence
from evidence.verification import make_evidence_bundle,verify_evidence_bundle
from review_agent.repository import ReviewRepository
from review_agent.proposal_resolver import selected_proposal

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/candidate_revision_resolution_20261030'
repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');plans=[];deferred=[]

def add(identity,changes,evidence,reason,extra_ids=()):
    try:
        current=repo.get_candidate(identity);candidate=repo.get_revision(identity);decision=repo.latest_decision(identity)
        proposal,_=selected_proposal(repo,candidate,decision);audit=repo.get_audit(identity,decision['audit_id'])
        state=(audit.get('agent_state') or {}).get('state',{})
        archive={e['evidence_id']:e for p in audit['rounds'] for e in p['evidence_bundle']['items']}
        archive.update({e['evidence_id']:e for e in state.get('archive',[])})
        run=repo.get_run(candidate['run_id']);doc=load_document_index(run['document_path'],candidate['source_index_hash'],require_verified=True)
        additional=[]
        if extra_ids:
            found={e['evidence_id']:e for e in (document_sentence_evidence(doc,s) for s in doc['sentences'])}
            additional=[found[eid] for eid in extra_ids if eid not in archive]
        verified=verify_evidence_bundle(make_evidence_bundle(additional,['targeted_missing_symbol_definitions']),doc,candidate)
        if verified.get('rejected_items'):raise ValueError('additional source failed verification')
        protected=set(state.get('protected_ids',[]))|{eid for v in audit['votes'] if v.get('response_status')=='ok' for eid in v.get('counter_evidence_ids',[])}
        protected|={eid for issue in audit['context'].get('issue_history',[]) if issue.get('status','open')!='resolved' for eid in issue.get('evidence_ids',[])}
        archive.update({e['evidence_id']:e for e in verified['items']})
        if not protected<=set(archive) or not set(evidence)<=set(archive):raise ValueError('required source material missing')
        plans.append({'candidate_id':identity,'base_revision':candidate['revision'],'expected_row_version':current['row_version'],
            'source_audit_id':audit['audit_id'],'source_proposal_digest':proposal['proposal_digest'],'changes':changes,
            'evidence_ids':evidence,'reason':reason,'protected_evidence_ids':sorted(protected),
            'additional_verified_evidence':verified['items'],'pilot':len(plans)<3,
            'requires_original_three_model_revision_approval':True,'requires_fresh_new_revision_review':True})
    except Exception as exc:deferred.append({'candidate_id':identity,'reason':str(exc),'error_type':type(exc).__name__})

definitions=['ev_05755ce07d3a6733daa1','ev_684d5caafe374ecc2554','ev_ada05bba41d53aa0ad16']
add('cand_e701d8ef2859275576ea',{'head_type':'C_53d15a0ecfc3dc8ecd93'},
    ['ev_5f04a1717f2ae5f320ad','ev_fa8269096aa5c7db9eeb',*definitions],
    '原文直接称头项为“弹道系数 C”，结合已发布类名核验真实类型。保留 can 模态与比例表达。针对 Gemma 上轮“变量未定义”的有效反对，本次补入原书 i、C_m、d 的真实定义原句；原票不变，不以重发同一证据取代反对。',extra_ids=definitions)
for identity in ('cand_bc646ed2a183364b23c9','cand_d8c43e8fee277759ed82'):
    add(identity,{'qualifiers':{'quantifier':'均'},'qualifier_status':{'quantifier':'extracted'}},
        ['ev_41fab59235dbcad2f457','ev_2f28bfed003541ab96d5'],
        '原文“我国均是以…为依据编制”中的“均”尚未保存在量词字段，本次补齐明示量词并保留已审核的“我国/文中上述”范围；仍需独立复核新版本，不能将其扩大为全部同名表的普遍规律。')
add('cand_087e253eb4e4af8bcf40',{'surface_relation':'重合于','qualifiers':{'modality':'assumed'}},
    ['ev_96b3496662a7edae2028','ev_53b28a16d7e392acb48c'],
    '保留原文建模假设的完整作用范围和时刻量词。针对 Gemma 对 asserted 的有效反对，中央模态契约已补入 assumed；本次真实改变模态，不将 asserted 的旧批准复用。关系恢复为重合于，假设性质由 assumed 明确表达；不声明弹轴与速度矢量在一般物理情况下必然重合。')
add('cand_2cd7690d60efe95c709f',{'surface_relation':'建立时引入','qualifiers':{'polarity':'positive'},'qualifier_status':{'polarity':'extracted'}},
    ['ev_0e1b47909f453c31cc1a','ev_5c268a7204504407c9c5'],
    '针对 Qwen 对“建立依据”为概括推断的有效反对，本次不再只改极性，而将谓词改为原句“引入”所陈述的建立过程，并保留标题限定为质点弹道方程组的建立；完整保留简化问题、反映质心运动主要规律的条件。新谓词必须由原三席独立核验，不覆盖旧拒绝票。')
(OUT/'final_followup_revision_plan.json').write_text(json.dumps({'items':plans,'selected':len(plans),'deferred':deferred,
    'model_calls':0,'candidates_modified':False},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'selected':len(plans),'deferred':deferred,'model_calls':0,'candidates_modified':False},ensure_ascii=False))
