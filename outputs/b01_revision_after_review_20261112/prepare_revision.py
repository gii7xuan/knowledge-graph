"""Source-check the completed B01 objections and prepare a narrower local draft."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from uuid import uuid4

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
from document.hierarchy_index import load_document_index
from evidence.hierarchical_retriever import document_sentence_evidence
from evidence.verification import make_evidence_bundle, verify_evidence_bundle
from ontologyBuild.release_service import load_release
from ontology_review import semantic_competency
from ontology_review.contracts import proposal_digest, versions
from ontology_review.delta import catalog
from ontology_review.draft_repair import repair_draft
from ontology_review.evidence import load_sources
from ontology_review.publisher import current_release_id
from ontology_review.review_payload import validator_payload
from ontology_review.runtime import OntologyReviewRuntime
from review_agent.case_budget import CaseBudget
from review_agent.cli import _configured_validators
from review_agent.contracts import sha256_json
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.service import load_config
from review_agent.shadow_revision import clone

SOURCE = ROOT / 'outputs/targeted_regression_20261112'


def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def write(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if (OUT / 'started.json').exists(): raise ValueError('Use the existing frozen revision; never overwrite it')
    m = read(SOURCE / 'prepared_manifest.json'); summary = read(SOURCE / 'modeling_summary.json')
    votes = read(SOURCE / 'modeling_votes.json'); original = read(SOURCE / 'prepared_b01_proposal.json')
    assert summary['new_model_calls'] == 3 and summary['cumulative_model_calls'] == 16
    assert {v['voter_id']:v['decision'] for v in votes} == {'judge_a':'revise','judge_c':'approve','judge_b':'approve'}
    assert all(v['response_status'] == 'ok' for v in votes)
    protected = {**read(SOURCE / 'protected_inputs.json'), **{str(p):digest(p) for p in [
        SOURCE / 'modeling_summary.json', SOURCE / 'modeling_votes.json', SOURCE / 'prepared_b01_proposal.json',
        SOURCE / 'user_authorization.json', SOURCE / 'dispatch_manifest.json', Path(m['database'])]}}
    assert all(digest(Path(path)) == value for path,value in protected.items())
    write(OUT / 'started.json', {'at':datetime.now(timezone.utc).isoformat(), 'preparation_only':True,
        'additional_model_calls_authorized':0, 'prior_cumulative_calls':16})
    write(OUT / 'protected_inputs.json', protected)
    repo = clone(Path(m['database']), OUT / 'workspace', allowed_root=ROOT / 'outputs')
    releases = OUT / 'workspace/releases'; releases.mkdir()
    base_id = original['base_release_id']
    shutil.copytree(Path(m['release_root']) / base_id, releases / base_id)
    with sqlite3.connect((Path(m['release_root'])/'release_catalog.sqlite3').as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(releases/'release_catalog.sqlite3') as dst: src.backup(dst)
    task = repo.extension_status(m['extension_id'])
    evidence, documents, candidates, cases = load_sources(repo, task)
    document = next(iter(documents.values())); candidate = next(iter(candidates.values()))
    text = document['source_snapshot']['canonical_text']
    old_source = read(SOURCE / 'prepared_b01_competency_request.json')['source_documents'][0]
    assert text == old_source['text']

    def span(start, end):
        assert 0 <= start < end <= len(text) and end-start <= 1200
        sections = [s for s in document['sections_by_id'].values() if s.get('start',0) <= start < end <= s.get('end',0)
                    and any(c['section_id']==s['section_id'] and c['document_start']<end and c['document_end']>start for c in document['chunks'])]
        section = min(sections,key=lambda s:s['end']-s['start'])
        raw = document_sentence_evidence(document, {'document_start':start,'document_end':end,
            'section_id':section['section_id'],'sentence_id':'post-review-source-context','text':text[start:end]},level='other_section')
        verified = verify_evidence_bundle(make_evidence_bundle([raw],['other_section']),document,candidate)
        assert not verified['rejected_items'] and len(verified['items'])==1
        return verified['items'][0]

    unit_pos = text.index('的量纲为1')
    unit_start = text.rfind('\n\n',0,unit_pos)+2
    reynolds = text.index('应是雷诺数',unit_pos)
    unit_end = text.index('\n\n',reynolds)
    unit_evidence = span(unit_start,unit_end)
    factor_start = text.rfind('\n\n',0,text.index('式中第一个组合表示'))+2
    factor_end = text.index('\n\n',text.index('称为空气阻力函数，用',factor_start))
    factor_evidence = span(factor_start,factor_end)
    product_start = text.index('这样，空气阻力加速度的表达式可以简化成')
    explanation = text.index('空气阻力加速度是表示弹丸特征',product_start)
    product_end = text.index('\n\n',explanation)
    product_evidence = span(product_start,product_end)
    extra = [unit_evidence,factor_evidence,product_evidence]
    write(OUT / 'additional_exact_source_evidence.json',extra)
    disposition = [
        {'target':'C_afb01b87134088624d72','issue':'dimensionless not supported by supplied excerpt',
         'ai_disposition':'retain correct technical content and add omitted exact source context',
         'reason':'冻结整章原文明示 C_x0(v/c) 的量纲为1，原14段证据截取没有包含这一句；补证据而不把缺窗口误当成原书未说明。',
         'new_evidence_ids':[unit_evidence['evidence_id']]},
        {'target':'C_ad498a2e8a3eb22fdd25','issue':'unified upper definition overreach',
         'ai_disposition':'narrow wording to this chapter coordinate usage',
         'reason':'采用本章描述运动时所用坐标的限定表达，去除坐标体系及应区分等泛化/指令性措辞；不新增父类公理。'},
        {'target':'R_37918d1714e9945397f3','issue':'boundary of corresponding part unclear',
         'ai_disposition':'clarify the speed contribution while preserving the explicit source word part',
         'reason':'原文及 a_x=C H(y) F(v) 明确区分弹丸特征、大气密度与相对空气速度三部分；不采纳删除部分这一限定的建议，以免扩大为完整运动。',
         'new_evidence_ids':[factor_evidence['evidence_id'],product_evidence['evidence_id']]},
        {'target':'C_88ecbdb51d6e44007aec','issue':'hierarchy deletion disputed across seats',
         'ai_disposition':'defer this operation outside the next narrower draft',
         'reason':'不把来源未证明子类关系解释成来源明确否定该关系，也不把保留既有基线父类当作本次重新批准；从下一批移出，保留独立待核验义务。'}]
    write(OUT / 'objection_disposition.json', {'annotated_by':'ai','human_gold':False,
        'original_valid_votes_modified':False,'old_parent_relationship_approved':False,'items':disposition})
    operations = deepcopy([o for o in original['operations'] if o['target']!='C_88ecbdb51d6e44007aec'])
    for op in operations:
        if op['kind']!='revise_definition':continue
        if op['target']=='C_afb01b87134088624d72':
            op['changes']['definition']='本章空气阻力表达式中的空气阻力系数；C_x0(v/c)的量纲为1，在零攻角近似下以马赫数为自变量，攻角非零时还与攻角有关，本章另讨论雷诺数的影响。'
            op['evidence_ids'].append(unit_evidence['evidence_id'])
        elif op['target']=='C_ad498a2e8a3eb22fdd25':
            op['changes']['definition']='本章描述弹丸质心位置、速度等运动状态时所用的坐标；具体形式包括地面直角坐标、自然坐标及动坐标。'
        elif op['target']=='R_37918d1714e9945397f3':
            op['changes']['definition']='表示本章空气阻力函数 F(v) 表述弹丸相对于空气的运动速度 v 对弹丸运动影响部分的关系；该部分与弹丸特征的弹道系数 C、空气特征的密度函数 H(y) 相区分。'
            op['evidence_ids'].extend([factor_evidence['evidence_id'],product_evidence['evidence_id']])
    assert len(operations)==9 and sum(o['kind']=='revise_constraint' for o in operations)==3
    write(OUT/'proposed_operations.json',operations)
    merged_evidence={e['evidence_id']:e for e in [*evidence,*extra]}
    repo.extension_update(task['extension_id'],task['row_version'],changes={'review_evidence':list(merged_evidence.values())})
    task=repo.extension_status(task['extension_id'])
    receipt=repair_draft(repo,releases,task['extension_id'],expected_row_version=task['row_version'],
        repair_key='source-context-clarified-three-constraint-revision',action='replace',
        reason='按冻结原文补齐量纲和三个组合的上下文、限定坐标定义；本批仅保留三处关系约束修订，层级项另行暂缓。',
        actor='codex_ai_source_checked_revision',evidence_ids=list(merged_evidence),operations=operations,
        resolution={'status':'unapproved_narrowed_source_bound_revision','reuse_schema_ids':sorted({o['target'] for o in operations}),
            'deferred_schema_ids':['C_88ecbdb51d6e44007aec'],'model_calls':0,'old_votes_reusable':False})
    write(OUT/'draft_repair_receipt.json',receipt)
    task=repo.extension_status(task['extension_id'])
    evidence,documents,candidates,cases=load_sources(repo,task)
    config=load_config();voters=_configured_validators(config)
    policy=original['committee_policy'];roster=original['committee_members']
    runtime=OntologyReviewRuntime(repo,releases,voters,None,config)
    runtime.base=load_release(releases/base_id);runtime.documents=documents;runtime.candidates=candidates
    runtime.context={'versions':versions(),'committee_members':roster,'committee_policy':policy,
        'planner_model':None,'planner_family':None,'options_hash':sha256_json(runtime.options),'mode':'shadow',
        'configured_strategy':'operator_prepared','executed_strategy':'source_context_checked_local_revision','independent_approval':False}
    audit=repo.start_ontology_audit(task['extension_id'],runtime.context,'ontology_audit_'+uuid4().hex)
    runtime.budget=CaseBudget({**runtime.options['budget'],'max_llm_calls_per_candidate':3,'max_total_tokens':300000,
        'total_timeout_seconds':600},repo,cases,task_type='ontology_extension',task_id=audit)
    runtime.state={'audit_id':audit,'task':task,'delta':task['delta'],'evidence':evidence,'checks':{},'history':[],
        'validations':{},'validation':None,'fingerprints':{},'critical_issues':[],'full_schema_searched':False,
        'stop_reason':'offline_preparation_no_call_authorization','step_no':0,'prior_counter_evidence_ids':[]}
    for number,(name,execute) in enumerate([
        ('existing_schema',lambda:runtime.tools.search_existing_schema(' '.join(sorted({o['target'] for o in operations})))),
        ('structure',runtime.tools.validate_schema_structure),('impact',runtime.tools.analyze_schema_impact),
        ('competency',runtime.tools.run_competency_checks)],1):
        result=execute();repo.ontology_step(audit,number,'tool',{'tool':name,**result})
    assert runtime.state['checks']['structure']['status']=='pass'
    schema=task['delta']['merged_schema'];guard=OntologyGuard(load_snapshot(schema))
    fixtures=read(SOURCE/'frozen_regression_cases.json')['cases'][:8]
    results=[]
    for case in fixtures:
        observed=guard.check(case['candidate']);expected=case['expected']['B01']
        matched=all(observed[k]==v for k,v in expected.items())
        active=case['target_revision']!='C_88ecbdb51d6e44007aec'
        assert matched or not active
        results.append({'case_id':case['case_id'],'within_new_delta_scope':active,'global_desired_behavior_matched':matched,
            'observed':{k:observed[k] for k in ('status','domain_status','range_status')},'original_expected':expected})
    assert sum(r['within_new_delta_scope'] and r['global_desired_behavior_matched'] for r in results)==6
    assert next(r for r in results if r['case_id']=='STR-04-N')['global_desired_behavior_matched'] is False
    assert catalog(schema)['C_88ecbdb51d6e44007aec']['parents']==catalog(runtime.base.ontology)['C_88ecbdb51d6e44007aec']['parents']
    write(OUT/'regression_results.json',{'active_scope_cases':6,'active_scope_matches':6,'all_original_structural_cases':8,
        'global_desired_matches':sum(r['global_desired_behavior_matched'] for r in results),'held_hierarchy_cases':2,
        'negative_hierarchy_target_still_unresolved':True,'original_expectations_changed':False,'results':results})
    reg={'status':'pass','scope':'six_active_three_relation_structural_cases_only','delta_hash':task['delta']['delta_hash'],
        'results':[r for r in results if r['within_new_delta_scope']],'deferred_hierarchy_target_not_claimed_fixed':True,
        'independent_semantic_approval':False,'annotated_by':'ai'}
    reg['record_id']='check_'+sha256_json(reg);runtime.state['checks']['regression']=reg
    runtime.budget.usage['validation_rounds']=1
    proposal=runtime.freeze_proposal(runtime.state)
    proposal.update(semantic_context_delivery=original['semantic_context_delivery'],semantic_coverage_mode=original['semantic_coverage_mode'])
    proposal['proposal_digest']=proposal_digest(proposal);repo.ontology_proposal(proposal);runtime.checkpoint('ready')
    request,_=semantic_competency.request_for(repo,releases,proposal)
    write(OUT/'prepared_proposal.json',proposal)
    write(OUT/'prepared_validator_payload.json',validator_payload(proposal,delivery_version='ontology_validator_payload_v3'))
    write(OUT/'prepared_competency_request.json',request)
    write(OUT/'owl_patch.json',proposal['source_patch'])
    (OUT/'UNAPPROVED_ontology.ttl').write_text(proposal['source_patch']['merged_rdf'],encoding='utf-8')
    accounts=[repo.case_status(c) for c in cases]
    budget_requirements={'new_model_calls_authorized':0,'cumulative_calls_used':16,
        'case_accounts':accounts,'future_review_token_envelope':300000,
        'token_limit_additions_needed_for_that_envelope':{a['case_id']:max(0,a['usage']['tokens']+300000-a['limits']['tokens']) for a in accounts},
        'budget_limit_changed_in_this_preparation':False,'previous_votes_reusable':False}
    write(OUT/'future_review_budget_requirements.json',budget_requirements)
    source_codes={str(p.relative_to(ROOT)):digest(p) for folder in ('ontology_review','ontologyBuild','review_agent','evidence','document','schema','prompts')
        for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.json','.txt'}}
    manifest={'at':datetime.now(timezone.utc).isoformat(),'database':str(Path(repo.database_path).resolve()),'release_root':str(releases),
        'proposal_digest':proposal['proposal_digest'],'source_reviewed_digest':original['proposal_digest'],
        'extension_id':task['extension_id'],'audit_id':audit,'object_revision':proposal['object_revision'],'round_id':proposal['round_id'],
        'operations':len(operations),'constraint_operations':3,'definition_operations':6,'evidence_spans':len(evidence),
        'local_structure':'pass','active_regression_matches':6,'active_regression_cases':6,'held_global_regression_target':'STR-04-N',
        'semantic_competency_executed':False,'modeling_votes':0,'additional_model_calls_authorized':0,
        'new_model_calls':0,'cumulative_model_calls':16,'old_valid_votes_unchanged':True,'published':False,'production_written':False,
        'code_fingerprints':source_codes,'preparation_script_sha256':digest(Path(__file__))}
    assert current_release_id(releases)==current_release_id(ROOT/'runs/ontology_releases')==base_id
    assert all(digest(Path(path))==value for path,value in protected.items())
    write(OUT/'prepared_manifest.json',manifest)
    print(json.dumps({k:manifest[k] for k in ('operations','constraint_operations','definition_operations','evidence_spans','active_regression_matches','held_global_regression_target','new_model_calls','cumulative_model_calls')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
