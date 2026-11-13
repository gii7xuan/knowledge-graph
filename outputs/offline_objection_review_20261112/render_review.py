"""Render the authored decisions; never edit ontology, votes, or model ledgers."""
from collections import Counter
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def read(name):
    return json.loads((HERE / name).read_text(encoding='utf-8'))


def write(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    records, terms = read('per_id_review.json'), read('term_review.json')
    evidence = {x['evidence_id']: x for x in read('source_evidence.json')}
    source = (HERE / 'frozen_source.md').read_text(encoding='utf-8')
    summary = read('review_summary.json')
    labels = {'keep': '保留', 'revise': '修订', 'hold': '暂缓',
              'hold_source_ambiguity': '暂缓：来源冲突或版面疑点',
              'hold_scope_ambiguity': '暂缓：ID 指称范围待定',
              'hold_existing_review': '暂缓：沿用既有异议及未知请求'}
    by_id = {x['schema_id']: x for x in records}
    lines = ['# DeepSeek 130 项异议离线逐项复核', '',
             '审阅者为本会话 AI；这是逐项语义标注，不是独立人工金标准，也不是委员会新票。', '',
             '冻结原文、当前定义、父类及 domain/range 均纳入判断；同名关系按 ID 分开核验。保留表示本条异议未充分成立，不表示完成所有公理证明。修订包括定义元数据缺失，不能全部解释为事实错误。', '']
    for term in terms:
        lines += [f"## {term['number']:03d} {term['term']} — {labels[term['recommendation']]}", '']
        for identity in term['schema_ids']:
            row = by_id[identity]
            lines += [f"### {identity}", '',
                      f"结论：{labels[row['recommendation']]}。问题类型：`{row['issue_kind']}`。", '',
                      f"判断：{row['reason']}", '',
                      f"当前定义：{row['current_definition'] or '（缺失）'}", '']
            if row['schema_kind'] == 'class':
                lines += [f"父类：{row['current_parent_label'] or '（未声明）'}；ID：`{row['current_parent'] or '无'}`。", '']
            else:
                lines += [f"domain：{'、'.join(row['current_domain_labels']) or '（未声明）'}；range：{'、'.join(row['current_range_labels']) or '（未声明）'}。", '',
                          f"端点 ID：`{','.join(row['current_domain'])}` → `{','.join(row['current_range'])}`。", '']
            if row['unapproved_definition_draft']:
                lines += [f"未批准定义草案：{row['unapproved_definition_draft']}", '']
            else:
                lines += ['未生成新定义草案；具体原因见上述判断。', '']
            for eid in row['evidence_ids']:
                ev = evidence[eid]
                line = source[:ev['start']].count('\n') + 1
                lines += [f"证据 `{eid}`；字符区间 [{ev['start']}, {ev['end']})；[冻结原文第 {line} 行]({(HERE / 'frozen_source.md').as_posix()}:{line})。", '',
                          '```text', ev['quote'], '```', '']
    (HERE / 'term_review.md').write_text('\n'.join(lines), encoding='utf-8')

    specs = {
        'R_2a66075367666d54e7e7': {'priority': 'P1', 'repair': '保留消耗的语义，候选 range 改为动能；复核空气/黏性过程是否需要细化。', 'candidate_range': ['C_a7877ae0fe8183f8b986']},
        'R_e1de72d5c68b765c28b9': {'priority': 'P1', 'repair': '若保持“具有部件”，候选方向应为分离波→正激波；另核斜激波是否已有相应属性。若用逆关系，必须明确命名并重新核验。', 'candidate_domain': ['C_c89817eb0fc6a2a0b963'], 'candidate_range': ['C_426d7ce8c711071adac8']},
        'R_cee80a3f175ec841dd1f': {'priority': 'P1', 'repair': '拆开力作用于质心和方程描述运动两种关系；查既有重力/阻力属性复用，不能仅用注释保留质心→方程组的“作用于”。'},
        'R_5da207e3fa51409de7b9': {'priority': 'P1', 'repair': '核式（1-56）矢量运动方程作为投影输入、坐标系作为表示背景、式（1-57）作为结果；当前结果类不应充当输入。先查现有方程 ID，再定补丁。'},
        'R_825f6e2698a7ae67d160': {'priority': 'P1', 'repair': '用“在该层适用的分段规律”等角色表达数学分布与物理区域关系；不要保留“对流层属于温度分布”的类属读法。'},
        'R_23c30368744e610b9a5c': {'priority': 'P1', 'repair': '表达式（1-100）描述横风偏与退电关系；先查公式/关系表达对象，不能只对调横风偏和退电。'},
        'R_1019d420d55d6c331f64': {'priority': 'P1', 'repair': '区分两射击面的夹角和夹角对速度投影的依赖，现有两平面端点不足以表达依赖项。'},
        'R_3c75dd89923bc714630d': {'priority': 'P2', 'repair': '评估将依赖项指向头部形状，或明确对象特征角色；同时保留阻力大小及超声速条件。', 'candidate_range': ['C_cbcb15d49fba77017748']},
        'R_8c8d928b1aa9b0f97bd4': {'priority': 'P2', 'repair': '用编表方法、编表用途或适用火炮等角色重新表达；不得把高射火炮当作编表施事。'},
        'R_4f50d9e9384ae5c2980a': {'priority': 'P2', 'repair': '把气象条件表达为修正依据，澄清修正的是射击参数/计算而非火炮实体；检查既有参数概念。'},
        'R_bacd990da3482215545f': {'priority': 'P2', 'repair': '区分超声速对象、头部形状特征和减阻设计目的；来源不足以形成任意超声速弹丸都会减阻的关系。'},
        'R_69d8ebeb21191bb11194': {'priority': 'P2', 'repair': '先核外弹道是否被本 ID 用作学科简称，并查外弹道学现有 ID；澄清研究主体后再定 domain。'},
        'R_5848ec7e02661adeae06': {'priority': 'P2', 'repair': '核加速度作为方程项/方程描述量的角色；不要保留加速度描述方程的施事读法。'},
        'R_fe5696de2034d33084f1': {'priority': 'P2', 'repair': '明确运动方程在自然坐标中表示的角色，再决定谓词及方向；此项先消歧，不直接判物理事实为假。'},
        'R_37918d1714e9945397f3': {'priority': 'P2', 'repair': '限定空气阻力函数对运动模型中阻力项的贡献，避免一个分量代表全部弹丸运动。'},
        'R_5eb753682c8d887575fc': {'priority': 'P2', 'repair': '结合 DEV-019 旧记录，表达地炮弹道计算所需顶点和落点诸元；区分火炮、计算任务和参数，不自动扩大顶点端点。'},
        'C_afb01b87134088624d72': {'priority': 'P2', 'repair': '按本章阻力表达式改写无量纲系数定义，区分阻力、空气密度及阻力系数，不将系数解释成力。'},
        'C_88ecbdb51d6e44007aec': {'priority': 'P2', 'repair': '解除“某一恒定风模型中的动系可视为惯性系”到全称父类的跳跃；先复核父类，不只改定义。'},
        'C_ad498a2e8a3eb22fdd25': {'priority': 'P2', 'repair': '先界定坐标系通用 ID 与动坐标系子类，当前定义仅覆盖随风运动的坐标系；不得无依据合并 ID。'},
    }
    semantic = [x for x in records if x['declared_semantics_needs_review'] and x['recommendation'] == 'revise']
    assert set(specs) == {x['schema_id'] for x in semantic}
    candidates = []
    for row in semantic:
        candidates.append({'schema_id': row['schema_id'], 'term': row['term'], 'issue_kind': row['issue_kind'],
            'current': {k: row[k] for k in ('current_definition', 'current_parent', 'current_parent_label', 'current_domain', 'current_domain_labels', 'current_range', 'current_range_labels')},
            **specs[row['schema_id']], 'evidence_ids': row['evidence_ids'], 'approved': False, 'applied': False,
            'executable_patch': False})
    write('structural_revision_candidates.json', {'basis': read('input_manifest.json'),
        'count': len(candidates), 'warning': 'Repair specifications only; candidate endpoints are not approved RDF operations.',
        'candidates': sorted(candidates, key=lambda x: (x['priority'], x['schema_id']))})
    counts = dict(Counter(x['issue_kind'] for x in records))
    plan = {'annotated_by': 'ai', 'human_gold': False, 'model_api_calls': 0, 'applied': False,
        'order': [
            {'step': 1, 'scope': '19 语义/结构项', 'action': '先确定端点、方向、角色及父类；不要把错误结构包装成补定义。', 'artifact': 'structural_revision_candidates.json'},
            {'step': 2, 'scope': '80 未批准定义草案', 'action': '结构修订后按对应 ID 合并草案、消解同名及近义复用；不机械新增属性。', 'artifact': 'unapproved_definition_drafts.json'},
            {'step': 3, 'scope': '6 暂缓项', 'action': '核原书版面、指称范围及既有任务，保留冲突和未知请求，未解决则继续暂缓。'},
            {'step': 4, 'scope': '新冻结提案', 'action': '重新编译并记录新的代码/材料哈希，离线核验 delta；当前批次不发审核请求。'},
            {'step': 5, 'scope': '后续另行授权的审核', 'action': '新提案需要三席建模审核和三席整章覆盖审核；旧票保留为历史，不能移植为新提案批准。'}],
        'issue_kind_counts': counts,
        'outside_scope_ids_count': len(summary['outside_scope_ids']),
        'frozen_full_chapter_question_limitation': '现有整章问项只有一个总问句，未强制逐 ID 覆盖。后续应提供全量清单及每项证据/语义/结构/范围判断，并在本地核验清单覆盖。当前130项复核不等于全部270项认证。'}
    write('revision_plan.json', plan)
    draft_types = Counter(by_id[x['target']]['schema_kind'] for x in read('unapproved_definition_drafts.json')['operations'])
    report = f'''# 130 项异议复核与客户端日志改进

已完成本轮第 1、2 项：离线核验 DeepSeek 的 130 个异议标签，并给实际模型客户端补齐 HTTP/SSE 观测。新增真实模型调用为 0。

## 异议复核结果

|口径|保留|修订|暂缓|合计|
|---|---:|---:|---:|---:|
|异议标签|40|84|6|130|
|独立本体 ID|44|116|6|166|

130 标签映射到 86 个类和 80 个关系 ID。同名关系分别判断，不能只按44个关系名称投一票。已绑定130段逐字相等的原文证据，包含字符区间、文档版本及索引哈希。

“修订”包含元数据缺失、定义范围不全和结构角色问题，不能将116项都称为事实错误。“保留”表示本条异议未充分成立；其中8项只是保留原有未批准关系定义草案，不能视作正式发布。87个 ID 在该冻结合并快照中缺定义，不能因此推断概念无来源。

已列出19项优先语义/结构修订规格，并生成80项未批准定义草案（类 {draft_types['class']} 项、关系 {draft_types['object_property']} 项）。二者有交叉，不能相加为新增本体项。尚有104个原本体 ID 不属于这130个异议的映射范围，未在本轮获得完整逐项认证。

## 优先处理的问题

1. `R_2a66075367666d54e7e7` 的“消耗”应复核动能对象，当前 range 是弹丸实体。
2. `R_e1de72d5c68b765c28b9` 的“具有部件”方向与分离波由正激波、斜激波组成的原文相反。
3. 投影、作用于、描述、夹角取决于等属性存在公式、结果、受力对象或依赖项角色错位，补定义不足以修复。
4. 高射火炮编表、气象修正等原句中的适用对象或依据被当成施事，应先确定关系角色。
5. 阻力系数、坐标系和惯性参考系存在定义或父类范围问题，需要定义与结构联动核验。

`R_fbc54f6800679e25d95d` 当前 range 为科氏加速度，与本章相符；不应为 DEV-028 错误候选“地面曲率”放宽约束。弹丸与空气的黏附关系须明确方向约定，不能只凭词面倒转。

6项暂缓分别是：中间层、亚同温层的原文术语冲突；同温层父类疑似断行误分；标准定律、标准分布的通用名称与特定函数指称；横风影响弹道关系的条件范围与既有未知审核。均保留原证据和旧记录。

## 交付物

- [130项完整逐项报告]({(HERE / 'term_review.md').as_posix()})：每个ID的现状、判断、证据和草案。
- [166个ID结构化标注]({(HERE / 'per_id_review.json').as_posix()})、[逐字证据]({(HERE / 'source_evidence.json').as_posix()})。
- [19项结构修订规格]({(HERE / 'structural_revision_candidates.json').as_posix()})、[80项未批准定义草案]({(HERE / 'unapproved_definition_drafts.json').as_posix()})。
- [后续修订顺序]({(HERE / 'revision_plan.json').as_posix()})、[客户端日志说明]({(ROOT / 'docs/model_transport_diagnostics_20261112.md').as_posix()})。
- [交付校验]({(HERE / 'delivery_verification.json').as_posix()})、[回归测试结果]({(HERE / 'regression_test_result.json').as_posix()})。

## 边界及后续

本轮由本会话 AI 按证据和本体逐项判断，`annotated_by=ai`、`human_gold=false`。没有伪造人工金标准，没有变更旧委员会票或将 DeepSeek fail 改为 pass。未改生产本体、未生成发布版本，累计实际模型调用仍为13次；4条正式未知请求继续保留未知状态。

日志代码已经改变，因此不能沿用旧冻结材料中的代码指纹开启新审核。后续应先处理结构和暂缓项，再合并定义并生成新的冻结提案；新提案重新满足三席建模和整章覆盖要求。本轮结果不能替代这两项发布门禁。
'''
    (HERE / 'report.md').write_text(report, encoding='utf-8')
    print(json.dumps({'full_report_terms': len(terms), 'full_report_ids': len(records), 'structural_candidates': len(candidates), 'draft_types': dict(draft_types)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
