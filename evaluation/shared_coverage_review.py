"""Complete the source-bound 40-case coverage audit without requests or publication.

NOTES and FAMILIES are explicit engineering judgments by the coding agent after
reading the frozen source statements and complete relation catalogue. They are
not an automated similarity classifier, human gold or independent model votes.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

from .freeze_shared_ontology import ROOT, read, write
from review_agent.contracts import sha256_json

POOL = ROOT / 'outputs/development_pool_20261117'
PRE = ROOT / 'outputs/preannotation_followup_20261118'
DISPUTES = ROOT / 'outputs/priority_disputes_20261118'
VERSION = 'shared_ontology_coverage_agent_review_v1'

# Aliases identify actual frozen properties, not proposed formal IDs.
R = {
    'has': 'R_b374d309647377365f38', 'classifies': 'R_fa903edd5e42396ce95e',
    'approximates': 'R_406bc04a6711460b8da5', 'obtains': 'R_5cfeb014dc68c80c2f5c',
    'represents': 'R_37918d1714e9945397f3', 'describes': 'R_fe5696de2034d33084f1',
    'based_on': 'R_398398ebed85b2bb150f', 'causes': 'R_5d7591372f1859dc86e0',
    'pressure_effect': 'R_12a1cf5c80d7e73a97a2', 'changes': 'R_5db05ead52149bdae580',
    'ignores': 'R_5550f67bcf4a36b3531f', 'uses': 'R_34c14554de9690a0bac8',
    'parts': 'R_3a4d4e6372b2170ec6a1', 'follows': 'R_b6c9f202b6f2fb55af5d',
    'acts_on': 'R_9aa34c7a4f252989587e', 'forms': 'R_0e6aeaed149fdb07cfac',
    'depends': 'R_3c75dd89923bc714630d', 'reflects': 'R_fe176660a681f17dc9db',
    'changes_with_height': 'R_029d377fdd30415aa4e3', 'corresponds': 'R_0aba3d423b601977e28c',
}

# key: label, proposed role signatures, restrictions. These are requirements,
# not an executable ontology patch or authority to widen any existing property.
FAMILIES = {
 'interpretation': ('带归属的局部解释', [('hasAttributedCharacterization','概念','来源解释命题')],
     '解释命题节点记录作者/引述、肯定或否定、Think of it as 与原始跨度；不升级为恒常本质属性。'),
 'introduction': ('教材篇章介绍', [('isIntroducedInPassage','教材概念组','来源篇章')],
     'work and energy 的组合范围及本书前文位置保留；学科不是施事，篇章位置不作物理时间。'),
 'classification': ('带范围和条件的分类/判据', [('hasScopedClassification','受限对象或对象组','模型或分类判据')],
     '区分 thermally perfect、Newtonian、simple 的分类体系；can、Many、all 和条件挂到命题，禁止生成无条件全称 subclass。'),
 'equation_condition': ('方程满足或适用命题', [('hasEquationConstraint','流动情境','限定方程命题')],
     '限定方程身份、适用条件与极性；不把不符合方程解释为能量不守恒。'),
 'limit_condition': ('理想行为的趋近条件', [('hasApproachCondition','模型行为','条件命题')],
     '保留 approached 的程度与条件，不改成等价、无条件分类或气体组成断言。'),
 'derivation': ('有输入和范围的推导/表达关系', [('isResultOfDerivation','方程或量','推导描述'),('expressesLaw','方程描述','定律概念')],
     '推导节点分别记录输入、操作、输出、适用范围；不把一输入描述升级为唯一输入，不倒转结果与操作。'),
 'unit': ('量的单位/量纲文本', [('hasUnitExpression','量的单位说明','单位文本字面量')],
     '单位说明不是测量值；保留文本、datatype 规范化待办，不凭量纲填入数值或换算。'),
 'analysis_requirement': ('局部分析的考虑要求', [('requiresAccountingFor','分析情境','能量考虑命题')],
     '保留 we 的局部主体、Because of this 的先行说明和 must；不建物理因果边。'),
 'designation': ('对象的局部称谓/角色', [('isDesignatedInContext','控制体边界对象','控制面角色')],
     '控制体端面被指定为控制面；命名或角色不等于物理因果，也不自动成为全体边界的分类。'),
 'symbol': ('符号的局部表达式定义', [('hasDefiningExpression','算子符号','表达式文本字面量')],
     '保留向量/算子排版证明与书页；字符串保存不意味着完整数学结构已建模。'),
 'metric': ('量与所表征强度的局部约定', [('characterizesInContext','量比值','强度概念')],
     '压力比作为该段激波强度的表征；不建压力与强度的无条件 owl:sameAs 或类等价。'),
 'functional': ('变量函数关联和物质依赖', [('participatesInFunctionalRelation','变量组','函数关系描述'),('variesWithSubstance','函数关系描述','物质条件')],
     '保留 some 的变量集合与不同物质的依赖；函数关系不是因果边，不能省略多元角色。'),
 'law_statement': ('定律的来源陈述', [('statesProposition','定律概念','来源命题')],
     '命题保留作者原文与范围；不凭常识补恒压/恒温等未提供条件，也不将原文简述提升为普遍物理定律新证明。'),
 'causal': ('来源支持的因果及比较命题', [('causesInContext','原因事件描述','结果事件描述'),('hasCauseInContext','结果量描述','原因事件描述')],
     '034 保持 effect→cause 方向，015 保持比较级及多结果共同陈述；不自动拆分为每个独立因果，不将 more 当普通数量词。'),
 'analysis_structure': ('分析使用对象和局部结构假设', [('usesControlVolume','分析情境','控制体对象'),('hasStructuralAssumption','分析情境','带角色及数量的假设命题')],
     '分析者与参考系场景区分；一入口且一出口的角色与 AND 保留，不能变成所有系统的结构。'),
 'capacity': ('受限定的承载能力', [('hasLoadCapacityDescription','流体对象组','负载能力命题')],
     '保留 can 与 little or no、剪切和拉伸的组合；不自动变成完全无承载能力的否定。'),
 'density': ('密度的时空约束', [('hasDensityConstraint','流体情境','密度约束命题')],
     '空间各处相等和时间不变分别保留；常数不是已知数值，流体密度不能无证据指定为空气密度。'),
 'term_meaning': ('方程项含义及方程未表达内容', [('hasTermInterpretation','有来源身份的方程项','解释命题'),('doesNotSpecifyInContext','有来源身份的方程','未指定内容命题')],
     '精确绑定方程号/项；否定指定内容不是否定该物理量存在，material 不自行改写为累计质量。'),
 'event_description': ('事件及其方式、尺度或倾向', [('hasScopedEventDescription','参与对象','事件描述')],
     'burst/develop/bend 为事件，quickly 和 very small distance 是方式/尺度，tend to 是倾向；不同事件不通过共享关系被判同义。'),
 'principle_constraint': ('原理对状态描述数目的约束', [('isSpecifiedByPrinciple','独立性质数目概念','状态公设概念')],
     '不把所给两性质的局部简单系统假设推广到所有物质，也不将公设当数值。'),
 'premise': ('推导前提', [('hasPremise','推导情境','前提命题')],
     '有势质量力与定常且（正压或不可压缩）属于本段推导条件，保留 AND/OR 及沿流线范围。'),
 'applicability': ('定律的系统范围', [('hasApplicationScope','定律表述','系统情境')],
     '固定质量和经历过程限定到同一系统；不是系统必然发生该过程的事件事实。'),
 'notation_norm': ('局部记号规范要求', [('hasNormativeNotationRequirement','记号惯例','要求命题')],
     '保留 would require 与作者忽略惯例的后文；δ 双划按原页保留，未知模态不改 asserted。'),
 'concept_alias': ('概念身份与别名路线', [],
     '先澄清概念身份，再评估现有类 labels/aliases 元数据；不因 is known as 就新建万能关系，不生成未经核证的 sameAs。'),
}

# n, family, prerequisite route, meaning, per-case preservation/rejection reason,
# closest frozen property aliases, related existing class labels.
NOTES = [
 (1,'interpretation','expression_scope_first','covolume 在作者解释口吻下不被赋予物理意义',
  '保留原主谓宾及否定，采用上轮最小范围方案；首版 this passage 草案的反对意见保留，不复用其审批。','has reflects',''),
 (2,'introduction','expression_scope_first','本书前文介绍功与能这组主要热力学概念',
  '修正 introduced 的施事；earlier sections 是篇章位置；不能用外弹道研究关系，也不能把 work and energy 整体指定为动能。','uses','动能'),
 (3,'classification','extension_requirements','条件下气体可按 thermally perfect 模型考虑',
  '不能以理想气体标签替代 thermally perfect；既有近似看成仅空气→理想气体，属于仅名称/建模方式相近。原占位符另走已建技术草案。','approximates classifies','理想气体 空气'),
 (4,'equation_condition','expression_scope_first','实际流体流动与本书式3.9的否定符合关系',
  '补证已定位3.9及黏性机械能损失说明；补齐该方程的局部身份与范围，不把否定泛化到所有伯努利形式。','describes represents','微分方程'),
 (5,'limit_condition','extension_requirements','所述分子间距条件下趋近理想气体行为',
  'approached 是带条件的趋近，不能改成空气属于理想气体；保留作者对 mean free path 的原文说明，不另作物理纠错。','approximates','理想气体'),
 (6,'derivation','extension_requirements','方程是指定截面积分平均操作的结果',
  '得到关系仅状态方程→空气密度函数，不能借用；结果到操作的方向、原式身份和有限过流截面条件均须保留。','obtains based_on','微分方程'),
 (7,'unit','extension_requirements','covolume 单位的文本说明',
  '本体没有数据属性或单位关系。原 literal 值存在，datatype=unit expression 是待规范的自定义文本；不补测量数值。','has',''),
 (8,'analysis_requirement','expression_scope_first','文中分析必须考虑这些变化所需能量',
  'Because of this 的先行词来自控制体进出物质压力/密度变化；先绑定 we 与这些变化，不能用物理影响或用于关系冒充方法要求。','uses pressure_effect',''),
 (9,'equation_condition','extension_requirements','定常条件下密度时间偏导为零的命题',
  '现有随高度变化是气温→高度；本项是密度与时间约束，条件命题不是气温空间分布。','changes_with_height','微分方程 空气密度'),
 (10,'designation','extension_requirements','该控制体端面被指定为控制面',
  '命名/角色没有现有谓词；CS 的身份局限在该控制体端面。原 polarity/modality unknown 不由覆盖审查自动补肯定。','classifies parts',''),
 (11,'symbol','extension_requirements','哈密顿算子的局部表达式文本',
  '原页已解开句号误附问题；保留新增 PDF 核证，literal value/datatype 方案待审；表示关系限空气阻力函数→弹丸运动。','represents',''),
 (12,'metric','expression_scope_first','压力比在该段被用作激波强度',
  '压力比不是气压，激波强度不是激波实体；is 需按局部表征解释，不能作无限定身份等同。激波类定义局限原有飞行情境。','represents pressure_effect','气压 激波'),
 (13,'functional','extension_requirements','一些变量通过函数关系相关联',
  '不能以影响或取决于取代函数关系；变量组与函数关系的参与角色应显式表示，保留 some，不推因果。','depends pressure_effect','状态方程'),
 (14,'law_statement','extension_requirements','Charles定律在书中陈述体积与温度比例命题',
  '定律→命题，不是气温→马赫数或密度的影响；给定句未陈述其他条件，不能凭常识补入后当来源证据。','describes pressure_effect','气温'),
 (15,'causal','expression_scope_first','分子排列更紧密与碰撞更多、压力更高的比较陈述',
  '既有引起为横风→侧偏，所有形成/产生亦有专门端点；保留 more 的程度和多结果结构，不能自动拆成无条件独立因果。','causes pressure_effect forms','气压'),
 (16,'functional','expression_scope_first','函数关系依物质而变化',
  '当前尾项 substance 的角色需明确为物质条件，不能与函数关系作同类实体关联；既有改变/取决于均无此范围。','changes depends','状态方程 气体状态方程'),
 (17,'analysis_structure','expression_scope_first','文中欧拉参考系分析使用选定控制体',
  '上轮已备施事修订，不能以跟随或描述自然坐标系的关系替换；现有动参考系定义也不等于 Eulerian frame。','follows describes uses','动参考系 惯性参考系'),
 (18,'capacity','extension_requirements','流体承受很小或不承受剪切/拉伸负载的能力描述',
  '作用于只描述重力或空气阻力的特定受力关系，不是承载能力。保留 little or no 与 can，技术占位符和极性未知单独待审。','acts_on',''),
 (19,'density','extension_requirements','不可压缩情境下密度为常数并保持时空限定',
  '常数为定性值；现有空气密度不等于所有流体密度。与038为同源重叠描述，不能算独立支持。','has changes_with_height','空气密度'),
 (20,'term_meaning','expression_scope_first','式2.129右端第二项的原文含义',
  '上轮已从连续原文绑定式2.129，待审修订将尾项改为解释命题；原 CV 实体尾项不足以表示整段项义。','represents describes',''),
 (21,'event_description','expression_scope_first','隔膜能够迅速破裂的事件及方式',
  'very quickly 是方式，不是独立事件尾实体；不扩写设备设计或计算，can 和局部对象保留。','forms',''),
 (22,'principle_constraint','extension_requirements','确定状态所需独立性质数目由状态公设给出',
  '不是状态方程到空气密度函数的得到关系；不能仅凭旁文将所有系统所需性质数一律填二。','obtains based_on','状态方程'),
 (23,'interpretation','expression_scope_first','covolume 在作者解释中作为改善拟合的数',
  '与001同源互补，保留 Think of it as；simply 表示话语限定，不是数量，也不是数学相等。','has reflects',''),
 (24,'analysis_structure','expression_scope_first','本文分析作一入口且一出口的结构假设',
  '保留 assumed、作者局部范围及每个角色的 one；基本假设现有定义是原有弹丸运动假设，不可直接扩作通用系统假设。','parts ignores','基本假设'),
 (25,'term_meaning','extension_requirements','本书式2.40未指定能量在内能与动能间的分配',
  '否定的是方程指定内容，不能用忽略科氏惯性力或描述关系；动能作为词汇相关类无正式定义且不能为整个复合命题定型。','describes ignores','动能'),
 (26,'derivation','extension_requirements','该运动方程是牛顿第二定律在流体力学的表达形式',
  '不是自然坐标系描述特定运动方程组。微分方程/运动方程组类缺定义，仅相关标签不能证明正确类型或律与方程等同。','describes represents','微分方程 运动方程组'),
 (27,'premise','expression_scope_first','有势质量力是指定推导的前提',
  '采用上轮前提方案，保留定常且（正压或不可压缩）和理想流体沿流线推导范围；不能作所有质量力的分类事实。','has classifies','基本假设'),
 (28,'interpretation','expression_scope_first','covolume 被引述为考虑分子尺寸与相互作用的参数',
  'has been described as 的归属不可省去；与001、023同一原段，既不是自动矛盾，也不能把 has 替成弹丸质量系数属性。','has reflects',''),
 (29,'applicability','expression_scope_first','所述第一定律表述的固定质量系统范围',
  '原尾项是范围情境而非独立事件；引用后接方程，不能把 can be written for 当作该系统已发生某过程。','uses',''),
 (30,'event_description','expression_scope_first','激波在很小距离上发展的局部事件描述',
  '现有尾项重复发展，尺度在谓词/quantifier混杂；应分事件与空间尺度。激波类定义的专门情境还需澄清，不能新增计算或优化内容。','forms','激波'),
 (31,'event_description','expression_scope_first','反射语境中特征线趋于弯曲的描述',
  '补证将 characteristics 绑定到本段稀疏波反射语境；bending 不能作无身份实体，tend to 不自动等同频率 usually。','changes',''),
 (32,'derivation','expression_scope_first','比气体常数由具有完整输入的推导确定',
  '原句还给出摩尔质量角色，不能将通用气体常数当唯一输入；仅记录输入角色，不运行或扩写计算。','obtains based_on',''),
 (33,'law_statement','extension_requirements','Boyle定律在书中陈述体积与压力比例命题',
  '定律→命题不是气压→空气密度的影响。未给定条件不凭常识补为已抽取条件，不将局部陈述直接编码为全域数值约束。','describes pressure_effect','气压'),
 (34,'causal','extension_requirements','容器气体压力由分子与壁面碰撞引起',
  '原方向是结果→原因；引起即使考虑逆向也限定横风与侧偏。气压/大气气压类的现有定义没有证明一般容器气体压力属于该范围。','causes pressure_effect forms','气压 大气气压'),
 (35,'concept_alias','class_material_first','物质状态函数关系被称为状态方程',
  '先澄清状态方程与气体状态方程两个既有类的身份及应用性定义，再评估类标签/别名机制；不能直接把一个函数关系实例与类作 sameAs。','represents corresponds','状态方程 气体状态方程'),
 (36,'notation_norm','expression_scope_first','作者说明但为本文目的忽略的记号规范要求',
  '保留 would require 和原页双划；上轮 unknown 编码方案仍阻止完整表达准入。既有忽略仅基本假设→科氏惯性力。','ignores uses',''),
 (37,'concept_alias','class_material_first','质量守恒方程与连续方程的局部同称',
  '目录没有连续方程/质量守恒方程类；可先建有来源定义的概念与别名，未必需新关系。微分方程类缺定义，且仅为更宽层级不能代替该概念。','corresponds represents','微分方程'),
 (38,'density','expression_scope_first','不可压缩情境下密度处处相等且时间不变',
  '原尾项密度与谓词中相等的比较角色不清；需表达密度时空约束，保留各处与时间范围。与019同源，不独立计票。','has changes_with_height','空气密度'),
 (39,'classification','extension_requirements','本文将处理的许多流体为Newtonian的受限分类',
  '保留 Many、本文对象子集及原极性未知；属于仅覆盖杀伤爆破弹→弹丸，不能作为通用 rdf:type 或 subclass 替身。','classifies',''),
 (40,'classification','extension_requirements','全部特征线为直线时称simple wave的判据',
  '条件和 all 必须附到判据；不能因 wave 与弹道波都含波就定型，更不能用形成弹道波的关系当分类判据。','classifies forms','弹道波'),
]


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def check_matrix(rows, property_ids):
    expected = set(property_ids)
    if len(rows) != 40 or len({r['sample_id'] for r in rows}) != 40:
        raise ValueError('all 40 distinct cases required')
    for row in rows:
        items = row['relation_checks']
        if len(items) != len(expected) or {x['relation_id'] for x in items} != expected:
            raise ValueError('each case must cover each frozen relation exactly once')
        if any(x['direct_reuse'] or not x['reason'] for x in items):
            raise ValueError('unreviewed reuse or missing explanation')
        if row['formal_mapping_assigned'] or row['human_gold'] or row['ontology_published']:
            raise ValueError('engineering audit cannot silently become mapping, gold or publication')


def run(out):
    from ontologyBuild.release_service import load_release
    from ontologyBuild.ontology_adapter import iter_classes, iter_object_properties, iter_datatype_properties
    from document.snapshot import read_snapshot
    from document.hierarchy_index import load_document_index
    from evidence.verification import verify_evidence_bundle
    if out.exists() or not out.is_relative_to(ROOT / 'outputs'):
        raise ValueError('new isolated output directory required')
    samples = read(POOL / 'pilot/review_samples.json')['samples']
    by_id = {s['sample_id']: s for s in samples}
    work = {r['sample_id']: r for r in read(PRE / 'ontology_coverage_worklist.json')}
    materials = read(PRE / 'mapping_materials.json')
    release = load_release(materials['release']['ontology_release_path'])
    props = {p['id']: p for p in [*iter_object_properties(release.ontology), *iter_datatype_properties(release.ontology)]}
    classes = {c['id']: c for c in iter_classes(release.ontology)}
    assert release.binding() == materials['release']
    assert props == {p['id']: p for p in materials['full_relation_catalog']}
    assert classes == {c['id']: c for c in materials['full_class_catalog']}
    assert len(props) == 48 and len(classes) == 189
    assert set(R.values()) <= set(props)
    assert {f'PILOT-{r[0]:03d}' for r in NOTES} == set(by_id) == set(work)
    assert {r[1] for r in NOTES} == set(FAMILIES)
    supplements = read(PRE / 'source_supplements.json')
    disputes = read(DISPUTES / 'seven_dispute_resolutions.json')
    watched = {**read(PRE / 'baseline.json')['watched_hashes'], **read(DISPUTES / 'baseline.json')['watched_hashes']}
    watched_paths = [PRE / name for name in ('ontology_coverage_worklist.json', 'mapping_materials.json',
        'source_supplements.json', 'ai_preannotations.json', 'priority_answers_blank.json')]
    watched_paths += [POOL / 'pilot/review_samples.json', POOL / 'pilot/reviewer_A_blank.json',
        POOL / 'pilot/reviewer_B_blank.json', DISPUTES / 'review.sqlite3', DISPUTES / 'report.json',
        DISPUTES / 'seven_dispute_resolutions.json', DISPUTES / 'source_visual_addendum.json',
        ROOT / 'outputs/ontology_freeze_20261116/live/frozen_release.json']
    watched_paths += [p for p in release.path.rglob('*') if p.is_file()]
    watched.update({str(p): file_hash(p) for p in watched_paths})
    visual = read(DISPUTES / 'source_visual_addendum.json')
    for sid in ('PILOT-011', 'PILOT-036'):
        item = visual[sid]
        assert item['original_binding'] == by_id[sid]['binding']
        assert file_hash(item['path']) == item['sha256']
        for image in item['images']:
            assert file_hash(image['path']) == image['sha256']
    document_rows = {d['document_id']: d for d in read(POOL / 'source_manifest.json')['documents']}
    snapshots, indices = {}, {}
    classes_by_label = {c['label']: c for c in classes.values()}
    rows = []
    original_quotes = supplement_quotes = 0
    for n, family, route, meaning, note, near, class_labels in NOTES:
        sid = f'PILOT-{n:03d}'
        sample = by_id[sid]; candidate = sample['candidate']; triple = candidate['triple']
        assert sample['binding']['candidate_content_hash'] == sha256_json(candidate)
        assert work[sid]['binding'] == sample['binding']
        if candidate['document_id'] not in indices:
            doc = document_rows[candidate['document_id']]
            indices[candidate['document_id']] = load_document_index(doc['index_path'], candidate['source_index_hash'], require_verified=True)
        verified = verify_evidence_bundle(sample['evidence_bundle'], indices[candidate['document_id']], candidate)
        assert verified['evidence_hash'] == sample['binding']['evidence_hash']
        anchors = [*sample['original_source_anchors']]
        extra = [x for group in supplements[sid]['contexts'] for x in group['items']]
        for item in [*anchors, *extra]:
            path = item['parent_path']
            if path not in snapshots:
                snapshots[path] = read_snapshot(path)
                watched[path] = file_hash(path)
            snapshot = snapshots[path]
            version = item.get('parent_document_version', item.get('document_version'))
            assert snapshot['document_version'] == version
            assert snapshot['canonical_text'][item['start']:item['end']] == item['quote']
        original_quotes += len(anchors); supplement_quotes += len(extra)
        related = {R[k] for k in near.split()}
        checks = []
        for rid, prop in props.items():
            d = '、'.join(classes[c]['label'] for c in prop.get('domain', []))
            r = '、'.join(classes[c]['label'] for c in prop.get('range', []))
            close = rid in related
            reason = (f'本项需表达“{meaning}”。该关系声明为“{prop["description"]}”，正式端点为{d}→{r}。'
                + ('其字面或宽泛语义仅可作对照，未证明精确含义、方向和两端类型同时满足。' + note
                   if close else '其限定含义和端点不提供本项所需谓词的完整表示；未据名称相似放宽范围。'))
            checks.append({'relation_id': rid, 'direct_reuse': False,
                'comparison': 'nearby_but_not_supported' if close else 'different_declared_relation',
                'reason': reason, 'domain_ids': prop.get('domain', []), 'range_ids': prop.get('range', []),
                'endpoint_type_entailment_used': False, 'owl_disjointness_claimed': False})
        class_checks = []
        for label in class_labels.split():
            c = classes_by_label[label]
            class_checks.append({'class_id': c['id'], 'label': label, 'definition': c.get('description'),
                'definition_present': bool(c.get('description') or c.get('definitions')),
                'status': 'related_material_not_confirmed_endpoint_type', 'formal_type_assigned': False,
                'reason': note, 'hierarchy_or_definition_recertified': False})
        rows.append({'sample_id': sid, 'binding': sample['binding'], 'original_triple': triple,
            'extraction_quote': candidate['extraction_quote'], 'source_anchors': anchors,
            'supplementary_source_items': extra, 'review_completed': True,
            'reviewer_kind': 'coding_agent', 'saw_ai_preannotations': True, 'independent_model_review': False,
            'review_scope': 'exact_original_candidate_against_frozen_relation_definitions_and_signatures',
            'direct_relation_coverage': 'no_supported_direct_reuse', 'needed_meaning': meaning,
            'prerequisite_route': route, 'reason_and_preservation_requirements': note,
            'requirement_family': family, 'nearby_relation_ids': sorted(related),
            'relation_checks': checks, 'relevant_class_checks': class_checks,
            'original_contract_issues': candidate['contract_issues'],
            'unknown_qualifier_fields': [k for k, v in triple['qualifier_status'].items() if v == 'unknown'],
            'source_semantic_revisions_applied': False, 'hypothetical_repaired_expression_approved': False,
            'previous_dispute_addendum_ref': str(DISPUTES / 'seven_dispute_resolutions.json') if sid in {r['sample_id'] for r in disputes['rows']} else None,
            'formal_mapping_assigned': False, 'human_gold': False, 'ontology_published': False})
    check_matrix(rows, props)
    # Integrity checks exercise rejection of omissions and silent status upgrades.
    invalid = deepcopy(rows); invalid[0]['relation_checks'].pop()
    try: check_matrix(invalid, props)
    except ValueError: pass
    else: raise AssertionError('missing catalogue item was accepted')
    invalid = deepcopy(rows); invalid[0]['formal_mapping_assigned'] = True
    try: check_matrix(invalid, props)
    except ValueError: pass
    else: raise AssertionError('silent mapping upgrade was accepted')

    requirements = []
    for key, (label, signatures, restriction) in FAMILIES.items():
        cases = [r for r in rows if r['requirement_family'] == key]
        signatures = [{'draft_key': 'draft:' + name, 'label': name, 'domain_role': domain, 'range_role': range_,
            'definition': label + '：在有来源身份和限定范围的命题中，从“' + domain + '”记录到“' + range_ + '”的' + label + '。' + restriction,
            'formal_id': None, 'kind': 'datatype_property_proposal' if '字面量' in range_ else 'object_property_or_reified_assertion_proposal'}
            for name, domain, range_ in signatures]
        requirements.append({'family': key, 'label': label, 'sample_ids': [c['sample_id'] for c in cases],
            'proposed_signatures': signatures, 'preservation_requirements': restriction,
            'source_proofs': [{'sample_id': c['sample_id'], 'binding': c['binding'],
                'quote': c['extraction_quote'], 'anchors': c['source_anchors']} for c in cases],
            'new_class_role_requirements': sorted({s[field] for s in signatures for field in ('domain_role', 'range_role')}),
            'all_named_roles_are_provisional_not_existing_class_assignments': True,
            'missing_existing_materials': [x for c in cases for x in c['relevant_class_checks']],
            'status': 'source_bound_shared_modeling_requirement', 'executable_schema_patch': False,
            'formal_ids_reserved': False, 'changes_existing_signatures': False,
            'requires_definition_identity_and_modeling_review': True, 'approved': False, 'published': False})
    completed_worklist = []
    for row in rows:
        old = deepcopy(work[row['sample_id']])
        old.update(label_status='coding_agent_coverage_review_completed', review_completed=True,
            review_origin='coding_agent_not_human_gold_or_committee_vote',
            direct_relation_coverage=row['direct_relation_coverage'], prerequisite_route=row['prerequisite_route'],
            requirement_family=row['requirement_family'], completion_ref=str(out / 'candidate_reviews.json') + '#' + row['sample_id'])
        completed_worklist.append(old)
    unchanged = {p: file_hash(p) == h for p, h in watched.items()}
    assert all(unchanged.values())
    with sqlite3.connect((DISPUTES / 'review.sqlite3').as_uri() + '?mode=ro', uri=True) as con:
        prior_attempts = con.execute('SELECT COUNT(*) FROM request_attempts').fetchone()[0]
        unknown = con.execute("SELECT COUNT(*) FROM request_attempts WHERE state='unknown'").fetchone()[0]
    assert prior_attempts == 2 and unknown == 1
    summary = {'version': VERSION, 'review_completed': True, 'reviewed_candidates': len(rows),
        'frozen_relation_count': len(props), 'datatype_properties_in_frozen_release': 0,
        'frozen_class_count': len(classes), 'relation_comparisons': sum(len(r['relation_checks']) for r in rows),
        'directly_reusable_relations_for_original_candidates': 0,
        'direct_relation_coverage_counts': dict(Counter(r['direct_relation_coverage'] for r in rows)),
        'prerequisite_route_counts': dict(Counter(r['prerequisite_route'] for r in rows)),
        'shared_requirement_families': len(requirements),
        'provisional_property_signatures': sum(len(r['proposed_signatures']) for r in requirements),
        'relevant_class_material_checks': sum(len(r['relevant_class_checks']) for r in rows),
        'class_identity_or_alias_route': [r['sample_id'] for r in rows if r['requirement_family'] == 'concept_alias'],
        'original_anchored_quotes_verified': original_quotes, 'supplementary_quotes_verified': supplement_quotes,
        'full_class_hierarchy_recertified': False, 'reviewer_kind': 'coding_agent',
        'human_gold_count': 0, 'independent_model_votes_added': 0, 'new_api_requests': 0,
        'earlier_study_attempts_unchanged': prior_attempts, 'earlier_study_unknown_still_open': unknown,
        'formal_mappings_assigned': 0, 'ontology_published': False, 'revisions_applied': 0,
        'production_written': False, 'model_reliability_updated': False, 'paper_metrics': None,
        'all_input_hashes_unchanged': all(unchanged.values()), 'all_matrix_membership_checks_passed': True,
        'missing_pair_and_silent_mapping_upgrade_rejection_checked': True,
        'missing_direct_relation_is_not_automatic_rejection_or_permission_to_publish': True}
    out.mkdir(parents=True)
    write(out / 'baseline.json', {'watched_hashes': watched})
    write(out / 'protocol.json', {'version': VERSION, 'scope': 'all_40_frozen_development_pilot_candidates',
        'release': release.binding(), 'review_origin': 'explicit_coding_agent_source_and_catalogue_analysis',
        'saw_ai_preannotations_and_prior_model_objections': True, 'not_independent_blind_review': True,
        'no_api_requests': True, 'no_source_or_candidate_changes': True, 'no_publication': True,
        'comparison_rule': 'All experimental methods keep the same frozen release. Any adopted expansion requires one new common version and separately bound inputs; no per-method or per-candidate private expansion.'})
    write(out / 'frozen_catalog.json', {'release': release.binding(), 'properties': list(props.values()), 'classes': list(classes.values()),
        'catalogs_match_verified_release_artifact': True})
    write(out / 'candidate_reviews.json', {'version': VERSION, 'rows': rows})
    write(out / 'extension_requirements.json', {'base_release': release.binding(), 'families': requirements,
        'all_comparison_methods_must_share_adopted_changes': True, 'approved': False, 'published': False})
    write(out / 'coverage_worklist_completed.json', completed_worklist)
    write(out / 'verification.json', {**summary, 'input_hash_checks': unchanged})
    lines = ['# 40条共享本体覆盖审查（2026-11-18）', '',
        '本轮40条覆盖审查已完成，审查者为编码 agent，已查看 AI 预标和上轮反对意见。本结果是有来源绑定的工程审查，不能充当人工金标准或独立三模型审核票。', '',
        f'核对冻结发布中的48个关系、189个类清单，以及相关类定义；逐候选列出全部48个关系，共1920项对照。原候选没有找到符合既有定义、方向和正式端点的直接关系复用。该结论不等于每条都必须新增关系，也不等于原命题来源不支持。', '',
        '现有属性多有特定情境端点。缺少源对象属于其域和值域的证据时，没有借 OWL domain/range 反推类型，也没有宣称不相容即逻辑不相交。36个既有类缺定义等历史问题未被本轮冒充全量重新认证。', '',
        '## 逐条处理', '', '| 样本 | 所需含义 | 先决处理 | 审查依据与保留项 |', '|---|---|---|---|']
    route_labels = {'expression_scope_first': '先修表达/范围', 'class_material_first': '先澄清类身份/别名', 'extension_requirements': '进入共享建模需求'}
    for row in rows:
        lines.append('| ' + ' | '.join([row['sample_id'], row['needed_meaning'], route_labels[row['prerequisite_route']], row['reason_and_preservation_requirements']]) + ' |')
    lines += ['', '## 共享扩充需求', '',
        f'归并为{len(requirements)}组来源支持的建模需求，列出{summary["provisional_property_signatures"]}个待讨论属性/重述节点签名；它们不是已批准的新关系数或最小本体。正式类身份、定义和建模评审后可合并、改用现有元数据或放弃。', '',
        'PILOT-035、037 单列概念身份与别名路线，不先新增“是/称为”万能关系。其余组按原命题所需含义提出谓词/角色需求；存在表达缺陷的条目，需求仅用于规划，不能视为修订后命题已获支持。', '']
    for req in requirements:
        lines += [f'- **{req["label"]}**（{", ".join(req["sample_ids"])}）：{req["preservation_requirements"]}']
    lines += ['', '## 验证与交接', '',
        f'40条原候选引用、{supplement_quotes}项补证均按来源版本和字符范围验证。完整关系清单与真实发布物一致；漏对照项与偷偷赋映射的结果都会被拒绝。原工作单、AI预标、人工空白表、160条候选、来源库、修订草案、冻结本体和生产本体目录哈希未变。', '',
        '本轮新增 API 请求、审核票、人工标签、正式映射、应用修订和发布数均为0。既有 Gemma 未知请求仍保持未知，没有重发。', '',
        '下一步按本表先决条件处理表达与类身份，再审核共享建模需求，形成一份各方法共同使用的新版本候选；经建模审核与验证后才能冻结用于新一轮实验。当前48关系版本保持原样，没有让单个方法单独扩充。', '',
        '[逐候选完整审查](candidate_reviews.json) · [共享建模需求及来源证明](extension_requirements.json) · [已完成工作单](coverage_worklist_completed.json) · [核验记录](verification.json)', '']
    (out / 'review.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    run(ROOT / 'outputs/shared_ontology_coverage_20261118')
