"""Read-only, source-bound diagnosis of all 30 completed development samples."""
from collections import Counter
from datetime import datetime, timezone, timedelta
from importlib.util import spec_from_file_location, module_from_spec
import hashlib
import json
from pathlib import Path

from ontologyBuild.release_service import load_release
from ontology_review.delta import catalog
from evidence.verification import make_evidence_bundle, verify_evidence_bundle
from document.hierarchy_index import load_document_index
from review_agent.contracts import sha256_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/dev30_error_diagnosis_20261103'
OUT.mkdir(parents=True, exist_ok=True)
SOURCE = ROOT / 'outputs/dev30_20261031'
BOOK = SOURCE / '第一章30条开发样本_人工填写.xlsx'
data = json.loads((SOURCE / 'sample_data.json').read_text(encoding='utf-8'))
annotations = json.loads((ROOT / 'outputs/dev30_analysis_20261103/annotations_scoped_v4.json').read_text(encoding='utf-8'))
correction_dir = ROOT / 'outputs/dev30_corrections_20261103'
correction_event = json.loads((correction_dir / 'human_correction_event.json').read_text(encoding='utf-8'))
subject_revision = json.loads((correction_dir / 'DEV-022_correction_result.json').read_text(encoding='utf-8'))
workbook_proof = json.loads((correction_dir / 'workbook_verification.json').read_text(encoding='utf-8'))
assert subject_revision['source_support_result'] == 'supported' and not subject_revision['production_applied']
assert subject_revision['candidate']['revision'] == 2 and subject_revision['candidate']['triple']['head'] == '矢量方程（1-56）'
assert workbook_proof['original_modified'] is False and len(workbook_proof['changed_cells']) == 6
workbook_hash = hashlib.sha256(BOOK.read_bytes()).hexdigest()
assert workbook_hash == annotations['scope']['source_workbook_sha256']
spec = spec_from_file_location('dev4_pilot', ROOT / 'runs/prepare_dev30_revision_pilot_20261103.py')
pilot = module_from_spec(spec); spec.loader.exec_module(pilot)
integrity = pilot.verify_inputs(data)
repo = pilot.ReadOnly(data['source_database'])
run = repo.get_run(data['selected_raw_candidates'][0]['run_id'])
document = load_document_index(run['document_path'], data['selected_raw_candidates'][0]['source_index_hash'], require_verified=True)
text = document['source_snapshot']['canonical_text']; assert text == data['source_canonical_text']
terms = catalog(load_release(data['selected_raw_candidates'][0]['ontology_release_path']).ontology)

# These are diagnostic findings, not replacement human labels or admission flags.
findings = {
 'DEV-001': ('关系边界/映射待整理', '原文直接支持“基本一致”。候选谓语又包含整个宾语“国际上通用的标准”，与tail重复；正式关系ID、尾类型未指定。', '先规范为比较关系并保留“基本”程度，再核验正式映射。人工H列否的具体理由尚未单独给出。', '原文及结构可确认；人工判否理由待补'),
 'DEV-002': ('来源判断分歧', '人工标证据不足，三模型支持。冻结证据中有“最后再根据状态方程，即可分别得到…H(y)”及式1-28，不能直接认定为模型无依据支持。', '补看上游温度/气压标准分布与状态方程的推导范围，复核人工证据不足的依据。', '分歧待复核'),
 'DEV-003': ('缺引用＋词面映射错误', '三席来源字段均支持，但Gemma的qualifiers=pass没有任何evidence_id，来源票只计2席；guard又把“表示”匹配到空气阻力函数→弹丸运动的既有属性。', '把缺引用作为可定位的契约失败处理；不能为满足domain把弹道系数改成空气阻力函数，应重新做语义映射。', '审核记录及代码可确认'),
 'DEV-004': ('原推导限定未进入结构', '候选横风→引起→侧偏未记录其引用公式推导中的恒定风和弹轴/相对速度重合假设。原文支持核心因果，人工指出限定不完整。', '针对该推导保留两个假设；不把它们说成所有横风引起侧偏的普遍必要条件。开发稿三席来源支持，人工总体新稿标签仍为空。', '用户指出缺限定；具体受限修订已核验'),
 'DEV-005': ('极性未闭合＋公式范围', 'polarity仍为unknown。Qwen还要求核清vτ分段经验公式的适用范围；函数归属与其某一分段公式条件不能混为一谈。', '依据肯定原文提交极性修订，同时分清F(vτ)与分段公式条件，不靠默认positive放行。', '极性可确认；条件范围待细核'),
 'DEV-006': ('极性未闭合', '原文是肯定类比句，但候选polarity=unknown，DeepSeek明确指出表达未完成；正式关系ID及类型也未齐。', '按原文修订极性，保留“类似于”的类比语义，另做正式映射。', '审核记录可确认'),
 'DEV-007': ('人工标注错误，已确认更正', '原文直接出现“超声速弹丸飞行中所形成的弹道波”。用户已确认原反驳/事实否为标注错误，改为支持/是；此条原模型来源支持没有判断错误。', '校正版及v4有效标注已更正；原填写文件、冻结候选及历史返回保留。', '用户明确确认，已更正'),
 'DEV-008': ('未发现明确来源错误', '候选保留“通过改变空气阻力的方向”机制，原文、人工与三席来源一致，现有正式映射通过。', '保持当前证据和版本绑定；不能由单例通过外推全章准确率。', '本轮未见明确错误'),
 'DEV-009': ('比较关系尚未规范映射', '同等弹道高度条件已保留，人工和三席均支持；谓语“比升弧陡”重复包含宾语，正式比较关系ID缺失。', '整理成明确比较谓语，保留等高度条件；本体unknown不当作事实反驳。', '形式问题可确认'),
 'DEV-010': ('属性/值表示未落地', '空气具有一定密度有来源支持，但head/tail类型和正式关系ID为空，“一定的密度”目前被装成一个实体。', '决定采用密度属性、定性说明或有证据的量值结构，不捏造具体密度数值。', '结构事实可确认；建模方案待审'),
 'DEV-011': ('人工标注错误，形式映射仍待处理', '原文直接比较i43小于/大于1与标准弹空气阻力的大小，候选已保留比较对象及两种取值条件。用户确认原反驳/事实否为标注错误；来源支持判断正确，但宽泛谓语的正式映射仍未完成。', '校正版及v4有效标注改为支持/是；将来源正确与正式关系尚未落地分别记录。主表$$另属展示缺陷。', '用户明确确认，已更正'),
 'DEV-012': ('多参数对象的形式映射', '候选已保留动参考系、无风/弹轴重合及can模态，人工和来源意见一致；三个参数被拼在一个entity尾项，正式ID未指定。', '后续明确参数集合/多元依赖表示，不在拆分时丢条件；主表$$为展示缺陷。', '形式问题可确认，未确认事实错误'),
 'DEV-013': ('对象所属者漏词', '候选写“表面粗糙度”，原文写“弹丸表面的粗糙度”。模型在上下文中补足了“弹丸”，导出的实体名称却未携带所属对象，未满足独立三元组的完整性要求。', '按用户解释补成“弹丸表面粗糙度”；既有尾类型实际就是表面粗糙度，不必为此改类型。开发新稿三席来源支持。', '用户已确认原因'),
 'DEV-014': ('未发现明确来源错误', '法线垂直切线的关系、人工和三席来源一致，正式映射通过。主表直接引文曾误取为$$。', '使用完整证据/补充正文核验；展示问题不算此事实错误。', '来源未见明确错误；展示缺陷已确认'),
 'DEV-015': ('未发现明确来源错误', '空气阻力影响空气弹道的原文、人工判断及现有映射一致。', '保持版本与证据绑定。', '本轮未见明确错误'),
 'DEV-016': ('并列成分与局部限定', '整句组成说明有支持，但三个组成项被拼成一个entity；“超声速时所特有”只修饰波动阻力，当前限定保留在尾项正文而非独立字段。', '若拆成多条关系，应仅给波动阻力项保留超声速限定，不能扩散给全部组成项或直接丢失。', '结构风险可确认，整句来源获支持'),
 'DEV-017': ('定义句误作部件关系', '“射程偏差和方向偏差统称为风偏”被抽为风偏具有部件射程偏差；随后正式映射审核产生实质分歧。用户已选择复用既有风偏定义。', '已收口为本体定义证据，不生成部件三元组，不将其标成事实错误或无效知识。', '用户已明确处理'),
 'DEV-018': ('极性与比例关系范围', 'polarity=unknown；DeepSeek指出多因素共同作用及适用条件未保留。人工认为限定完整，二者有范围判断差异。', '先修订有证据的极性，再复核比例关系的控制条件；不默认任意条件下均严格成正比。', '极性可确认；条件范围有分歧'),
 'DEV-019': ('未发现明确来源错误', '地炮需要知道弹道顶点的表述得到人工和三席支持，正式映射通过。', '保持当前证据和版本。', '本轮未见明确错误'),
 'DEV-020': ('属性切分＋检查层次混用', '关系槽中混入“其章动角（或攻角）总是”，尾值仅“很小”。审核中又把缺正式关系误用为原文方向未知，造成来源共识与本体申请互相等待。', '已改为具体角度定性属性，保留稳定飞行条件/总是量词；提示版本分开原文方向与本体约束，开发候选第4版来源共识已闭合。本体发布仍有另行执行阻塞。', '结构、原审核记录及新版本对照已确认'),
 'DEV-021': ('定义/字面值映射未齐', '数学定义以literal尾值保存，原文及人工支持；正式关系ID缺失，不能用概念可表达替代正式属性检查。', '采用可承载定义文字的规范表示，并保持原模态，不转成无依据的实体关系。', '形式问题可确认，未确认事实错误'),
 'DEV-022': ('投影对象与结果混淆，主语已修订', '原文将矢量方程投影后得到微分方程组，原候选却让结果充当被投影主语。用户确认修正；原两席反对有依据。开发第2版改为矢量方程（1-56），肯定极性有证据，新版三席来源支持。', '按用户明确人工修订保存新版本；保留修改审查2赞成1反对，未伪造自动全票。旧微分方程组类型和投影关系ID清空，正式映射待补，未准入生产。', '用户确认且新版本来源三席支持'),
 'DEV-023': ('近似语义已保留、形式未齐', '候选保留射高/射程范围、量值变化不大及can模态，人工和来源一致；常数尾项及正式关系ID未定。', '按近似属性/说明建模，不改成无条件严格恒定的断言。', '未见明确来源错误'),
 'DEV-024': ('未发现明确来源错误', '空气阻力影响枪弹的原文、人工和三席来源一致，正式映射通过。', '保持当前证据与版本。', '本轮未见明确错误'),
 'DEV-025': ('未发现明确来源错误', '空气近似理想气体获人工和三席来源支持，正式映射通过；主表引文曾仅为$$。', '使用完整补充正文；近似程度不改为严格同一关系。', '来源未见明确错误；展示缺陷已确认'),
 'DEV-026': ('反证字段自相矛盾＋结构混放', 'DeepSeek在counterevidence检查中明确说小章动角近似“不否定”原因果，却仍把该段列入counter_evidence_ids；聚合按实际反证列表阻断。literal尾项还附带了弹丸实体类型。', '分开已检查的候选反例与仍成立的实质反证；一致性不符应要求原模型澄清，不能程序自动删除反证。另修事件/字面值结构并同步恢复后的决策摘要。', '票字段、正文和聚合规则可确认'),
 'DEV-027': ('条件/模态已保留、形式未齐', '候选保留物体类别、经过速度最小值后及may模态，人工与来源一致，正式类型和关系仍缺。', '后续映射保留可能性和条件，不改成所有物体都出现的必然事实。', '未见明确来源错误'),
 'DEV-028': ('收录范围＋表格主语迁移', '用户已说明本次不收录该公式讲解知识。DeepSeek另指出证据说表1-4的射程变化计算考虑地面曲率，没有明确以“弹丸运动方程”为主语。正式关系还错配到考虑科氏加速度的属性。', '按用户任务范围排除，保留原来源标签；不作为事实错误负例，不用错配属性硬映射。', '用户已确认范围；证据对象差异可定位'),
 'DEV-029': ('任务收录范围', '原文确有坐标轴与正北夹角的设定，三模型支持；用户明确认为这类讲解公式的假设不是本次有效知识。', '加入任务知识类型筛选；只针对该案例，不采用“含公式就排除”的规则。', '用户已确认'),
 'DEV-030': ('命名与分类语义未分清', '原文是运动方程“称为”三自由度方程，候选改成三自由度方程“是”运动方程。一席因命名/分类及本体方向未定而保留unknown。', '明确要保留命名定义还是分类事实，并与正式映射分开判断；不能直接把反向改写当同一关系。', '审核记录可确认，未确认事实错误'),
}
unresolved_groups = {
 '极性未闭合为主要阻塞': ['DEV-005','DEV-006','DEV-018'],
 '定义/命名/属性方向待厘清': ['DEV-017','DEV-020','DEV-030'],
 '来源主语或对象有实质分歧': ['DEV-022','DEV-028'],
 '限定检查缺少引用': ['DEV-003'],
 '反证角色字段冲突': ['DEV-026'],
}
all_grouped = [sid for group in unresolved_groups.values() for sid in group]
assert len(all_grouped) == len(set(all_grouped)) == 10
assert set(all_grouped) == {row[0] for row in data['model_rows'] if row[3] == 'unresolved'}

records = []
for human, binding, candidate, model_row in zip(annotations['annotations'], data['bindings'], data['selected_raw_candidates'], data['model_rows']):
    sid = binding['sample_id']; assert human['sample_id'] == sid and sha256_json(candidate) == binding['candidate_content_hash']
    audit = repo.get_audit(candidate['candidate_id'], binding['audit_id'])
    proposal = next(p for p in audit['rounds'] if p['proposal_digest'] == binding['source_proposal_digest'])
    bundle = verify_evidence_bundle(make_evidence_bundle(proposal['evidence_bundle']['items'], []), document, candidate)
    assert not bundle['rejected_items']
    votes = [v for v in audit['votes'] if v['round_id'] == proposal['round_id']]
    assert len(votes) == 3 and all(v['response_status'] == 'ok' for v in votes)
    allowed = {e['evidence_id'] for e in bundle['items']}
    compact_votes = []
    for v in votes:
        checks = {x['check']: x for x in v['checks']}
        omissions = [{'check': name, 'status': checks[name]['status'], 'issue': 'no_verified_citation', 'note': checks[name]['note']}
                     for name in ('triple_support','qualifiers','relation_direction')
                     if checks[name]['status'] in {'pass','not_applicable'} and not set(checks[name]['evidence_ids']) & allowed]
        compact_votes.append({**{k:v.get(k) for k in ('voter_id','model_id','model_family','source_support','verdict',
                                                       'supporting_evidence_ids','counter_evidence_ids','checks','suggested_revision')},
                              'core_check_citation_omissions': omissions})
    diagnosis = findings[sid]
    decision = repo.latest_decision(candidate['candidate_id']) or {}
    consensus = decision.get('consensus') or {}
    records.append({'sample_id': sid, 'binding': binding, 'original_triple': candidate['triple'],
                    'human_labels_original': human['human_labels_original'],
                    'human_labels_effective': human['human_labels_effective'],
                    'effective_human_source_support': human['effective_human_source_support'],
                    'original_source_label_eligible_for_scoring': human.get('original_source_label_eligible_for_scoring', True),
                    'confirmed_scope': {k:human.get(k) for k in ('label_scope_status','knowledge_selection_excluded',
                        'evaluation_kind','fact_generation_eligible','definition_reuse_resolution_hash',
                        'original_subject_error_confirmed','label_correction_event_hash','original_candidate_factual_gold')},
                    'machine_source_consensus': model_row[3], 'machine_ontology_guard': model_row[4],
                    'decision_snapshot': {'audit_id': decision.get('audit_id'), 'revision': decision.get('revision'),
                                          'source_support_result': decision.get('source_support_result'),
                                          'source_support_consensus': consensus.get('source_support_consensus'),
                                          'eligible_voter_ids': consensus.get('eligible_voter_ids'),
                                          'reason_codes': decision.get('reason_codes')},
                    'diagnosis': {'category': diagnosis[0], 'mechanism': diagnosis[1], 'recommended_action': diagnosis[2],
                                  'certainty': diagnosis[3]}, 'votes': compact_votes, 'evidence_bundle': bundle,
                    'pending_human_followup': False,
                    'remaining_scope_disagreement': sid == 'DEV-002',
                    'development_subject_revision': subject_revision if sid == 'DEV-022' else None})

assert any(v['core_check_citation_omissions'] for v in records[2]['votes'] if v['voter_id']=='judge_b')
v26 = next(v for v in records[25]['votes'] if v['voter_id']=='judge_c')
assert 'ev_b04e8e553504d4332197' in v26['counter_evidence_ids']
assert any('反证不成立' in c['note'] for c in v26['checks'])
assert text[10260:10283] == '图1-6表示了超声速弹丸飞行中所形成的弹道波。'
assert len(records) == 30
summary = {'checked_at_beijing': datetime.now(timezone(timedelta(hours=8))).isoformat(),
           'samples': 30, 'raw_human_source_counts': dict(Counter(r['human_labels_original']['来源支持'] for r in records)),
           'corrected_workbook_source_cells_counts': dict(Counter(r['human_labels_effective']['来源支持'] for r in records)),
           'corrected_workbook_factual_cells_counts': dict(Counter(r['human_labels_effective']['事实表述正确'] for r in records)),
           'scoped_original_source_gold_counts': dict(Counter(r['effective_human_source_support'] or 'not_scored' for r in records)),
           'machine_source_counts': dict(Counter(r['machine_source_consensus'] for r in records)),
           'ontology_guard_counts': dict(Counter(r['machine_ontology_guard'] for r in records)),
           'unresolved_primary_groups_mutually_exclusive': unresolved_groups,
           'unknown_polarity_samples': [r['sample_id'] for r in records if r['original_triple']['qualifier_status']['polarity']=='unknown'],
           'missing_relation_ids': [r['sample_id'] for r in records if not r['original_triple'].get('relation_id')],
           'locator_display_defects': ['DEV-004','DEV-011','DEV-012','DEV-014','DEV-025'],
           'raw_source_disagreement_with_supported_consensus': [r['sample_id'] for r in records if r['machine_source_consensus']=='supported' and r['human_labels_original']['来源支持']!='支持'],
           'effective_source_disagreement_with_supported_consensus': [r['sample_id'] for r in records if r['machine_source_consensus']=='supported' and r['effective_human_source_support'] not in {None,'supported'}],
           'user_confirmed_label_corrections': ['DEV-007','DEV-011'],
           'user_confirmed_subject_revision': 'DEV-022',
           'pending_user_explanations': [], 'remaining_scope_disagreements': ['DEV-002'],
           'annotation_scope_version': 'v4', 'annotation_scope_hash': annotations['scope']['scope_hash'],
           'human_correction_event_hash': correction_event['event_hash'],
           'corrected_workbook_sha256': workbook_proof['corrected_sha256'],
           'source_workbook_sha256': workbook_hash, 'integrity': integrity,
           'original_gold_accuracy_computable': False, 'population_accuracy_computable': False,
           'source_file_modified': False, 'production_written': False, 'diagnosis_generation_model_calls': 0,
           'development_source_reaudit_performed': True}
assert summary['corrected_workbook_source_cells_counts'] == {'支持':28,'证据不足':1,'反驳':1}
assert summary['corrected_workbook_factual_cells_counts'] == {'是':27,'否':3}
assert summary['effective_source_disagreement_with_supported_consensus'] == ['DEV-002','DEV-013']
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'case_diagnoses.json').write_text(json.dumps({'summary':summary,'cases':records},ensure_ascii=False,indent=2),encoding='utf-8')

lines = ['# 30条样本错误原因分析', '',
 '已对照原标注、冻结候选、固定三席原始返回、原文坐标及用户最新确认逐条核对。007、011的标注错误已更正到独立校正版；022已按用户指令修订主语并完成新版来源复审。原填写文件和生产库未改。', '',
 '主要问题集中在对象省略、句型/角色抽错、来源与本体检查串用、引用不完整、反证字段冲突，以及任务收录范围。原30条里10条来源共识未闭合，不能统一记成10条错误拒绝。', '',
 '## 一、原始分布与口径', '',
 '- 30条、240项核心人工标注均已填写；原文件哈希未变。',
 '- 原填写来源：26支持、3反驳、1证据不足；冻结程序来源：20 supported、10 unresolved。原4条人机分歧为002、007、011、013；007和011已由用户确认是标注错误，更正后仍需区分002的评价范围分歧与013的对象省略。',
 '- 校正版E列字面统计为28支持、1反驳、1证据不足，L列为27是、3否。022的原E列“支持”保留并加注，但用户已确认原主语错误，不再作为原候选的支持真值；按v4实际可计分来源标签为27支持、1反驳、1证据不足、1未计分。两套统计不能混用。',
 '- L列是建议修改后的表述判断；013后续说明针对原候选漏“弹丸”；017后来确定复用定义；028、029是本次任务不收录。几种口径不能拼成原候选准确率。',
 '- I/K列是概念能否表达，不是正式ID与约束是否齐全。本体检查11通过、17未知、2失败，不能据此称人工19条判错。18条未指定关系ID，其中15条H仍填“是”；H的正式ID口径尚未单独确认。', '',
 '## 二、已定位的主要机制', '',
 '| 问题 | 样本与证据 | 原因与处理 |', '|---|---|---|',
 '| 对象省略、关系/值拆分错误 | 013漏“弹丸”；020把章动角/攻角与量词放进关系，尾值只留“很小” | 模型在段落里补足语义，结构化结果却没有完整携带对象与属性名。审核要同时检查来源支持和导出表达自足性。 |',
 '| 推导限定与适用范围没有完整落到字段 | 004；018还存在比例关系条件分歧 | 条件可能修饰公式、分支或某个并列项。不能只取主干句，也不能把相邻条件扩散到所有关系。004已提出保留推导假设的受限版本。 |',
 '| 定义、分类、部件及事件角色混淆 | 017“统称”被抽作部件；022把投影结果当投影对象；030混用“称为”与“是” | 同一词语出现不等于主谓宾角色相同。017保留定义证据；022已按用户确认修正主语，新版来源三席支持，原两席反对有依据。 |',
 '| 来源方向被本体缺口拖成unknown | 020有原始Qwen检查说明；017/030也涉及句型和正式映射 | 原文方向与正式关系约束分开判断。本体缺失不能单独成为原文方向未知的理由；后续020的新提示版本完整三席对照已修复此类阻塞。 |',
 '| 检查pass但缺少证据ID | 003 Gemma qualifiers=pass、evidence_ids=[] | 输出被当作合法uncertain票保存，聚合又不计其来源票，停在2/3。应让核心检查的引用要求与聚合口径一致，缺引用走有定位的契约补正，不能自动补ID或降门槛。 |',
 '| 已排除的反证仍列在实质反证数组 | 026 DeepSeek说“反证不成立”，counter_evidence_ids仍含ev_b04… | “已核查的候选反例”和“仍成立的反证”字段含义混用。聚合保守阻断有理由，不能自动删掉反证；需向原模型澄清并做字段一致性校验。 |',
 '| 词面匹配与按约束猜类型 | 003“表示”回落到空气阻力函数的属性；建议把弹道系数改成该domain类 | 关系同名不等于语义等价，domain/range是校验约束，不能反过来证明实体类型。 |',
 '| 来源支持与知识收录范围混用 | 028、029 | 原文确有公式/坐标设定不代表本次图谱要收录；用户已确认这两条排除，属于任务筛选，不是自动的事实错误。 |',
 '| 样本展示缺陷 | 004、011、012、014、025主表直接引文只有$$ | 是导出选句逻辑的问题，完整证据表未丢失。不能把它算成抽取错误，也不能据此直接判人工标签错误。 |', '',
 '## 三、10条未形成来源共识的拆解', '',
 '这是冻结原候选的分组，不随新版覆盖。按主要阻塞原因分组，下表互不重复；一个样本仍可能有次要问题。例如原022同时有极性unknown，开发第2版已显式修订并完成来源复审。', '',
 '| 主要阻塞 | 样本 | 数量 |', '|---|---|---:|']
for reason, ids in unresolved_groups.items(): lines.append('| '+reason+' | '+'、'.join(ids)+' | '+str(len(ids))+' |')
lines += ['',
 '程序来源票还要求三个来源检查有引用、表达字段闭合、没有未解决实质反证。因此“三个source_support字段都是supported”并不等于完整来源共识已通过。保留这些要求，同时应把阻塞原因准确暴露出来。', '',
 '## 四、已确认的更正与剩余分歧', '',
 '- DEV-007：用户确认原标注错误，来源支持改为“支持”，事实表述正确改为“是”。原模型来源支持判断正确。',
 '- DEV-011：同样更正为“支持、是”；原模型来源支持判断正确。正式关系映射仍待处理，不能把标注更正等同于正式准入。',
 '- DEV-022：用户确认修正主语为“矢量方程（1-56）”。开发第2版表述为“在前述基本假设条件下，矢量方程（1-56）投影于直角坐标系”，新版来源审核3/3支持。原两席指出角色错误有依据。修改审查保留2赞成、1反对；依用户明确指令人工保存修订，未改写反对票。',
 '- DEV-022正式映射：旧头类型是微分方程组，既有投影属性的domain也指向微分方程组，不能沿用到新主语。开发稿已清空head_type、relation_id；当前仍为revision_required，尚未正式准入或写入生产图谱。',
 '- DEV-002：人工证据不足与原文直接出现“最后再根据状态方程…得到H(y)”存在评价范围差异，需连同上游分布/推导上下文复核。', '',
 '用户原话“007和011标注错误。修正主语”已记录于human_correction_event.json。原表完整保留，校正版仅改6个单元格：007/011的E、L列及022的M、N列。B列冻结原候选、历史模型票均保留。', '',
 '## 五、30条逐条结论', '',
 '| 样本 | 主要定位 | 证据强度/状态 | 后续处理 |', '|---|---|---|---|']
for r in records:
    x = r['diagnosis']; lines.append('| '+r['sample_id']+' | '+x['category']+' | '+x['certainty']+' | '+x['recommended_action']+' |')
lines += ['',
 '## 六、建议的修正顺序', '',
 '1. 先处理无需争论领域知识的契约问题：003的pass缺引用、026的反证角色矛盾、恢复后决策摘要与最新票目录不同步。保留原票，不自动加引用或删反证。',
 '2. 规范抽取表达：013的对象所属者、020的属性名/取值切分、005/006/018的未知极性；016拆并列成分时保留局部条件。022开发稿已修主语和极性并通过来源复审。所有语义变动另走修订与新版本审核。',
 '3. 处理正式映射：022新主语类型及投影属性适用范围、003的错误同名关系、001/030的比较或命名关系；017保持定义复用，不再生成部件三元组。',
 '4. 把028/029的知识收录策略与来源真实性分开记录。补充展示正文，以v4记录007/011已确认标注更正、022旧来源真值停用及新稿独立状态。',
 '5. 后续同输入对照只比较已定义的来源、限定、方向、映射和任务取舍分项。30条是定向开发集，不外推全章；未获得新修订稿人工真值时，不报告修订后人工准确率。', '',
 '## 七、与本轮30条分开的执行问题', '',
 '原冻结样本的90张模型返回均已成功保存。随后020扩展实验的Nginx 504、能力核验引文不精确和发布门槛未闭合，属于后续执行/协议问题，不能回填成原30条事实反驳。最新情况见../dev020_release_20261103/DEV-020处理结果.md。', '',
 '源表：'+str(BOOK), '源表SHA-256：'+workbook_hash,
 '结构化逐条依据：case_diagnoses.json。原文重点片段：重点分歧与原文.md。', '',
 '代码位置：review_agent/consensus.py的来源检查引用和counter_evidence_ids门槛；review_agent/ontology_guard.py的表面词关系回落；review_agent/response_contract.py的输出交叉一致性校验。']
(OUT/'30条样本错误原因分析.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

exhibits = ['# 重点分歧与原文', '', '所有原文按冻结文档字符坐标校验；原标签与用户确认后的有效标注分别保留，避免覆盖历史。', '']
for r in records:
    if r['sample_id'] not in {'DEV-002','DEV-003','DEV-004','DEV-007','DEV-011','DEV-013','DEV-017','DEV-018','DEV-020','DEV-022','DEV-026','DEV-028','DEV-029'}: continue
    t = r['original_triple']; x = r['diagnosis']
    exhibits += ['## '+r['sample_id'], '', t['head']+' → '+t['surface_relation']+' → '+t['tail'],
                 '原限定：'+json.dumps(t['qualifiers'],ensure_ascii=False),
                 '原人工：'+json.dumps(r['human_labels_original'],ensure_ascii=False),
                 '有效标注：'+json.dumps(r['human_labels_effective'],ensure_ascii=False),
                 '原候选来源计分值：'+str(r['effective_human_source_support']), x['mechanism'], '']
    for e in r['evidence_bundle']['items']:
        if len(e['quote'].strip()) < 5: continue
        exhibits += [e['evidence_id']+'，['+str(e['start'])+', '+str(e['end'])+')', '', '> '+e['quote'].replace('\n','\n> '), '']
    for v in r['votes']:
        if v['core_check_citation_omissions']: exhibits += [v['voter_id']+' 缺引用：'+json.dumps(v['core_check_citation_omissions'],ensure_ascii=False), '']
        if r['sample_id']=='DEV-026' and v['voter_id']=='judge_c':
            exhibits += ['DeepSeek实际反证数组：'+json.dumps(v['counter_evidence_ids'],ensure_ascii=False),
                         'DeepSeek反证检查：'+json.dumps(next(c for c in v['checks'] if c['check']=='counterevidence'),ensure_ascii=False), '']
(OUT/'重点分歧与原文.md').write_text('\n'.join(exhibits)+'\n',encoding='utf-8')
assert hashlib.sha256(BOOK.read_bytes()).hexdigest() == workbook_hash
print(json.dumps({'samples':30,'unresolved_primary_groups':unresolved_groups,'unknown_polarity':summary['unknown_polarity_samples'],
                  'missing_relation_id_count':len(summary['missing_relation_ids']),'source_workbook_modified':False,'model_calls':0},ensure_ascii=False))
