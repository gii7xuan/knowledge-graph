"""Reconcile completed votes with source/modeling facts; never amend a vote."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
from ontology_review.contracts import RESPONSE_SCHEMA, proposal_digest, vote_binding
from ontology_review.delta import apply_delta, catalog
from ontology_review.publisher import current_release_id
from ontology_review.validators import response_errors, consensus
from ontologyBuild.release_service import load_release
from ontologyBuild.rdf_graph import parse_turtle
from review_agent.contracts import sha256_json
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from rdflib import RDF, RDFS, OWL, URIRef


def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def write(name, value): (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    m=read(OUT/'prepared_manifest.json');p=read(OUT/'prepared_proposal.json')
    votes=read(OUT/'modeling_votes_20261113.json');summary=read(OUT/'modeling_summary_20261113.json')
    old_root=ROOT/'outputs/targeted_regression_20261112'
    old_p=read(old_root/'prepared_b01_proposal.json');old_votes=read(old_root/'modeling_votes.json')
    assert summary['new_model_calls']==3 and summary['cumulative_model_calls']==19
    assert len(votes)==3 and all(v['response_status']=='ok' for v in votes)
    assert proposal_digest(p)==p['proposal_digest']
    by_member={v['id']:v for v in p['committee_members']}
    for v in votes:
        assert not response_errors({k:v[k] for k in RESPONSE_SCHEMA['required']},p)
        assert all(v[k]==value for k,value in vote_binding(by_member[v['voter_id']],p).items())
        assert v['transport_metrics']['http_status']==200
    current=next(v for v in votes if v['voter_id']=='judge_a')
    prior=next(v for v in old_votes if v['voter_id']=='judge_a')
    cat=catalog(p['base_schema']);graph=parse_turtle(p['source_patch']['base_rdf'])
    identities=['C_a7877ae0fe8183f8b986','C_cbcb15d49fba77017748','C_f0037f9e5d770063017b',
                'C_85d7c2e6732f8a304f88','C_afb01b87134088624d72','C_0db798544ad999e5723f']
    schema_facts={i:{k:cat[i].get(k) for k in ('id','label','schema_kind','iri','description','parents')} for i in identities}
    for identity in ('C_a7877ae0fe8183f8b986','C_cbcb15d49fba77017748'):
        assert (URIRef(cat[identity]['iri']),RDF.type,OWL.Class) in graph
    precedents=[]
    for identity in ('R_52c7e2062efd9858ce95','R_a7404479f25f40e36270'):
        item=cat[identity]
        assert item['range']==['C_a7877ae0fe8183f8b986']
        assert (URIRef(item['iri']),RDFS.range,URIRef(cat['C_a7877ae0fe8183f8b986']['iri'])) in graph
        precedents.append({k:item.get(k) for k in ('id','label','domain','range','iri')})
    unchanged_constraints=[o for o in old_p['operations'] if o['kind']=='revise_constraint' and o['target']!='C_88ecbdb51d6e44007aec']
    constraints=[o for o in p['operations'] if o['kind']=='revise_constraint']
    assert unchanged_constraints==constraints
    coordinate_definition=next(o['changes']['definition'] for o in p['operations'] if o['target']=='C_ad498a2e8a3eb22fdd25')
    assert current['suggested_changes']['C_ad498a2e8a3eb22fdd25']['definition']==coordinate_definition
    text=read(OUT/'prepared_competency_request.json')['source_documents'][0]['text']
    needle='其阻力系数与标准弹阻力系数的比值称为弹形系数'
    position=text.index(needle);start=text.rfind('\n\n',0,position)+2
    end=text.index('\n\n',text.index('\\tag {1-33}',position))+2
    ratio_quote={'document_id':p['evidence'][0]['document_id'],'start':start,'end':end,'quote':text[start:end]}
    # A diagnostic alternative, compiled on the approved base, never registered
    # as a task or adopted as a correction.
    alternative=deepcopy(p['operations'])
    for op in alternative:
        if op['kind']=='revise_constraint' and op['target'] in current['suggested_changes']:
            changes=current['suggested_changes'][op['target']]
            if 'range' in changes:op['changes']['range']=changes['range']
    base=load_release(Path(m['release_root'])/p['base_release_id'])
    hypothetical=apply_delta(base,alternative,{e['evidence_id'] for e in p['evidence']})
    guard=OntologyGuard(load_snapshot(hypothetical['merged_schema']))
    cases=read(old_root/'frozen_regression_cases.json')['cases'][:6]
    effects=[]
    for case in cases:
        result=guard.check(case['candidate']);expected=case['expected']['B01']
        effects.append({'case_id':case['case_id'],'desired':expected,
            'observed_if_ranges_restored':{k:result[k] for k in ('status','domain_status','range_status')},
            'matches_original_source_bound_test':all(result[k]==v for k,v in expected.items())})
    assert sum(x['matches_original_source_bound_test'] for x in effects)==2
    write('qwen_range_suggestion_effect_20261113.json',{'diagnostic_only':True,'registered_or_applied':False,
        'expectations_authored_by':'ai','human_gold':False,'original_expectations_changed':False,
        'active_case_count':6,'matches_under_current_draft':6,'matches_if_two_ranges_restored':2,
        'failures_reintroduced':4,'results':effects})
    findings=[
        {'issue_id':'representation_of_quantities_and_shapes','type':'modeling_convention_requires_clarification',
         'facts':'动能、头部形状在批准基线中已声明为OWL类；另两条消耗关系也以动能为range。',
         'assessment':'不能仅凭它们可被自然语言称为属性，就在本项目中自动把客体换回物体。若改用literal或另一种重述模式，需要明确统一建模规则及迁移范围。此为本会话AI判断，不覆盖原席票。'},
        {'issue_id':'suggested_shape_id_label','type':'exact_id_label_check',
         'facts':'建议的 C_f0037f9e5d770063017b 实际标签是弹头部，C_cbcb15d49fba77017748 才是头部形状。',
         'assessment':'建议会恢复原实体端点，不能将该ID描述为已有弹头部形状类。'},
        {'issue_id':'coordinate_noop_suggestion','type':'non_actionable_suggestion',
         'facts':'本轮建议的坐标定义与当前提案逐字相同。',
         'assessment':'按该文本替换不会产生修订。需说明具体哪一范围判断及可执行变更，而非机械重评。'},
        {'issue_id':'drag_and_form_coefficients','type':'source_scope_requires_explicit_argument',
         'facts':'原文及既有弹形系数类把i定义为同马赫数下阻力系数相对标准弹阻力系数的比值。',
         'assessment':'不能在没有分类依据时，将弹形系数i与阻力系数Cx0的定义范围混同；如果需要更宽上位类，应给出具体证据和建模方案。'},
        {'issue_id':'cross_round_constraint_assessment','type':'changed_assessment_of_identical_operations',
         'facts':'两轮三处关系约束操作完全相同，源证据ID也相同；前轮constraint_strength为pass，本轮为fail，必要性检查本轮仍称三处修订均受原文支持。',
         'assessment':'模型可以修正判断，但应明确新增前提或判据。保留两轮有效票，不把意见变化本身当作无效票或批准。'}]
    write('review_findings_20261113.json',{'annotated_by':'ai','human_gold':False,'vote_amendment_authority':False,
        'schema_facts':schema_facts,'existing_quantity_range_precedents':precedents,
        'three_constraint_operations_identical_across_rounds':True,'constraint_operations_sha256':sha256_json(constraints),
        'prior_constraint_strength':next(c['status'] for c in prior['checks'] if c['check']=='constraint_strength'),
        'current_constraint_strength':next(c['status'] for c in current['checks'] if c['check']=='constraint_strength'),
        'coordinate_suggestion_identical_to_current':True,'form_factor_exact_source':ratio_quote,'findings':findings})
    questions=[
        '在本体已把动能、头部形状声明为类，且另两条消耗关系已指向动能类的约定下，你的属性不得作object-property客体判断依赖哪条项目规则？请区分来源事实与表示方式选择。',
        'C_f0037f9e5d770063017b的实际标签是弹头部。请明确建议要保留实体端点，还是采用已存在的头部形状类；说明语义角色和证据。',
        '原文正激波与斜激波组成分离波，为何不足以支持本属性的部分关系？请指出补丁实际加入的哪条公理过强，并区分命名domain/range与存在性或基数约束。',
        '请根据原文i的比值定义，说明为什么把弹形系数纳入阻力系数类的定义范围；给出明确分类证据或标记为建模选项。',
        '坐标定义的建议与当前文本完全相同。若仍需修订，请给出实际不同的文本、受影响短语和证据，或者说明原建议为何不产生变化。',
        '三处约束操作未改变，constraint_strength从前轮pass变为本轮fail。请说明变化所依据的新前提或规则；不要求改变当前revise结论。']
    write('clarification_questions_draft_20261113.json',{'task_type':'draft_objection_explanation_only',
        'proposal_digest':p['proposal_digest'],'original_voter_id':'judge_a','current_vote_sha256':sha256_json(current),
        'prior_same_voter_vote_sha256':sha256_json(prior),'questions':questions,
        'other_reviewer_votes_included':False,'may_amend_or_replace_vote':False,'calls_authorized':0,'dispatched':False,
        'instruction':'Explain source/assumptions and preserve actual uncertainty. This is not a request to approve, not a format repair, and not a formal vote amendment.'})
    with sqlite3.connect(Path(m['database']).as_uri()+'?mode=ro',uri=True) as c:
        rows=c.execute('SELECT state,metadata_json FROM request_attempts').fetchall()
    states=[state for state,raw in rows if all(json.loads(raw).get('binding',{}).get(k)==p[k] for k in ('task_id','audit_id','round_id','proposal_digest'))]
    assert states==['completed']*3
    assert all(digest(Path(path))==value for path,value in read(OUT/'protected_inputs.json').items())
    assert current_release_id(ROOT/'runs/ontology_releases')==p['base_release_id']
    total_tokens=sum(v['input_tokens']+v['output_tokens'] for v in votes)
    assert total_tokens==summary['budget']['usage']['input_tokens']+summary['budget']['usage']['output_tokens']==223465
    receipt=read(OUT/'case_budget_authorization_receipts_20261113.json')
    assert len(receipt)==1 and json.loads(receipt[0]['additions_json'])=={'tokens':277343}
    check={'status':'passed','client_date':'2026-11-13','observed_at_utc':datetime.now(timezone.utc).isoformat(),
        'authorized_calls':3,'actual_calls':3,'cumulative_model_calls':19,'all_three_valid_bound_votes':True,
        'decisions':{v['voter_id']:v['decision'] for v in votes},'consensus':consensus(votes,p)['result'],
        'new_unknown_requests':0,'previous_reviews_and_protected_inputs_unchanged':True,
        'case_token_addition_authorized_and_exact':277343,'this_pass_reported_tokens':total_tokens,
        'formal_vote_amended':False,'suggested_range_changes_adopted':False,
        'full_chapter_review_executed':False,'sample_model_review_executed':False,'published':False,'production_written':False,
        'modeling_votes_sha256':digest(OUT/'modeling_votes_20261113.json'),'completed_database_sha256':digest(Path(m['database']))}
    write('modeling_delivery_verification_20261113.json',check)
    print(json.dumps({k:check[k] for k in ('status','actual_calls','cumulative_model_calls','decisions','consensus','new_unknown_requests','formal_vote_amended')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
