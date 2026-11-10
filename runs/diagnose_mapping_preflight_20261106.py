"""Read-only diagnosis of the 14 waiting-mapping cases using their bound snapshots."""
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path

from evaluation.review_samples import ReadOnlyRepository
from review_agent.contracts import sha256_json
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.ontology_context import policy_for as context_policy
from review_agent.mapping_preflight import prepare, policy_for, definitions
from schema.relation_registry import RelationRegistry

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'outputs/dev30_v63_stream_rerun_20261106'
OUT = ROOT/'outputs/mapping_preflight_20261106'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    old = json.loads((SOURCE/'comparison.json').read_text(encoding='utf-8'))
    repo = ReadOnlyRepository(SOURCE/'review.sqlite3')
    entries, cases, definition_tasks, catalogs = {}, [], {}, {}
    for case in old['cases']:
        if case['workflow_state'] != 'mapping_review': continue
        candidate = repo.get_revision(case['candidate_id'], case['revision'])
        audit = repo.get_audit(candidate['candidate_id'], case['audit_id'])
        digest = repo.latest_decision(candidate['candidate_id'])['selected_proposal_digest']
        proposal = next(p for p in audit['rounds'] if p['proposal_digest'] == digest)
        assert sha256_json(candidate) == sha256_json(proposal['candidate'])
        guard = OntologyGuard(load_snapshot(candidate.get('ontology_path') or repo.get_run(candidate['run_id'])['ontology_path'], candidate['ontology_version']))
        initial = proposal['ontology_context']; bundle = proposal['evidence_bundle']
        enriched, report = prepare(guard, candidate, bundle, initial, proposal.get('ontology_context_policy') or context_policy({}), policy_for({}))
        assert initial['source_catalog_hash'] == report['source_catalog_hash']
        binding = {'audit_id': audit['audit_id'], 'proposal_digest': digest, 'candidate_hash': sha256_json(candidate),
                   'evidence_hash': bundle['evidence_hash'], 'ontology_context_hash': proposal['ontology_context_hash']}
        entries[audit['audit_id']] = {'binding': binding, 'report': report, 'report_hash': sha256_json(report)}
        triple = candidate['triple']; rid = triple.get('relation_id')
        global_properties = RelationRegistry(guard.ontology).properties
        catalogs[report['source_catalog_hash']] = {'ontology_release_id':candidate.get('ontology_release_id'),
            'relation_count':len(global_properties),
            'relation_definition_count':sum(bool(definitions(p)) for p in global_properties.values())}
        category = 'assigned_id_definition_missing' if rid and not definitions(global_properties.get(rid, {})) else 'formal_id_unassigned' if not rid else 'assigned_metadata_needs_review'
        selected_votes = [v for v in audit['votes'] if v['proposal_digest'] == digest]
        formal_rejections = [v['model_id'] for v in selected_votes if v.get('mapping_diagnosis') in {'candidate_mapping_error','constraint_conflict'}]
        cases.append({'sample_id': case['sample_id'], 'candidate_id': candidate['candidate_id'], 'audit_id': audit['audit_id'],
            'category': category, 'mapping_judgments_recorded': len(selected_votes),
            'formal_id_assigned': bool(rid), 'judgment_ready': report['judgment_ready'],
            'program_proved_definition_inapplicable': False, 'model_formal_conflict_opinions': formal_rejections,
            'initial_property_ids': [p['id'] for p in initial['properties']], 'supplemented_property_ids': [p['id'] for p in enriched['properties']],
            'assigned_relation_id': rid, 'candidate_relation_ids': report['formal_mapping']['candidate_relation_ids'],
            'blocking_information': report['blocking_information'], 'report_hash': sha256_json(report)})
        if rid and category == 'assigned_id_definition_missing':
            definition_tasks[rid] = {'relation_id': rid, 'existing_property': global_properties[rid],
                'source_sample_id': case['sample_id'], 'candidate_expression': report['original_expression'],
                'source_evidence': [{'evidence_id': e['evidence_id'], 'quote': e.get('quote'), 'anchor_valid': e.get('anchor_valid')}
                    for e in bundle['items']], 'action': 'curate_and_independently_review_existing_definition',
                'proposed_definition': None, 'new_relation_needed': None,
                'new_relation_need_confirmed': False}
    assert len(cases) == 14
    result = {'created_at_beijing': datetime.now(timezone(timedelta(hours=8))).isoformat(), 'population': 14,
        'counts': dict(Counter(c['category'] for c in cases)), 'ready_for_semantic_mapping': sum(c['judgment_ready'] for c in cases),
        'model_requests': 0, 'historical_votes_changed': False, 'ontology_published': False,
        'global_gap_proved': False, 'bound_catalogs':catalogs, 'cases': cases}
    (OUT/'diagnosis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (OUT/'existing_definitions_to_complete.json').write_text(json.dumps(list(definition_tasks.values()), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (SOURCE/'mapping_diagnostics.json').write_text(json.dumps({'version':'post_run_mapping_diagnostics_v1', 'entries':entries}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    assigned = [c['sample_id'] for c in cases if c['category'] == 'assigned_id_definition_missing']
    unassigned = [c['sample_id'] for c in cases if c['category'] == 'formal_id_unassigned']
    lines = ['# 14条等待映射候选的信息排查', '', '北京时间：'+result['created_at_beijing'], '',
        '使用各候选原绑定发布快照、原冻结证据和原审核记录进行本地诊断。没有发模型请求，没有改票或修改发布本体。', '',
        f'- {len(assigned)}条已填写正式关系ID，但发布快照中的定义正文为空：'+', '.join(assigned)+'。',
        f'- {len(unassigned)}条未填写正式关系ID：'+', '.join(unassigned)+'。',
        '- 14条都有已记录的模型映射意见，不能将“未分配ID”一概解释成“模型没有执行映射判断”。',
        '- DEV-003 的“表示”在完整绑定本体中有精确名称对应，但该条尚未绑定正式ID；名称命中仅产生候选，仍需定义和端点核验。',
        *[f"- 绑定发布版本 {info['ontology_release_id']}：{info['relation_count']} 个关系中，{info['relation_definition_count']} 个有定义正文。类定义不能替代关系语义。" for info in catalogs.values()],
        '- 当前没有任何一条被程序证明“已有定义语义不适用”，也没有证明本体缺少这些概念。', '',
        '|样本|已填关系ID|定义与映射状态|补检后可进入完整映射判断|', '|---|---|---|---|']
    for c in cases:
        lines.append('|'+ '|'.join([c['sample_id'], c['assigned_relation_id'] or '未分配',
            '现有关系缺定义正文' if c['category']=='assigned_id_definition_missing' else '正式ID未分配，候选仍待核验',
            '是' if c['judgment_ready'] else '否，仍有信息义务'])+'|')
    lines += ['', '## 优先处理', '',
        '先补录并审查7个已指明关系的定义，不通过新增近义关系绕过。整理材料见 existing_definitions_to_complete.json：保留原关系记录、约束和原文证据，不自动生成或发布定义。',
        '再处理其余7条的实体类型、指称及正式关系候选；精确匹配、别名匹配和词法召回分开记录。局部片段不足时补检完整绑定版本，仍不把词法未命中当作全局语义缺口。',
        '独立诊断通过 audit_id、提案摘要、候选hash、原证据hash及原上下文hash绑定。监控可展示该补充诊断，但历史决定仍保持原样。', '',
        '详细原因及补检记录见 diagnosis.json 和原实验目录 mapping_diagnostics.json。']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='cases'}, ensure_ascii=False))


if __name__ == '__main__': main()
