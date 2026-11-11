"""Four bounded follow-ups using frozen returns and source material; no model calls or publication."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from contextlib import ExitStack
import hashlib
import json
import re
from pathlib import Path
from unittest.mock import patch
from document.hierarchy_index import load_document_index
from evaluation.review_samples import ReadOnlyRepository
from evaluation.review_quality import annotation_template,quality_report
from review_agent.consensus import aggregate_votes
from review_agent.contracts import sha256_json
from review_agent.review_observability import assess_vote,outcome,redact
from review_agent.response_corrections import apply_repairs
from review_agent.schema_validation import schema_errors
from review_agent.validator_contracts import contract_for
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.mapping_preflight import definitions
from ontologyBuild.release_service import load_release
from schema.relation_registry import RelationRegistry
from runs.revalidate_v8 import committee,digest,save,read

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'outputs/dev30_v8_full_20261110_0913'
DERIVED=ROOT/'outputs/dev30_v8_revalidated_20261110_final'
FIRST=('DEV-004','DEV-007','DEV-008','DEV-014','DEV-015')
QUEUE_NAMES={'missing_valid_seats':'缺少合格席位','evidence_role_clarification':'证据角色/维度引用核对',
    'substantive_disagreement':'具体含义或范围争议','mapping_blocked':'正式映射待处理','revision_required':'修订核验',
    'source_evidence_needed':'原文待补','scope_review':'范围待确认','scope_exclusion':'范围排除','manual_final_check':'最终核对'}


def markdown(out,name,lines):
    target=out/name;target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text('\n'.join(lines)+'\n',encoding='utf-8')


def material_rank(packets):
    rows=[]
    for p in packets:
        t=p['candidate']['triple'];classes={c['id']:c for c in p['ontology_context']['classes']}
        criteria={'primary_source_present':bool(p['candidate']['evidence_items']),
            'all_bundle_anchors_verified':all(e.get('anchor_valid') is True for e in p['evidence_bundle']['items']),
            'both_identities_and_types_available':all(not e['missing_information'] for e in p['mapping_preflight']['entities'].values()),
            'both_type_definitions_available':all(bool(definitions(classes.get(t.get(k),{}))) for k in ('head_type','tail_type')),
            'formal_relation_id_assigned':bool(t.get('relation_id')),
            'structural_constraints_checkable':p['ontology_check'].get('structural_status')=='pass'}
        rows.append({'sample_id':p['sample_id'],'criteria':criteria,'score':sum(criteria.values())})
    rows.sort(key=lambda x:(-x['score'],x['sample_id']))
    assert tuple(r['sample_id'] for r in rows[:5])==FIRST
    return {'rule':'六项材料可核对条件等权；并列按原样本编号升序，不按模型结论或通过可能性选择',
        'selection_does_not_imply_all_admission_conditions_complete':True,'rows':rows}


def proposal_for(repo,case):
    audit=repo.get_audit(case['candidate_id'],case['audit_id']);decision=repo.latest_decision(case['candidate_id']) or {}
    p=next(p for p in audit['rounds'] if p['proposal_digest']==decision['selected_proposal_digest'])
    votes=[v for v in audit['votes'] if v['proposal_digest']==p['proposal_digest']]
    return audit,decision,p,votes


def classify(o):
    d=o['dimension_results'];missing=o['whole_vote_eligible_voter_ids']
    if len(missing)<d['scope']['required_seats']:
        return 'missing_valid_seats','补齐已失败席位的实际契约问题；晚到合格结果优先，不重审已有合格席位'
    conflicts=[a for a in ('source','expression','scope') if d[a]['has_conflict']]
    if conflicts:return 'substantive_disagreement','仅复核 '+','.join(conflicts)+' 的具体争议及对应原文，保留反对意见'
    if d['expression']['status'] in {'semantic_revision','lossless_normalization'} or d['source']['status']=='contradicted':
        return 'revision_required','先提出保留来源的修订草案，再独立核验新版本'
    if d['scope']['status']=='exclude':return 'scope_exclusion','确认派生的范围排除依据，事实真假另记'
    if any(d[a]['valid_seats']<d[a]['required_seats'] for a in ('source','expression')):
        return 'evidence_role_clarification','核对正面证据集合、证据角色和维度引用；不是增加一个新审核席位'
    if d['source']['status']=='insufficient':return 'source_evidence_needed','补充当前缺失的原文，不能将未发现反证当正面支持'
    if d['scope']['status']=='unclear':return 'scope_review','按现有范围规则核对类别及判定条件'
    if d['mapping']['status']!='valid':return 'mapping_blocked','补充绑定版本的关系定义/身份/约束，保持事实表达'
    return 'manual_final_check','核对剩余关键阻塞；不按票数直接准入'


def analyze_calls(repo,cases):
    with repo.connect() as c:
        attempts={r['attempt_id']:{**dict(r),'metadata':json.loads(r['metadata_json']),'result':json.loads(r['result_json'] or '{}')}
            for r in c.execute('SELECT * FROM request_attempts')}
    patches=[];timeouts=[];before_full=[];after_full=[];selected_ok_patch=set();selected_patch=set()
    for case in cases:
        a,decision,p,votes=proposal_for(repo,case);baseline=deepcopy(votes)
        for i,v in enumerate(votes):
            attempt=attempts.get(v.get('attempt_id'))
            if v.get('response_status')=='timeout':
                assert attempt
                r=attempt['result'];phase=(attempt['metadata'].get('feedback') or {}).get('kind') or 'initial'
                timeouts.append({'sample_id':case['sample_id'],'voter_id':v['voter_id'],'model_id':v['model_id'],
                    'phase':phase,'attempt_id':v['attempt_id'],'caller_wait_ms':v['latency_ms'],
                    'actual_response_status':r.get('response_status'),'actual_latency_ms':r.get('latency_ms'),
                    'finish_reason':r.get('finish_reason'),'late_response':r.get('late_response'),
                    'local_dispatch_queue_ms':r.get('queue_time_ms'),'input_tokens':r.get('input_tokens'),'output_tokens':r.get('output_tokens'),
                    'ttft_ms':None,'provider_queue_ms':None,
                    'timing_limit':'local dispatch queue is not provider/GPU queue; TTFT and streaming gaps were not recorded',
                    'original_selected_vote_hash':sha256_json(v),'raw_reference':str(SOURCE/'review.sqlite3')+'#request_attempts/'+v['attempt_id']})
            if not attempt or (attempt['metadata'].get('feedback') or {}).get('kind')!='field_patch':continue
            selected_patch.add(attempt['attempt_id'])
            if assess_vote(v,p)['whole_vote_eligible']:selected_ok_patch.add(attempt['attempt_id'])
            original_id=attempt['metadata']['feedback']['original_attempt_id'];baseline[i]=deepcopy(attempts[original_id]['result'])
        prior=aggregate_votes(baseline,committee(p),proposal=p,proposal_digest=p['proposal_digest'])
        post=aggregate_votes(votes,committee(p),proposal=p,proposal_digest=p['proposal_digest'])
        if len(prior['eligible_voter_ids'])==3:before_full.append(case['sample_id'])
        if len(post['eligible_voter_ids'])==3:after_full.append(case['sample_id'])
    for ident,attempt in attempts.items():
        m=attempt['metadata'];feedback=m.get('feedback') or {}
        if feedback.get('kind')!='field_patch':continue
        r=attempt['result'];p=m['proposal'];plan=feedback['repair_plan'];original=feedback['original_response']
        contract=contract_for(p['response_contract_version']);merged=None;patch_error=None
        try:
            from review_agent.validators import _parse_content
            merged=apply_repairs(original,plan,_parse_content(r.get('raw_correction_response')))
        except (ValueError,TypeError,KeyError) as exc:patch_error=str(exc)
        exact=merged is not None and merged==r.get('corrected_response')
        judged_unchanged=exact and all(merged['dimensions'][k].get('status')==v.get('status') for k,v in original['dimensions'].items())
        judged_unchanged=judged_unchanged and original.get('expression_findings')==merged.get('expression_findings')
        judged_unchanged=judged_unchanged and original.get('evidence_assessments')==merged.get('evidence_assessments')
        unapplied=not r.get('raw_correction_response') and not r.get('corrected_response')
        assessment=assess_vote(r,p)
        patches.append({'attempt_id':ident,'sample_id':next(c['sample_id'] for c in cases if c['candidate_id']==p['candidate']['candidate_id']),
            'voter_id':m['binding']['voter_id'],'original_attempt_id':feedback['original_attempt_id'],
            'original_schema_errors':schema_errors(original,contract.RESPONSE_SCHEMA),
            'original_contract_errors':contract.validate_response(original),
            'targets':[t['path'] for t in plan['targets']],'merge_verified':exact,'judgments_and_roles_preserved':bool(judged_unchanged) if not unapplied else None,
            'unapplied_failed_patch':unapplied,'failure_reason':r.get('error'),
            'after_status':r.get('response_status'),'after_contract_errors':contract.validate_response(merged) if merged else [],
            'whole_vote_eligible':assessment['whole_vote_eligible'],'axis_usable':{k:v['usable'] for k,v in assessment['axis_observations'].items()},
            'selected_as_final':ident in selected_patch,'selected_eligible':ident in selected_ok_patch,
            'late_response':bool(r.get('late_response')),'input_tokens':r.get('input_tokens'),'output_tokens':r.get('output_tokens'),
            'latency_ms':r.get('latency_ms'),'patch_error':patch_error})
    assert len(timeouts)==9 and len(patches)==42
    assert all((x['merge_verified'] and x['judgments_and_roles_preserved']) or (x['unapplied_failed_patch'] and not x['whole_vote_eligible']) for x in patches)
    total_tokens=sum((a['result'].get('input_tokens') or 0)+(a['result'].get('output_tokens') or 0) for a in attempts.values())
    patch_tokens=sum((p['input_tokens'] or 0)+(p['output_tokens'] or 0) for p in patches)
    roi={'attempts':len(patches),'contract_valid_receipts':sum(p['whole_vote_eligible'] for p in patches),
        'selected_eligible_patch_seats':len(selected_ok_patch),'failed_after_patch':sum(not p['whole_vote_eligible'] for p in patches),
        'target_counts':dict(Counter(t for p in patches for t in p['targets'])),
        'merged_patches':sum(p['merge_verified'] for p in patches),'unapplied_failed_patches':sum(p['unapplied_failed_patch'] for p in patches),
        'all_applied_judgments_and_roles_preserved':True,
        'full_packets_without_patches':before_full,'full_packets_with_selected_patches':after_full,
        'additional_full_packets':sorted(set(after_full)-set(before_full)),
        'input_tokens':sum(p['input_tokens'] or 0 for p in patches),'output_tokens':sum(p['output_tokens'] or 0 for p in patches),
        'patch_share_of_committee_reported_tokens':patch_tokens/total_tokens,'currency_cost':None,
        'claim':'engineering contract recovery only; not factual accuracy or revision success'}
    return {'timeout_seats':timeouts,'timeout_counts':dict(Counter(x['voter_id']+':'+x['phase'] for x in timeouts)),
        'erratum':{'previous_claim':'Gemma has 9 waiting timeouts','actual_total':9,'actual_gemma':sum(x['voter_id']=='judge_b' for x in timeouts),
            'actual_qwen':sum(x['voter_id']=='judge_a' for x in timeouts)},'patches':patches,'patch_roi':roi}


def manual_queue(repo,cases):
    rows=[]
    for case in cases:
        if case['outcome']['recommendation']!='manual_review':continue
        audit,decision,p,votes=proposal_for(repo,case);recovered=deepcopy(votes);used=[]
        for i,v in enumerate(votes):
            if v.get('response_status')!='timeout' or not v.get('attempt_id'):continue
            receipt=repo.request_attempt(v['attempt_id']);r=receipt.get('result') or {}
            if receipt['state']=='completed' and r.get('late_response') and assess_vote(r,p)['whole_vote_eligible']:
                recovered[i]=deepcopy(r);used.append({'voter_id':v['voter_id'],'attempt_id':v['attempt_id'],'receipt_hash':sha256_json(r)})
        consensus=aggregate_votes(recovered,committee(p),proposal=p,proposal_digest=p['proposal_digest'])
        derived=outcome(p,recovered,committee(p),audit_status=audit['status'],
            context={**audit['context'],'final_gaps':p.get('gaps') or {}},decision={**decision,'consensus':consensus})
        category,action=classify(derived)
        rows.append({'sample_id':case['sample_id'],'candidate_id':case['candidate_id'],'revision':p['candidate']['revision'],
            'audit_id':audit['audit_id'],'proposal_digest':p['proposal_digest'],'evidence_hash':p['evidence_bundle']['evidence_hash'],
            'original_recommendation':case['outcome']['recommendation'],'original_eligible_voters':case['outcome']['whole_vote_eligible_voter_ids'],
            'late_receipts_used':used,'derived_recommendation':derived['recommendation'],
            'derived_eligible_voters':derived['whole_vote_eligible_voter_ids'],'dimensions':derived['dimension_results'],
            'primary_queue':category,'next_action':action,'blocking_reasons':derived['blocking_reasons'],
            'disputes':consensus.get('dispute_report'),'historical_issues_not_silently_cleared':audit['context'].get('issue_history',[]),
            'original_decision_changed':False,'formal_commit_performed':False,'model_calls':0})
    assert len(rows)==24
    return {'population':24,'primary_queue_counts':dict(Counter(r['primary_queue'] for r in rows)),
        'derived_recommendation_counts':dict(Counter(r['derived_recommendation'] for r in rows)),
        'interpretation':'read-only formal proposal for the frozen object; pending operator handling, not a production decision',
        'cases':rows}


def mapping_pilot(repo,packets):
    packet=next(p for p in packets if p['sample_id']=='DEV-008');candidate=packet['candidate'];relation_id=candidate['triple']['relation_id']
    release=load_release(candidate['ontology_release_path']);registry=RelationRegistry(release.ontology)
    bound=deepcopy(registry.properties[relation_id]);prod=ReadOnlyRepository(ROOT/'runs/v3/review.sqlite3')
    with prod.connect() as c:
        row=c.execute('SELECT task_json,status FROM ontology_extension_tasks WHERE extension_id=?',('ext_4272d42b83144e8c8da86d3530f76019',)).fetchone()
        task=json.loads(row['task_json']);task_status=row['status']
        review_votes=[json.loads(r['vote_json']) for r in c.execute('SELECT vote_json FROM ontology_votes WHERE audit_id=?',(task['latest_audit_id'],))]
    current_votes=[v for v in review_votes if v['proposal_digest']==task['selected_proposal_digest']]
    assert len(current_votes)==3 and all(v['delta_hash']==task['delta']['delta_hash'] and v['object_revision']==task['delta_revision'] for v in current_votes)
    publication_manifest=next(v for v in read(ROOT/'runs/ontology_publication_20261031/unapproved_owl_draft_manifest.json') if v['extension_id']==task['extension_id'])
    assert publication_manifest['delta_hash']==task['delta']['delta_hash'] and publication_manifest['published'] is False
    competency=read(ROOT/'runs/ontology_publication_20261031/crosswind_definition_competency_known_seat_status.json')
    shadow_schema=deepcopy(task['delta']['merged_schema']);shadow_relation=RelationRegistry(shadow_schema).properties[relation_id]
    guard=OntologyGuard(load_snapshot(release.ontology));guard.require_explicit_mapping=True;guard.require_relation_definition=True
    draft_guard=OntologyGuard(load_snapshot(shadow_schema));draft_guard.require_explicit_mapping=True;draft_guard.require_relation_definition=True
    before=guard.check(candidate);preview=draft_guard.check(candidate['triple'])
    # This is a metadata counterfactual only: no new release ID/binding is invented.
    assert not definitions(bound) and definitions(shadow_relation)
    assert candidate['triple']['surface_relation']=='通过改变空气阻力的方向来影响'
    assert before['status']=='unknown' and preview['status']=='pass'
    assert before['domain_status']==preview['domain_status']=='pass' and before['range_status']==preview['range_status']=='pass'
    release_repo=ROOT/'runs/ontology_releases'
    published=[p.name for p in release_repo.iterdir() if p.is_dir() and (p/'release_manifest.json').exists()]
    source_ids=[e['evidence_id'] for e in packet['evidence_bundle']['items']]
    operation=next(o for o in task['delta']['operations'] if o['target']==relation_id)
    assert set(operation['evidence_ids'])<=set(source_ids)
    return {'sample_id':'DEV-008','candidate_id':candidate['candidate_id'],'revision':candidate['revision'],
        'candidate_hash':sha256_json(candidate),'evidence_hash':packet['binding']['evidence_hash'],
        'bound_release_id':candidate['ontology_release_id'],'published_release_ids':published,
        'original_expression':deepcopy(candidate['triple']),'relation_id':relation_id,
        'bound_property':bound,'entity_preflight':packet['mapping_preflight']['entities'],
        'frozen_retrieval_trace':packet['mapping_preflight']['search_trace'],'bound_guard':before,
        'existing_definition_draft':{'extension_id':task['extension_id'],'status':task_status,'delta_revision':task['delta_revision'],
            'delta_hash':task['delta']['delta_hash'],'definition_operation':operation,'published':False,
            'selected_ontology_proposal_digest':task['selected_proposal_digest'],'audit_id':task['latest_audit_id'],
            'review_votes':[{k:v.get(k) for k in ('voter_id','response_status','decision','checks','suggested_changes','requested_context','semantic_status','structure_status')} for v in current_votes],
            'publication_blocker':publication_manifest['publication_blocker'],
            'historical_semantic_competency_snapshot':competency,
            'manifest_reference':str(ROOT/'runs/ontology_publication_20261031/unapproved_owl_draft_manifest.json')},
        'shadow_metadata_guard':preview,'shadow_is_approved_mapping':False,
        'stage_result':{'retrieval':'completed','relation_identity':'existing_ID_found','entity_identity':'document_local_types_and_source_available',
            'bound_definition':'missing','unpublished_draft_definition':'found','shadow_definition_and_constraints':'pass',
            'ontology_approval':'not_satisfied','publication':'not_performed','candidate_rebinding':'not_performed','new_semantic_mapping_review':'not_performed'},
        'actual_blocker':'existing sourced definition is still an unapproved ontology draft; the bound published release has no definition',
        'next_steps':['核对该既有定义草案尚未满足的独立本体审核/语义能力检查','批准并发布后由现有版本迁移流程重新绑定候选','以新绑定独立核验关系语义与约束，不复用旧票'],
        'mapping_success_claimed':False,'global_ontology_gap_claimed':False,'model_calls':0,'production_written':False}


def annotations(repo,packets,out):
    (out/'annotation_first5').mkdir(exist_ok=True)
    save(out,'annotation_first5/material_ranking.json',material_rank(packets))
    selected=[deepcopy(next(p for p in packets if p['sample_id']==sid)) for sid in FIRST]
    run=repo.get_run(selected[0]['candidate']['run_id']);doc=load_document_index(run['document_path'],selected[0]['candidate']['source_index_hash'],require_verified=True)
    canonical=doc['source_snapshot']['canonical_text'];selection=[]
    for packet in selected:
        packet['system']={};c=packet['candidate'];t=c['triple'];pre=packet['mapping_preflight'];context=packet['ontology_context']
        assert all(not e['missing_information'] for e in pre['entities'].values())
        assert packet['ontology_check']['structural_status']=='pass'
        classes=[x for x in context['classes'] if x['id'] in {t['head_type'],t['tail_type']}]
        assert len(classes)==2 and all(definitions(x) for x in classes)
        assert all(canonical[e['start']:e['end']]==e['quote'] for e in c['evidence_items'])
        selection.append({'sample_id':packet['sample_id'],'packet_id':packet['packet_id'],'revision':c['revision'],
            'reason':'任务已结束；原文锚点核验通过；两端身份/类型及其定义齐全；现有关系ID及结构约束可核对',
            'remaining_material_limit':'正式关系定义尚未批准；允许映射标为上下文不足，不强制给真/假'})
        lines=[f"# {packet['sample_id']} 当前版本盲标材料",'',f"候选版本：{c['revision']}；材料ID：{packet['packet_id']}",
            '',f"**{t['head']} — {t['surface_relation']} — {t['tail']}**",'',
            '限定信息：`'+json.dumps(t['qualifiers'],ensure_ascii=False)+'`','限定来源状态：`'+json.dumps(t['qualifier_status'],ensure_ascii=False)+'`',
            '',f"正式关系ID：{t.get('relation_id')}；头类型：{t['head_type']}；尾类型：{t['tail_type']}",
            '本页不展示模型投票或系统推荐。先按原文及任务规则独立填写；关系定义缺失允许标不可判断。','',
            '## 原文证据与位置','']
        for e in packet['evidence_bundle']['items']:
            lines += [f"### {e['evidence_id']}",f"位置：{e.get('start')}–{e.get('end')}；章节：{e.get('section_id')}；锚点：{e.get('anchor_valid')}",'',
                '> '+re.sub(r'!\[[^\]]*\]\([^)]*\)','[原文图片引用；未自动加载]',str(e.get('quote') or '')).replace('\n','\n> '),'']
        lines += ['## 主证据相邻语境','']
        for e in c['evidence_items']:
            excerpt=canonical[max(0,e['start']-280):min(len(canonical),e['end']+360)]
            excerpt=re.sub(r'!\[[^\]]*\]\([^)]*\)','[原文图片引用；未自动加载]',excerpt)
            lines += [f"{e['evidence_id']} 相邻语境（源文档偏移范围保留在完整材料JSON）：",'','```text',excerpt,'```','']
        lines += ['## 实体类型与正式关系材料','']
        for x in classes:lines += [f"- {x['id']} {x['label']}：{'；'.join(definitions(x))}"]
        props=[x for x in context['properties'] if x['id']==t['relation_id']]
        for x in props:lines += ['','```json',json.dumps(x,ensure_ascii=False,indent=2),'```']
        lines += ['','## 任务范围规则','',json.dumps(packet['scope_policy'],ensure_ascii=False,indent=2),
            '', '## 待人工填写','', '|维度|判断|证据ID与依据|','|---|---|---|',
            '|原文支持（支持/矛盾/不足/待定）|待填|待填|','|表达（规范/无损规范化/语义修订/待定）|待填|待填|',
            '|本体映射（有效/冲突/未分配/上下文不足）|待填|待填|','|范围（纳入/排除/待定）|待填|待填|',
            '', '是否需要事实/表达修订：待填。等待定义或正式映射不自动记为事实修订。',
            '是否满足全部准入条件：待填，可为不可判断。标注者/时间/是否看过模型意见：待填。',
            '确认后填入 annotations.json；模板保持 pending，AI预审不能作为人工标签。']
        target=out/'annotation_first5'/(packet['sample_id']+'.md');target.parent.mkdir(exist_ok=True)
        target.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    quality_report(selected)  # Verify exact packet material hashes after removing model conclusions.
    save(out,'annotation_first5/selection.json',selection)
    save(out,'annotation_first5/blind_packets.json',{'samples':selected,'model_opinions_included':False})
    templates=[dict(annotation_template(p),sample_id=p['sample_id']) for p in selected]
    save(out,'annotation_first5/annotations.json',{'instruction':'人工独立重标；未知可留空，确认后再设 resolved；AI预审不作真值','annotations':templates})
    return selection


def readable_reports(out,calls,queue,pilot,selection,packets):
    roi=calls['patch_roi'];q=queue['primary_queue_counts'];draft=pilot['existing_definition_draft']
    markdown(out,'report.md',[
        '# 2026-11-10 四项收敛分析','',
        '本轮只读核对冻结的30条真实返回及现有本体草案，新增模型调用0次。原票、原决定、生产数据库和发布版本均未改写。',
        '', '## 调用失败与契约失败','',
        '**更正旧报告：9个是全部超时席位，不是9个Gemma。** Gemma初审7次触及180秒、字段补丁1次触及60秒；Qwen初审1次触及90秒。',
        '9席晚到后：3席合格、5席仍违反语义契约、1席输出截断。Gemma初审的实际耗时197.5～664.5秒；其中2席最终合格。',
        '仅改成流式不能修复矛盾判断或输出截断。此次日志没有首字耗时、流间隔和提供方队列数据，不能将本地dispatch等待解释成GPU排队，也不能证明最早的64秒失败由哪个网关环节触发。',
        '', '|字段补丁口径|结果|','|---|---|',
        f"|尝试数|{roi['attempts']}|",f"|合并可重现、原判断/证据角色保留|{roi['merged_patches']}|",f"|没有可应用补丁|{roi['unapplied_failed_patches']}|",
        f"|最终契约合格票|{roi['contract_valid_receipts']}（含1张晚到）|",f"|原窗口选中合格补丁票|{roi['selected_eligible_patch_seats']}|",f"|补丁后仍失败|{roi['failed_after_patch']}|",
        '|同批返回去掉字段补丁的三席完整提案|0|','|保留原窗口选定补丁的三席完整提案|14|',
        f"|输入/输出token|{roi['input_tokens']:,} / {roi['output_tokens']:,}|",
        f"|占委员请求报告token|{roi['patch_share_of_committee_reported_tokens']:.2%}（不含规划请求）|",'',
        '42次均修复 `/dimensions/counterevidence/evidence_ids`：确有工程收益，但集中暴露反证检索结果与正面支持证据集合的契约填写问题。无补丁对照只对同一组已保存返回做反事实重聚合，不是第二轮随机实验。',
        '6次失败中，2次补丁正文为空/不可解析；4次虽补齐引用，仍有 finding 与检查状态不一致等真实语义契约错误。不能靠字段补丁把这些票变成通过。货币费用没有可靠账单，本报告仅列token。',
        '', '## 一条完整映射路径','',
        '选择 DEV-008。原文与两端身份可核对，现有正式关系ID能找到，domain/range均通过；绑定发布版缺的是关系定义。',
        f"已找到历史定义草案 `{draft['extension_id']}` 的第{draft['delta_revision']}版，并非确认本体没有这个关系。该草案尚未批准发布。",
        '其本体审核三席已到齐，意见为 Qwen revise、Gemma approve、DeepSeek approve；Qwen分项为pass/不适用且没有具体修改建议，与总决定仍需澄清，不能自动改票。既有语义能力检查另有提供方结果不明及契约失败。',
        '内存中使用现有草案定义后，元数据与类型约束检查从unknown变pass。这只定位阻塞，不等于正式映射成功；发布、候选重绑定、新绑定下的语义审核仍未完成。完整路径与版本证据见 [映射试点](mapping_pilot_DEV008.md)。',
        '', '## 原24条人工任务分流','',
        '|首要处理队列|数量|','|---|---|',
        *[f'|{QUEUE_NAMES[k]}|{n}|' for k,n in q.items()],
        '', '分流是下一步工作顺序，所有并存阻塞仍保留。缺少合格席位13条不等于13条超时；其中还包括契约冲突和截断。当前无可直接执行的已确认修订队列，不能把人工任务统称为修订。',
        'DEV-018、DEV-028、DEV-030的真实晚到合格票经原提案/版本/证据绑定严格重聚合：018和030仍需核对正面证据角色；028的派生建议从人工变为等待映射，同时仍保留历史关键问题和映射分歧。',
        '全30条的晚到派生建议为23人工、6等待映射、1范围排除，正式有效映射和准入仍为0。原运行仍是24/5/1及14份三席完整；晚到对账后17份完整不能算作原窗口内完成。详见 [24条清单](manual_queue_24.md)。',
        '', '## 首批人工重标','',
        '按6项材料可核对条件等权、并列按编号选择 DEV-004、007、008、014、015，未按模型结论筛选。5条均有核验锚点、两端身份/类型定义、关系ID和可检查的结构约束；正式关系定义仍缺失，标注允许不可判断。',
        '盲标材料与AI预审分开；当前人工确认0/5，所有标签保持pending，质量指标不填0%或100%。入口：[首批5条盲标说明](annotation_first5/README.md)。',
        '', '## 下一步','',
        '先完成这5条人工四维标签，形成第一版可核对的错误分析，再扩展到余下25条；按队列处理证据引用、具体争议和失败席位。映射试点先澄清现有定义草案的审核义务，再走已有发布、重绑定和语义核验流程。',
        '', '原始结构化证据：[调用与补丁](call_and_contract_analysis.json)、[24条派生决定](manual_queue_24.json)、[映射路径](mapping_pilot_DEV008.json)、[只读来源哈希](manifest.json)。',
        '', '复现：在项目根目录执行 `python -X utf8 -B -m runs.focus_v8_followup_20261110 --output outputs/<全新目录>`；入口禁止新模型调用。'])
    lines=['# 原24条人工任务的只读分流','',
        '以原人工任务为固定总体。只替换同提案、同版本的已完成晚到合格票，不补造席位、不清除历史问题、不改写正式决定。多种阻塞可以并存；主队列只指定先处理什么。',
        '', '|样本/版本|原合格席位→晚到后|晚到票|首要队列|派生建议|','|---|---|---|---|---|']
    for r in queue['cases']:
        lines.append(f"|{r['sample_id']} / v{r['revision']}|{len(r['original_eligible_voters'])}→{len(r['derived_eligible_voters'])}|{','.join(v['voter_id'] for v in r['late_receipts_used']) or '无'}|{QUEUE_NAMES[r['primary_queue']]}|{r['derived_recommendation']}|")
    for key,n in q.items():
        members=[r for r in queue['cases'] if r['primary_queue']==key]
        lines += ['',f'## {QUEUE_NAMES[key]}（{n}条）','',members[0]['next_action'],'']
        for r in members:
            lines += [f"- {r['sample_id']}："+'；'.join(f"{a}={d['status']}（有效{d['valid_seats']}/{d['required_seats']}，分歧{d['has_conflict']}）" for a,d in r['dimensions'].items())+'。',
                '  并存阻塞：`'+'`、`'.join(r['blocking_reasons'])+'`。']
    lines += ['','DEV-028虽然派生建议为wait_mapping，仍有映射分歧与历史关键问题，不能视为已获准入。',
        '修订队列当前没有已确认、可直接执行的条目；需先解决缺票/争议并形成具体方案，再按新版本重新验证。',
        '逐条proposal_digest、evidence_hash、晚到receipt_hash及保留问题见同名JSON。']
    markdown(out,'manual_queue_24.md',lines)
    markdown(out,'mapping_pilot_DEV008.md',[
        '# DEV-008 映射路径核对','',
        f"候选 `{pilot['candidate_id']}`，版本{pilot['revision']}；正式关系 `{pilot['relation_id']}`。原始关系短语保留，未替换为另一个含义。",'',
        '|阶段|现有证据|结论|','|---|---|---|',
        '|原文及两端身份|冻结证据包、文档内身份ID、类型定义|可核对|',
        '|检索|当前子集8个属性；扩大到绑定发布版189类、81属性及别名|已执行，词法召回不等于语义确认|',
        '|正式关系身份|R_1654fcf38913d94cb712 已存在|不是必须新增关系|',
        '|约束|头尾类型分别满足既有domain/range|pass|',
        '|已发布定义|绑定发布版description/definitions为空|guard=unknown，RELATION_DEFINITION_UNAVAILABLE|',
        '|现有定义草案|原文有出处，delta第2版；仅增加定义注解|未发布|',
        '|独立本体审核|三席到齐：revise / approve / approve|未满足批准门槛|',
        '|语义能力检查历史记录|B有效；A提供方结果未知；C契约失败|unknown，需先对账，不能盲重试|',
        '|内存元数据对照|给同一关系加载现有草案定义，类型约束不变|guard=pass；不代表批准|',
        '|发布→重绑定→新绑定语义审核|均未执行|正式映射仍未完成|',
        '',f"本体审核ID：`{draft['audit_id']}`；选定提案：`{draft['selected_ontology_proposal_digest']}`；delta_hash：`{draft['delta_hash']}`。",
        f"发布清单明确阻塞：`{draft['publication_blocker']}`。",
        'Qwen的revise票分项为pass或不适用，suggested_changes和requested_context均为空。这是应定向询问的总决定/理由一致性问题；不能凭其余两票自动把revise改成approve。',
        '', '本轮结论：完整走查到“既有草案的独立批准及发布”这个直接阻塞点，未声称成功入库。先对账能力检查的未知请求，再按现有预算仅澄清未解决的本体义务。批准发布后，须重新绑定候选并审核关系语义与约束；旧候选票不能直接转给新绑定。',
        '内存对照使用裸三元组检查元数据，没有生成合法的新release绑定，所以不构成版本迁移或正式映射验证。',
        '', '完整冻结关系、实体、检索轨迹、票摘要、能力检查记录见 [结构化证据](mapping_pilot_DEV008.json)。'])
    fields=packets[0]['field_spec']
    markdown(out,'annotation_first5/README.md',[
        '# 首批5条当前版本盲标','',
        '先只阅读本页、各样本页及blind_packets.json，不打开上一层报告或ai_pre_review.md。原始包的system已清空，不含模型意见；证据、规则、本体上下文和材料哈希保留。',
        '按材料完整度选择，完整定义仍可能缺失，允许部分维度不可判断。此批属于开发回归集，不能据此评价独立泛化效果。',
        '', '|样本|候选版本|材料|','|---|---|---|',
        *[f"|{r['sample_id']}|{r['revision']}|[{r['sample_id']}.md]({r['sample_id']}.md)|" for r in selection],
        '', '## 填写方法','',
        '1. 独立核对原文、指代、限定词、实体类型、正式关系和范围规则。原文引用中的图片未自动加载；不能将图像未核验内容当成已支持。',
        '2. 在annotations.json填写标注者、时间、证据ID、理由、四维判断，以及是否看过模型结论。未知可填null，不能猜测。',
        '3. 人工确认完成且完整材料已读后，才把status改为resolved、full_packet_reviewed改为true；当前全部pending。',
        '', '|维度|JSON允许值|','|---|---|',
        '|source|supported / contradicted / insufficient / unresolved / null|',
        '|expression|standard / lossless_normalization / semantic_revision / unresolved / null|',
        '|mapping|valid / conflict / unassigned / context_incomplete / null|',
        '|scope|include / exclude / unclear / null|',
        '', 'admission_eligible、needs_revision、mapping_correct填写true/false/null。准入为true必须同时source=supported、expression=standard、mapping=valid、scope=include。',
        'needs_revision只指候选事实/表达是否需改动；等待正式定义、映射或范围排除不自动等于需要事实修订。mapping_correct只评价实际形成的映射，材料不足可为null。',
        '证据和候选等绑定字段不要编辑；有材料更新应另生成版本和标注包，旧标签不能直接迁移。',
        '', '## 当前字段规范（原始冻结内容）','', '```json',json.dumps(fields,ensure_ascii=False,indent=2),'```','',
        '范围规则已附在各样本页；全量材料见blind_packets.json。AI预审另存，待独立标签提交后用于比对，不作为人工真值。'])
    markdown(out,'annotation_first5/ai_pre_review.md',[
        '# AI证据预审：与人工盲标分开','',
        '这是助手的初步材料分析，助手已看过模型汇总，因此不是独立盲标，不写入annotations.json，也不计作人工真值或质量指标。以下“支持”仅指待人工确认的原文关系，不代表满足完整准入。',
        '', '|样本|原文预审及证据|重点人工核对|','|---|---|---|',
        '|DEV-004|ev_49cebfb2fa94214e55e4、ev_916c5c438459f294e912均明示“横风引起侧偏”，核心关系有直接文字依据。|区分定性因果与附近公式的假设；候选没有抽取公式数值结论。映射定义仍缺。|',
        '|DEV-007|ev_4f89e426cf3ce358975f明示超声速弹丸飞行中形成弹道波；“超声速”及“飞行中”需保留。|确认完整候选的条件字段，不从未加载图像推导额外含义；不扩大到所有弹丸。|',
        '|DEV-008|ev_c8bc5b0bf7eca019fc99直接支持当前机制短语。|正式关系定义缺失不能直接判valid；现有草案未获批。原抽取短语不得因Schema替换而丢失机制。|',
        '|DEV-014|ev_a3da0498469c6db1da87、ev_49a0b96c3f815e30ee47补齐“其”的切线方向先行词，垂直关系可核对。|几何关系与当前自然坐标系的局部符号约定应分开判断，范围不能只因出现定义即排除或纳入。|',
        '|DEV-015|ev_0ddb306cd5e0abb97cd2给出因果，ev_6fd641de970a3b91cf58/ev_823711a6a4f5b03ed371标题补齐“空气弹道”语境。|确认“弹道”指代与标题一致；source维度当前未齐可能涉及正面证据角色/引用，可作为人工错误分析线索，尚不能计为系统误拒。|',
        '', '五条正式关系定义均未在当前发布版提供，映射预审保留context_incomplete，不能从结构约束pass推导语义映射有效。',
        '初步错误分析优先区分三类：材料/发布版本不足、证据角色导致维度不可用、候选表达本身有误。没有人工标签前不统计误放、错修或准确率。'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'outputs/dev30_v8_focus_20261110');args=parser.parse_args()
    out=args.output.resolve()
    if (out.exists() and any(out.iterdir())) or not out.is_relative_to(ROOT/'outputs'):raise ValueError('use a new or empty output directory under outputs')
    out.mkdir(parents=True,exist_ok=True);repo=ReadOnlyRepository(SOURCE/'review.sqlite3');cases=read(DERIVED/'smoke_report.json')['cases'];packets=read(DERIVED/'review_packets.json')['samples']
    watched=[SOURCE/'review.sqlite3',DERIVED/'review.sqlite3',DERIVED/'review_packets.json',DERIVED/'smoke_report.json',ROOT/'runs/v3/review.sqlite3',
        ROOT/'runs/ontology_publication_20261031/unapproved_owl_draft_manifest.json',ROOT/'runs/ontology_publication_20261031/crosswind_definition_competency_known_seat_status.json']
    hashes={str(p):digest(p) for p in watched}
    with ExitStack() as stack:
        for name in ('OpenAICompatibleClient','MockLLMClient'):
            stack.enter_context(patch('review_agent.llm_clients.'+name+'.complete_json',side_effect=RuntimeError('focused analysis prohibits new model calls')))
        calls=analyze_calls(repo,cases);queue=manual_queue(repo,cases);pilot=mapping_pilot(repo,packets);selection=annotations(repo,packets,out)
    save(out,'call_and_contract_analysis.json',redact(calls));save(out,'manual_queue_24.json',redact(queue));save(out,'mapping_pilot_DEV008.json',pilot)
    readable_reports(out,calls,queue,pilot,selection,packets)
    assert hashes=={str(p):digest(p) for p in watched},'read-only follow-up changed source bytes'
    save(out,'manifest.json',{'generated_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),'source_files_sha256':hashes,
        'script_sha256':digest(Path(__file__)),'model_calls':0,'production_written':False,'original_votes_and_decisions_changed':False,
        'first5':list(FIRST),'human_labels_confirmed':0})
    print(json.dumps({'output':str(out),'timeouts':calls['timeout_counts'],'patch_roi':calls['patch_roi'],
        'manual_queues':queue['primary_queue_counts'],'derived_recommendations':queue['derived_recommendation_counts'],
        'mapping_blocker':pilot['actual_blocker'],'first5':[r['sample_id'] for r in selection]},ensure_ascii=False))


if __name__=='__main__':main()
