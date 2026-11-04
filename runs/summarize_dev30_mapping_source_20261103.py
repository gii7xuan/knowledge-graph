"""Verify and summarize the development mapping/source continuation."""
from collections import Counter
from copy import deepcopy
from datetime import datetime
from importlib.util import spec_from_file_location, module_from_spec
import json
from pathlib import Path
import sqlite3

from ontology_review.delta import catalog, check_structure
from ontology_review.publisher import current_release_id
from review_agent.contracts import sha256_json
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.proposal_resolver import selected_proposal
from review_agent.repository import ReviewRepository

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/dev30_mapping_source_20261103'
PREVIOUS = ROOT / 'outputs/dev30_revision_pilot_20261103'
spec = spec_from_file_location('dev4_pilot', ROOT / 'runs/prepare_dev30_revision_pilot_20261103.py')
pilot = module_from_spec(spec); spec.loader.exec_module(pilot)
integrity = pilot.verify_inputs(json.loads((pilot.SOURCE / 'sample_data.json').read_text(encoding='utf-8')))
repo = ReviewRepository(OUT / 'development_review.sqlite3')
frozen = json.loads((PREVIOUS / 'frozen_targets.json').read_text(encoding='utf-8'))
bindings = {i['base_binding']['candidate_id']: i for i in frozen['items']}
application = json.loads((OUT / 'application_status.json').read_text(encoding='utf-8'))
resolution_path = OUT / 'DEV-017_definition_reuse_resolution.json'
dev017_resolution = json.loads(resolution_path.read_text(encoding='utf-8')) if resolution_path.exists() else None
rows = []
for item in application['results']:
    sid = item['sample_id']
    if not item.get('applied'):
        result = json.loads((OUT / (sid + '_mapping_review.json')).read_text(encoding='utf-8'))
        assert result['status'] == 'needs_human'
        assert sum(v.get('response_status') == 'ok' and v.get('decision') == 'reject' for v in result['votes']) == 1
        rows.append({'sample_id': sid, 'status': 'semantic_mapping_disagreement_preserved',
                     'development_applied': False, 'candidate_id': next(i for i in frozen['items'] if i['sample_id'] == sid)['base_binding']['candidate_id'],
                     'mapping_votes': [{k: v.get(k) for k in ('voter_id', 'decision', 'response_status')} for v in result['votes']],
                     'original_negative_vote': next(v for v in result['votes'] if v['decision'] == 'reject')})
        if sid == 'DEV-017' and dev017_resolution:
            assert not dev017_resolution['generate_part_triple']
            rows[-1].update(status=dev017_resolution['status'],
                            resolution_hash=dev017_resolution['resolution_hash'],
                            definition_binding=dev017_resolution['definition_binding'],
                            generate_new_fact_candidate=False,
                            original_mapping_disagreement_preserved=True)
        continue
    candidate = repo.get_revision(item['candidate_id']); decision = repo.latest_decision(item['candidate_id'])
    proposal, votes = selected_proposal(repo, candidate, decision)
    assert len(votes) == 3 and all(v['response_status'] == 'ok' and v['source_support'] == 'supported' for v in votes)
    assert len({v['model_family'] for v in votes}) == 3
    audit = repo.get_audit(candidate['candidate_id'], decision['audit_id'])
    assert audit['context']['independent_review_completed']
    current = repo.get_candidate(candidate['candidate_id'])
    rows.append({'sample_id': sid, 'candidate_id': candidate['candidate_id'], 'revision': candidate['revision'],
                 'candidate_content_hash': sha256_json(candidate), 'source_support': 'supported',
                 'independent_source_votes': len(votes), 'source_audit_id': audit['audit_id'],
                 'source_proposal_digest': proposal['proposal_digest'],
                 'source_evidence_hash': proposal['evidence_bundle']['evidence_hash'],
                 'ontology_guard': proposal['ontology_check']['status'], 'workflow_state': current['workflow_state'],
                 'pending_extension_id': current.get('pending_extension_id'), 'development_applied': True})

source20 = next(x for x in rows if x['sample_id'] == 'DEV-020')
task = repo.extension_status(source20['pending_extension_id'])
prep = json.loads((OUT / 'DEV-020_ontology_agent_preparation.json').read_text(encoding='utf-8'))
ontology_audit = repo.ontology_audit(prep['audit_id']); checkpoint = ontology_audit['checkpoint']['state']
checks = checkpoint['checks']; delta = task['delta']; patch = delta['owl_patch']
assert checks['structure']['status'] == 'pass' and checks['impact']['status'] == 'pass'
assert checks['competency']['status'] == 'unknown'
assert not ontology_audit['votes'] and not prep['release_published']
assert not patch['removed'] and patch['preserved_triple_count'] == patch['before_triple_count'] == 882
identity = delta['operations'][0]['target']; candidate20 = repo.get_revision(source20['candidate_id'])
probe = deepcopy(candidate20); probe['triple']['relation_id'] = identity
new_guard = OntologyGuard(load_snapshot(delta['merged_schema']))
assert new_guard.check(probe)['status'] == 'pass'
readonly = pilot.ReadOnly(ROOT / 'runs/v3/review.sqlite3')
with readonly.connect() as c:
    production = [json.loads(r[0]) for r in c.execute('SELECT r.candidate_json FROM candidates c JOIN candidate_revisions r ON r.candidate_id=c.candidate_id AND r.revision=c.current_revision')]
    statuses = {r[0]: r[1] for r in c.execute('SELECT human_status,COUNT(*) FROM candidates GROUP BY human_status')}
from ontologyBuild.release_service import load_release
base = load_release(candidate20['ontology_release_path']); old_guard = OntologyGuard(load_snapshot(base.ontology))
fields = ('status', 'relation_known', 'relation_id', 'domain_status', 'range_status', 'type_unknown',
          'mapping_ambiguous', 'hard_failure', 'reason_codes', 'constraint_semantics')
guard_changes = []
for c in production:
    before = old_guard.check(c); after = new_guard.check(c)
    if any(before.get(k) != after.get(k) for k in fields): guard_changes.append(c['candidate_id'])
assert not guard_changes and len(production) == 320
assert current_release_id(ROOT / 'runs/ontology_releases') == base.manifest['release_id']
previous_repo = pilot.ReadOnly(PREVIOUS / 'development_review.sqlite3')
for item in frozen['items']:
    assert sha256_json(previous_repo.get_revision(item['base_binding']['candidate_id'])) == item['base_binding']['candidate_content_hash']
with previous_repo.connect() as c: previous_attempts = {r[0] for r in c.execute('SELECT attempt_id FROM request_attempts')}
with repo.connect() as c:
    new_requests = [dict(r) for r in c.execute('SELECT attempt_id,state,response_status,metadata_json FROM request_attempts') if r['attempt_id'] not in previous_attempts]
assert not any(r['state'] in ('unknown', 'running') for r in new_requests)

dest = OUT / 'DEV-020_agent_prepared_owl'; dest.mkdir(exist_ok=True)
for name, obj in (('delta.json', delta), ('checks.json', checks), ('evidence_bundle.json', checkpoint['evidence']),
                  ('ontology.json', delta['merged_schema'])):
    (dest / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
(dest / 'ontology.ttl').write_text(patch['merged_rdf'], encoding='utf-8')
(dest / 'README.md').write_text(
    '# DEV-020 本体Agent准备稿，未批准\n\n'
    '来源审核已三席支持，正式本体Agent已检索完整本体并创建数据属性草案，结构/影响检查通过。'
    '本地能力问题检查为unknown：自然语言能力问题尚未得到独立语义验证。未分派本体委员票，未发布。\n\n'
    '本目录是Agent实际任务当前delta及检查记录；与旁边手工最小草案在定义措辞/注释语言上不同，不能混用delta哈希或审核结果。'
    '本轮核对320条既有候选后，约束判定均未变化；保留原有882条RDF，新增5条。该结果不能替代语义验证或准入。\n', encoding='utf-8')

summary = {'verified_at_beijing': datetime.now().astimezone().isoformat(), 'results': sorted(rows, key=lambda r: r['sample_id']),
           'development_applied_count': 3, 'fresh_source_supported_count': 3, 'fresh_source_valid_votes': 9,
           'ontology_preparation': {'extension_id': task['extension_id'], 'delta_hash': delta['delta_hash'],
                                    'structure': checks['structure']['status'], 'impact': checks['impact']['status'],
                                    'local_competency': checks['competency']['status'],
                                    'independent_ontology_votes': 0, 'release_published': False,
                                    'rdf_before': 882, 'rdf_after': patch['after_triple_count'],
                                    'old_candidate_guard_changes': len(guard_changes), 'old_candidates_scanned': len(production),
                                    'planner_usage': prep['budget']['usage']},
           'new_journaled_request_attempts': len(new_requests), 'new_request_states': dict(Counter(r['state'] for r in new_requests)),
           'new_unknown_requests': 0, 'production_applied': False, 'production_human_status_counts': statuses,
           'formal_release_unchanged': base.manifest['release_id'], 'previous_pilot_revisions_unchanged': True,
           'source_workbook_and_live_candidate_integrity': integrity, 'model_approval_is_not_human_accuracy': True}
(OUT / 'verification_and_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
report = ['# 正式映射与新版本来源审核结果', '',
          '本轮在新的开发副本中继续核验。原Excel、生产320条候选、既有人工状态、前次开发副本、本体正式版本及Neo4j均未修改。', '',
          '| 样本 | 本轮结果 | 下一步 |', '|---|---|---|',
          '| DEV-004 | 补齐推导条件后的新版本，三席均支持原文，本体检查通过 | 等待人工对完整修订稿确认，再进入正式提交 |',
          '| DEV-013 | 补全“弹丸”后的新版本，三席均支持原文，本体检查通过 | 保留用户缺词解释；未补造人工总体标签 |',
          ('| DEV-017 | 用户选择复用已有“风偏”定义；原文已绑定为定义证据 | 已收口，不生成部件三元组；历史分歧票保留 |'
           if dev017_resolution else '| DEV-017 | 正式“具有部件”映射出现2赞成、1反对，未应用 | 用户选择定义复用或保留组成稿待裁决 |'),
          '| DEV-020 | 主体类型获三席批准；新版本三席均支持原文 | 数据属性草案等待本体语义审核与发布 |', '',
          '## DEV-017 的实质分歧', '',
          'Qwen认为原文“风引起的射程偏差和方向偏差统称为风偏”是统称定义，不等同于部件构成；Gemma和DeepSeek赞成组成解释。原有效反对票及对应原文保留，没有重抽此票。', '',
          ('用户已明确选择“复用已有定义，不生成部件三元组”。已核验正式OWL中的“风偏”类C_8dcbead39b715b819261及其skos:definition“风引起的射程偏差和方向偏差的统称。”，并绑定原文ev_c3774a70bceb0819698c。处理结果保存至DEV-017_definition_reuse_resolution.json和开发副本的development_case_resolutions表；不修改已有定义、不新增本体版本，也不生成部件三元组或新事实候选。原标注、原候选和历史票保留；该条作为定义证据复用案例，不计作新事实修订后准确率样本，也不标为事实错误或无效知识。'
           if dev017_resolution else '现有“风偏”类已有定义“风引起的射程偏差和方向偏差的统称。”。建议把原文绑定为该定义的证据，不新增部件三元组。另一选项是保留用户的“由…组成”稿待人工裁决。用户问题仍在等待回复，本轮未代替用户选择，也未套用旧文字修订批准去绕过新映射反对。'), '',
          '## DEV-020 的缺口与准备结果', '',
          '正式本体189个类和81个对象属性中没有数据属性。主体可复用已有“弹丸”类；所缺的是把它关联到“章动角（或攻角）很小”这一字符串的属性。未将字符串伪装成实体。', '',
          '三席来源支持后，现有Agent自动创建add_data_property扩展任务。随后本体准备Agent用2次规划调用完成完整本体检索、属性草案、结构检查、影响分析与本地能力问题检查。结构及影响为pass；本地能力问题为unknown，因为自然语言问题未做独立语义验证。', '',
          'Agent实际草案见DEV-020_agent_prepared_owl。它完整保留原有882条RDF，新增5条；扫描320条当前候选后约束判定均未变化。尚无本体三席审核票，未发布，不能以草案ID直接准入。旁附的手工最小草案用于前期探测，定义措辞与Agent稿不同，两者哈希和验证记录不能混用。', '',
          '## 实验结果的口径', '',
          '本轮完成3条完整新版本的来源审核，共9张有效支持票。这是来源支持和正式约束的开发验证，不是人工准确率、全章效果或泛化结果。DEV-017也说明“文字修订通过”不能替代正式语义映射审核；DEV-020说明“概念上可表达”与正式数据属性齐全是不同指标。', '',
          '原模型、证据和票的绑定已核对，本轮没有未知请求。原填写文件哈希与30条生产候选哈希保持不变。']
(OUT / '正式映射与来源审核结果.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
print(json.dumps({k: summary[k] for k in ('development_applied_count', 'fresh_source_supported_count', 'fresh_source_valid_votes',
                                        'ontology_preparation', 'new_journaled_request_attempts', 'new_request_states',
                                        'new_unknown_requests', 'production_human_status_counts', 'production_applied')}, ensure_ascii=False))
