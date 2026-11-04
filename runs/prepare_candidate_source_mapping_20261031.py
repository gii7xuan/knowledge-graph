"""Curate source corrections and review incompatible formal mappings explicitly."""
from pathlib import Path
import json
from document.hierarchy_index import load_document_index
from evidence.hierarchical_retriever import context_sentences,document_sentence_evidence
from evidence.verification import make_evidence_bundle,verify_evidence_bundle
from review_agent.repository import ReviewRepository
from review_agent.proposal_resolver import selected_proposal
from review_agent.revision_patch import merge_triple_patch
from review_agent.admission import load_admission_artifacts
from ontologyBuild.ontology_adapter import iter_classes

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/candidate_revision_resolution_20261030'
repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');plans=[];deferred=[]

def add(identity,changes,evidence,reason,adjacent=None,extra_id=None):
    try:
        current=repo.get_candidate(identity);candidate=repo.get_revision(identity);decision=repo.latest_decision(identity)
        proposal,_=selected_proposal(repo,candidate,decision);audit=repo.get_audit(identity,decision['audit_id'])
        state=(audit.get('agent_state') or {}).get('state',{})
        archive={e['evidence_id']:e for p in audit['rounds'] for e in p['evidence_bundle']['items']}
        archive.update({e['evidence_id']:e for e in state.get('archive',[])})
        manifest=repo.get_run(candidate['run_id']);document=load_document_index(manifest['document_path'],candidate['source_index_hash'],require_verified=True)
        additional=[]
        if adjacent:
            for sentence in context_sentences(document,archive[adjacent],before=2,after=2):
                item=document_sentence_evidence(document,sentence)
                if item['evidence_id'] not in archive:additional.append(item)
        if extra_id and extra_id not in archive:
            found=[document_sentence_evidence(document,sentence) for sentence in document['sentences']]
            item=next(e for e in found if e['evidence_id']==extra_id)
            additional.append(item)
        verified=verify_evidence_bundle(make_evidence_bundle(additional,['source_context_for_specific_revision']),document,candidate)
        if verified.get('rejected_items'):raise ValueError('supplemental source anchor failed verification')
        protected=set(state.get('protected_ids',[]))|{eid for v in audit['votes'] if v.get('response_status')=='ok' for eid in v.get('counter_evidence_ids',[])}
        protected|={eid for issue in audit['context'].get('issue_history',[]) if issue.get('status','open')!='resolved' for eid in issue.get('evidence_ids',[])}
        archive.update({e['evidence_id']:e for e in verified['items']})
        if not protected<=set(archive) or not set(evidence)<=set(archive):raise ValueError('required material evidence missing')
        if merge_triple_patch(candidate['triple'],changes,strict=True)==candidate['triple']:raise ValueError('no substantive change')
        plans.append({'candidate_id':identity,'base_revision':candidate['revision'],'expected_row_version':current['row_version'],
            'source_audit_id':audit['audit_id'],'source_proposal_digest':proposal['proposal_digest'],'changes':changes,
            'evidence_ids':evidence,'reason':reason,'protected_evidence_ids':sorted(protected),
            'additional_verified_evidence':verified['items'],'pilot':len(plans)<3,
            'requires_original_three_model_revision_approval':True,'requires_fresh_new_revision_review':True})
    except Exception as exc:deferred.append({'candidate_id':identity,'reason':str(exc),'error_type':type(exc).__name__})

add('cand_466b9468afe0efc3434c',{'qualifiers':{'quantifier':'均'},'qualifier_status':{'quantifier':'extracted'}},
    ['ev_0beaf8dd9de212bcc203','ev_35ab5e98538f2389bf38'],
    '补回原句“均”所表达的全称范围；保留“今后”“除特别指明外”“外弹道中”的全部限制，不复用历史审核作为新版本批准。')
add('cand_c0f73fb07469b204dbf7',{'head':'高射火炮外弹道射表','head_type':'C_f459437ed9067a73ce23',
    'qualifiers':{'polarity':'positive','applicability':'我国，文中上述高射火炮外弹道射表'},'qualifier_status':{'polarity':'extracted'}},
    ['ev_41fab59235dbcad2f457','ev_2f28bfed003541ab96d5','ev_d0aefd173dd6805454d4'],
    '“编制”的主体是前文所指外弹道射表，不是火炮；使用原文显式表名与正式类定义核验，不从关系约束推定实体类别。保留“我国/上述”范围。',
    adjacent='ev_41fab59235dbcad2f457',extra_id='ev_d0aefd173dd6805454d4')
add('cand_087e253eb4e4af8bcf40',{'surface_relation':'被假定重合于'},
    ['ev_96b3496662a7edae2028','ev_53b28a16d7e392acb48c'],
    '原文陈述的是建模假设，不能作为无条件的物理重合事实。将“假定”显式纳入关系谓词，asserted 仅断言该假设的存在；保留本章建立运动方程、时刻等限制。若当前表示不足，应投反对或不确定，不能以缺少 assumed 枚举默认通过。')
for identity in ('cand_4a0747767cfb9020ae71','cand_ccda7f5e8cf2b462ccab','cand_d05f258f70eb3dfe2220'):
    add(identity,{'tail':'摩擦阻力、涡流阻力及超声速时所特有的波动阻力','tail_type':None,
        'qualifiers':{'polarity':'positive','condition':None,'applicability':None},
        'qualifier_status':{'polarity':'extracted','condition':'not_stated','applicability':'not_stated'}},
        ['ev_34a9e63d42e4c425b893'],
        '用原句完整三项组成替换单项“由…组成”，保留“主要”。“超声速时所特有”完整留在尾项中并且仅修饰波动阻力，不将其误作全部组成的全局条件。清除不再代表整个列表的单一分量类型；只是在该核验证据范围内未述全局条件，绝非删除波动阻力的限制。')
for identity,evidence in (
    ('cand_f9a86d764470a851cd8a',['ev_197b526479fee7b852fe','ev_a45c2dc8de96babdf36c']),
    ('cand_f4876022d0c83d291ac3',['ev_c15baaaa1767cf8bcdc6'])):
    add(identity,{'relation_id':None},evidence,
        '源实体是弹道系数，保留正确的原 head_type，禁止将其换成空气阻力函数来凑 domain/range。正式发布包中 R_37918d1714e9945397f3 的 domain 是空气阻力函数；本次仅撤回该不相容的已确认映射，保留“表示”的原文关系与所有限制，进入待完整查找状态。这不宣称需要新增同名关系，也不作本体扩展批准。请结合随附真实类/属性定义审核该映射撤回操作。')
add('cand_5b459021eb0a3fe91145',{'tail_type':'C_e0f77606c291cca8d429'},
    ['ev_3ac495284f0e81c6c629','ev_bcc35ff4d1e37f138b4c'],
    '原文明示尾项为“空气密度函数的标准定律 H(y)”，并给出密度比函数；结合随附实际类定义审核其类型，不仅凭“得到”关系 range 补类型。保留原表达及“分别”范围；新版本仍需独立复核。')

(OUT/'source_mapping_revision_plan.json').write_text(json.dumps({'items':plans,'selected':len(plans),'deferred':deferred,
    'model_calls':0,'candidates_modified':False},ensure_ascii=False,indent=2),encoding='utf-8')
release,_=load_admission_artifacts(repo,repo.get_revision('cand_5b459021eb0a3fe91145'))
print(json.dumps({'selected':len(plans),'deferred':deferred,'model_calls':0,'candidates_modified':False,
    'reviewed_class_definitions':[c for c in iter_classes(release.ontology) if c['id'] in {'C_e0f77606c291cca8d429','C_f459437ed9067a73ce23'}]},ensure_ascii=False))
