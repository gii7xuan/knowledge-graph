# 长篇领域文档知识图谱：层级证据校准机制代码改造方案

## 1. 改造目标

现有代码已经具备 Markdown 标题切分、本体约束、三元组抽取、证据文本、置信度分流、人工审核和 Neo4j 入库等基础能力，不需要推翻重写。本次改造的核心，是把当前流程：

> 当前文本块抽取 → 人工加权公式打分 → 固定阈值过滤

调整为：

> 层级文档索引 → 上下文子本体检索 → 候选三元组抽取 → 层级证据检索 → 证据与本体联合特征 → 正确概率校准 → 风险分层入库

论文中的主要创新应放在“层级证据约束的三元组置信度校准与风险可控入库”。章节目录和子本体检索作为支撑模块，不单独宣称主要创新。

## 2. 当前代码与数据存在的问题

### 2.1 数据问题

对 `triplets` 目录已有结果进行统计，共有 11,328 条三元组，其中：

- 8,816 条没有保存明确证据，占约 77.8%；
- 11,312 条至少一端实体类型为 `Unknown`；
- 847 条置信度超过 1；
- 至少存在 208 条完全重复的三元组；
- 当前人工审核日志只有 8 条，无法训练和评价校准模型。

这些结果说明旧版数据和当前代码中的置信度裁剪逻辑不一致。后续实验不能混用旧结果，应在新目录中重新生成数据。

### 2.2 代码问题

1. `extract_triplets/extrct.py` 中存在两套文本切分逻辑：封装函数使用 1000 字符，`main()` 又使用 800 字符，实际运行没有复用封装函数。
2. `get_ontology_text()` 直接截取前 10 个类和前 20 个关系，不是根据章节内容选择相关子本体。
3. 抽取 Prompt 中的允许关系被硬编码，与 `ontology.json` 中的关系集合可能不一致。
4. `validate_and_fix()` 在关系规范化之前计算置信度，关系修正后没有重新计算。
5. 证据评分使用整个 chunk，只返回 0、0.5、1，没有保存真正支持三元组的句子、字符位置和证据层级。
6. LLM 自评、逻辑审核、证据审核可能来自同一个模型，多个得分并不相互独立。
7. 0.8、0.6、0.4 是人工设置阈值，没有通过标注数据证明其对应的实际正确率。
8. Neo4j 导入模块没有根据新的自动接纳、人工接纳、拒绝状态进行严格过滤。
9. 代码中存在明文 API 凭据和数据库密码，应更换原凭据，并改用环境变量读取。

## 3. 建议的代码结构

建议在项目中增加以下目录和文件：

```text
document/
└── hierarchy_index.py          # 章节、文本块和句子的层级索引

schema/
└── subontology_retriever.py    # 根据章节语义检索相关子本体

evidence/
├── span_locator.py             # 原文证据位置校验
└── hierarchical_retriever.py   # 局部、小节、章节层级证据检索

confidence/
├── feature_extractor.py        # 构造校准模型输入特征
├── calibrator.py               # 训练、加载和执行概率校准
└── risk_policy.py              # 根据目标精度进行风险分层

scripts/
├── build_label_dataset.py      # 从人工审核结果生成标注数据
└── train_calibrator.py         # 训练并保存校准模型

evaluation/
└── evaluate_calibration.py     # 计算 ECE、Brier、覆盖率等指标
```

现有 `confidence/confidence_scorer.py` 可以暂时保留，但应逐步从“最终置信度计算器”改为“连续特征提取器”。

## 4. 统一章节切分并建立层级索引

### 4.1 修改位置

文件：`extract_triplets/extrct.py`

删除 `main()` 内重复的 Markdown 切分代码，统一调用增强后的：

```python
chunks = split_markdown_text(text)
```

### 4.2 扩展文本块结构

每个 chunk 除了保存正文和面包屑，还应保存章节关系、相邻文本块和句子位置：

```python
{
    "document_id": "huopaodandaoxue",
    "chunk_id": "chunk_xxx",
    "section_id": "section_xxx",
    "parent_section_id": "section_parent",
    "breadcrumb_path": "第3章 > 3.2节 > 空气阻力",
    "header_level": 3,
    "chunk_order": 25,
    "prev_chunk_id": "chunk_024",
    "next_chunk_id": "chunk_026",
    "content": "...",
    "sentences": [
        {
            "sentence_id": "chunk_xxx_s0",
            "text": "...",
            "start": 0,
            "end": 36
        }
    ]
}
```

后续证据检索需要使用以下层级信息：

- 当前句及其相邻句；
- 当前文本块；
- 同一小节的其他文本块；
- 当前小节的父章节；
- 文档中的前后位置。

固定字符切分仍可作为超长章节的二次切分方法，但应只保留一套参数，并记录参数版本。

## 5. 将本体截断改为上下文子本体检索

### 5.1 修改位置

文件：`extract_triplets/extrct.py` 中的 `get_ontology_text()`。

删除任意截取的 `[:10]` 和 `[:20]`，增加 `schema/subontology_retriever.py`。

### 5.2 检索方法

将每个本体类和关系组织为检索文本：

```python
relation_text = (
    f"{relation_label}；"
    f"定义：{description}；"
    f"主体类型：{domain}；"
    f"客体类型：{range}"
)
```

使用现有 `Qwen3-Embedding-4B`，以章节路径和正文作为查询：

```python
query = breadcrumb_path + "\n" + chunk_content
```

建议检索：

- 相关实体类型 Top 8；
- 相关关系 Top 10～15；
- 命中类型的父类；
- 命中关系对应的 domain/range 类型。

接口示例：

```python
sub_schema = schema_retriever.retrieve(
    breadcrumb_path=chunk["breadcrumb_path"],
    text=chunk["content"],
    top_k_classes=8,
    top_k_relations=12,
)
```

子本体仅用于降低 Prompt 长度和无关类型干扰。后置验证仍应使用完整本体，避免相关关系未被召回时直接丢失知识。

不建议使用 BERTopic 完成逐块子本体检索。现有嵌入模型可以直接计算章节语义与本体定义之间的相似度，实现更简单，也便于消融实验。

## 6. 修改三元组抽取 Prompt

### 6.1 删除不可靠字段

当前 Prompt 要求模型同时输出三元组、证据、理由、`thought` 和 `confidence`。建议删除：

- `thought`；
- `confidence`。

LLM 自评置信度不等于正确概率，同时让抽取模型给自己评分会与后续校准形成循环依赖。

### 6.2 区分原始关系和标准关系

抽取阶段输出候选事实：

```json
{
  "triplets": [
    {
      "head": "空气密度",
      "surface_relation": "成正比",
      "tail": "空气阻力加速度",
      "evidence_quote": "空气阻力加速度与空气密度成正比",
      "head_type_candidate": "C_PhysicalParameter",
      "tail_type_candidate": "C_PhysicalParameter"
    }
  ]
}
```

其中：

- `surface_relation` 尽量保留原文关系表达；
- `relation` 由后置关系映射模块生成，表示本体标准关系。

抽取阶段不要强制把所有关系直接改写成本体关系，否则无法评价关系映射是否改变了原意。

## 7. 增加层级证据检索

### 7.1 复用已有证据定位代码

`ontologyBuild/evidence_validator.py` 已实现 `find_evidence_span()`，能够检查证据是否真实存在于原文，并返回字符起止位置。建议将这一函数移动或封装到 `evidence/span_locator.py`，供本体候选和三元组证据共同使用。

### 7.2 层级检索顺序

`evidence/hierarchical_retriever.py` 按照以下范围逐级检索：

1. LLM 返回的原文证据；
2. 三元组所在句及前后各一句；
3. 当前 chunk；
4. 同一小节的其他 chunk；
5. 父章节下的相关 chunk；
6. 必要时搜索其他章节。

接口示例：

```python
evidence_result = evidence_retriever.retrieve(
    triplet=triplet,
    source_chunk=chunk,
    document_index=document_index,
)
```

返回结构：

```python
{
    "best_evidence": {
        "evidence_id": "ev_xxx",
        "chunk_id": "chunk_xxx",
        "sentence_id": "chunk_xxx_s2",
        "text": "空气阻力加速度与空气密度成正比",
        "start": 126,
        "end": 144,
        "level": "local_sentence",
        "retrieval_score": 0.91,
        "entailment_score": 0.94,
        "contradiction_score": 0.02
    },
    "support_count": 2,
    "searched_levels": [
        "local_sentence",
        "current_chunk",
        "same_section"
    ]
}
```

父章节摘要可以用于定位候选证据，但不能作为最终事实证据。最终证据必须来自原始文档，防止生成摘要中的错误被当作原文事实。

## 8. 重组后置处理顺序

当前 `validate_and_fix()` 在关系修正前计算置信度，应调整为以下顺序：

```python
def process_candidate(triplet, chunk, document_index):
    # 1. 定位或检索原文证据
    evidence_result = evidence_retriever.retrieve(
        triplet, chunk, document_index
    )

    # 2. 规范化关系
    mapping_result = relation_mapper.map(
        triplet["surface_relation"]
    )
    triplet["relation"] = mapping_result["relation"]

    # 3. 使用已定位证据预测实体类型
    type_result = predict_entity_types(
        triplet,
        evidence_result["best_evidence"]["text"],
    )

    # 4. 在标准关系上执行 domain/range 验证
    ontology_result = validate_domain_range(
        triplet["relation"],
        type_result,
    )

    # 5. 提取校准特征
    features = feature_extractor.extract(
        triplet=triplet,
        evidence_result=evidence_result,
        mapping_result=mapping_result,
        type_result=type_result,
        ontology_result=ontology_result,
    )

    # 6. 预测经过校准的正确概率
    probability = calibrator.predict_proba(features)

    # 7. 风险分流
    decision = risk_policy.route(probability)

    return build_result(
        triplet,
        evidence_result,
        mapping_result,
        type_result,
        ontology_result,
        features,
        probability,
        decision,
    )
```

完整顺序应为：证据检索、关系映射、实体分类、本体检查、特征生成、概率校准、风险分流。

## 9. 将人工加权公式改为可训练校准器

### 9.1 校准特征

保留现有计算逻辑中的有效信号，但不要直接人工乘权。建议生成连续特征：

```python
features = {
    "evidence_entailment": 0.94,
    "evidence_exact_match": 1,
    "head_in_evidence": 1,
    "tail_in_evidence": 1,
    "support_count": 2,
    "contradiction_score": 0.02,
    "evidence_level": 0,
    "relation_mapping_score": 0.88,
    "relation_mapping_margin": 0.21,
    "domain_ok": 1,
    "range_ok": 1,
    "head_type_confidence": 0.84,
    "tail_type_confidence": 0.90,
    "extraction_agreement": 0.67
}
```

`relation_mapping_margin` 表示第一候选关系和第二候选关系的相似度差。差值越小，说明关系映射越不稳定。

`evidence_level` 可以编码为：

- 0：当前句；
- 1：当前 chunk；
- 2：同一小节；
- 3：父章节；
- 4：跨章节。

### 9.2 简单校准器

考虑项目规模和实现成本，第一版使用逻辑回归即可：

```python
from sklearn.linear_model import LogisticRegression
import joblib


class TripleCalibrator:
    def __init__(self):
        self.model = LogisticRegression(
            class_weight="balanced",
            max_iter=1000,
        )

    def fit(self, features, labels):
        self.model.fit(features, labels)

    def predict_proba(self, features):
        return float(self.model.predict_proba([features])[0, 1])

    def save(self, path):
        joblib.dump(self.model, path)
```

数据充足后，可以在逻辑回归输出上增加 Isotonic Regression。论文实验建议比较：

- 原人工加权公式；
- 仅使用证据蕴含分；
- Logistic Regression；
- Logistic Regression + Isotonic Calibration。

原公式输出只能称为 `raw_score`，不能直接称为“正确概率”。经过校准模型处理后的结果保存为 `calibrated_probability`。

## 10. 通过验证集选择风险阈值

当前 0.8、0.6、0.4 阈值应改为通过验证集选择。例如，自动入库要求达到 95% 精度：

```python
def find_accept_threshold(labels, probabilities, target_precision=0.95):
    candidates = sorted(set(probabilities))
    valid = []

    for threshold in candidates:
        selected = [
            y for y, p in zip(labels, probabilities)
            if p >= threshold
        ]
        if selected and sum(selected) / len(selected) >= target_precision:
            valid.append(threshold)

    return min(valid) if valid else 1.0
```

风险分流：

```python
if probability >= accept_threshold:
    decision = "auto_accept"
elif probability <= reject_threshold:
    decision = "auto_reject"
else:
    decision = "manual_review"
```

`accept_threshold` 用于保证自动接纳精度。`reject_threshold` 也应通过验证集确定；在标注数据不足时，低分结果先进入人工审核，不直接自动删除。

阈值文件应记录：

```json
{
  "target_precision": 0.95,
  "accept_threshold": 0.87,
  "reject_threshold": 0.18,
  "validation_document_ids": ["doc_05"],
  "policy_version": "precision95_v1"
}
```

## 11. 改造人工审核界面

### 11.1 修改位置

文件：`review_triplets_app.py`。

### 11.2 新增标注字段

人工审核不能只记录保留或删除，还应记录三类判断：

```json
{
  "triple_correct": 1,
  "evidence_support": "full",
  "relation_mapping_correct": 1,
  "corrected_triple": null
}
```

建议取值：

- `triple_correct`：0 或 1；
- `evidence_support`：`full`、`partial`、`none`；
- `relation_mapping_correct`：0 或 1；
- `corrected_triple`：人工修改后的三元组，没有修改时为 `null`。

### 11.3 审核页面显示内容

页面应显示：

- 候选三元组；
- 高亮后的最佳证据句；
- 证据所在章节和证据层级；
- 原始关系和标准关系；
- domain/range 检查结果；
- 原始特征分；
- 校准概率；
- 系统建议的处理动作。

当前只有 8 条审核记录，不能用于训练。建议从不同教材、不同关系和不同原始分数区间分层抽取 600～1000 条进行人工标注。

训练集、验证集和测试集必须按照“文档”划分，不能把同一章节中的相似三元组随机分到训练集和测试集，否则会产生数据泄漏。现有七本文档可以采用五本训练、一本验证、一本测试，或者进行按文档留一法交叉验证。

人工修改三元组时，原始三元组应标记为错误；修改后的三元组作为一条新的、经过人工确认的正确样本保存。

## 12. 重新设计结果 JSON

为兼容旧代码，可以继续保留 `evidence` 和 `confidence`，但建议使用以下结构：

```json
{
  "triple_id": "tr_xxx",
  "head": "空气密度",
  "surface_relation": "成正比",
  "relation": "正比于",
  "tail": "空气阻力加速度",
  "head_type": "C_PhysicalParameter",
  "tail_type": "C_PhysicalParameter",

  "document_id": "huopaodandaoxue",
  "source_id": "chunk_xxx",
  "breadcrumb_path": "第3章 > 空气阻力",

  "evidence": "空气阻力加速度与空气密度成正比",
  "evidence_items": [
    {
      "chunk_id": "chunk_xxx",
      "sentence_id": "chunk_xxx_s2",
      "start": 126,
      "end": 144,
      "level": "local_sentence",
      "retrieval_score": 0.91,
      "entailment_score": 0.94,
      "contradiction_score": 0.02
    }
  ],

  "confidence_features": {
    "evidence_entailment": 0.94,
    "evidence_exact_match": 1,
    "domain_ok": 1,
    "range_ok": 1,
    "relation_mapping_score": 0.88
  },

  "raw_score": 0.83,
  "calibrated_probability": 0.91,
  "confidence": 0.91,
  "decision": "auto_accept",
  "calibrator_version": "lr_iso_v1",
  "policy_version": "precision95_v1"
}
```

新结果建议写入：

```text
runs/v2/candidates/
runs/v2/accepted/
runs/v2/review_queue/
runs/v2/rejected/
runs/v2/metrics/
```

不要直接覆盖现有 `triplets` 目录，防止新旧置信度结果混用。

## 13. 修改 Neo4j 入库逻辑

### 13.1 增加入库条件

文件：`to_neo4j/save_to_neo4j.py`。

只有自动接纳或人工接纳的事实才能进入正式图谱：

```python
if item.get("decision") not in {
    "auto_accept",
    "human_accept",
}:
    return False
```

### 13.2 扩展 Fact 和 Evidence 属性

`Fact` 节点增加：

- `raw_score`；
- `calibrated_probability`；
- `decision`；
- `calibrator_version`；
- `policy_version`。

`Evidence` 节点增加：

- `document_id`；
- `chunk_id`；
- `sentence_id`；
- `breadcrumb_path`；
- `start`；
- `end`；
- `level`；
- `retrieval_score`；
- `entailment_score`；
- `contradiction_score`。

现有关系：

```text
(Fact)-[:SUPPORTED_BY]->(Evidence)
```

可以继续保留，并增加文档层级关系：

```text
(Evidence)-[:FROM_CHUNK]->(Chunk)
(Chunk)-[:BELONGS_TO]->(Section)
(Section)-[:PARENT_OF]->(Section)
(Document)-[:HAS_SECTION]->(Section)
```

### 13.3 统一实体唯一标识

当前代码根据 `head_type` 和 `tail_type` 使用不同 Neo4j 标签执行 `MERGE`。同一个实体在类型预测发生变化时，可能生成多个节点。

建议统一使用：

```cypher
MERGE (e:Entity {entity_key: $entity_key})
SET e.name = $name,
    e.type_id = $type_id
```

`entity_key` 可以由“规范化实体名 + 领域”生成。本体类别保存为 `type_id` 属性，确认稳定后再附加类别标签。

## 14. 去重和多证据聚合

抽取 Prompt 中要求模型自行去重并不可靠，必须在代码中确定性去重。

推荐事实键：

```python
fact_key = normalize(head) + "\t" + relation + "\t" + normalize(tail)
```

同一事实来自多个章节时，不删除后续证据，而是聚合到同一个事实：

```text
                  ┌─ Evidence 1
Fact ─ SUPPORTED_BY
                  ├─ Evidence 2
                  └─ Evidence 3
```

事实正确概率可以使用校准模型基于多证据特征重新计算，而不是简单取多个置信度的最大值。

## 15. 评价脚本与论文指标

`evaluation/evaluate_calibration.py` 至少计算：

### 15.1 抽取质量

- Precision；
- Recall；
- F1；
- 本体约束符合率；
- 重复率。

### 15.2 证据质量

- Evidence Exact Match Rate；
- Evidence Accuracy；
- 完整支持、部分支持、无支持比例；
- 不同证据层级的召回率。

### 15.3 校准质量

- Expected Calibration Error（ECE）；
- Brier Score；
- Reliability Diagram；
- AUROC 或 AUPRC。

### 15.4 风险入库效果

- Coverage@95% Precision；
- Precision-Coverage 曲线；
- 自动入库比例；
- 人工审核比例；
- 错误自动入库比例。

论文最终需要证明：在同样要求 95% 自动入库精度时，层级证据校准方法比单 chunk 验证、GraphJudge 式二分类和人工加权公式接纳了更多正确三元组，并降低了人工审核量。

## 16. 消融实验设计

建议设置以下消融实验：

| 实验版本 | 目的 |
|---|---|
| 完整方法 | 层级证据、本体特征、关系映射特征、校准和风险分流全部启用 |
| 去掉章节层级 | 检验跨句、跨 chunk 和跨小节证据的作用 |
| 只验证当前 chunk | 与现有代码直接比较 |
| 去掉本体约束特征 | 检验 domain/range 和实体类型的作用 |
| 去掉关系映射特征 | 检验关系规范化不确定性的作用 |
| 去掉多次抽取一致性 | 检验 extraction agreement 的作用 |
| 不做概率校准 | 对比原始分类器输出与校准概率 |
| 使用人工加权公式 | 与现有置信度机制直接比较 |
| 使用固定阈值 | 与验证集自动选择阈值比较 |

## 17. 推荐实施顺序

### 第一阶段：建立可信数据基础

1. 统一 `split_markdown_text()`；
2. 为 chunk 和句子增加层级及位置字段；
3. 强制验证 `evidence_quote` 是否存在于原文；
4. 将新结果输出到 `runs/v2/`；
5. 完成代码级确定性去重。

### 第二阶段：实现上下文约束抽取

1. 增加子本体检索；
2. 修改抽取 Prompt；
3. 区分 `surface_relation` 和标准 `relation`；
4. 调整关系映射、实体分类和 domain/range 验证顺序。

### 第三阶段：收集标注数据

1. 改造人工审核页面；
2. 分层抽样 600～1000 条候选三元组；
3. 分别标注三元组正确性、证据支持程度和关系映射正确性；
4. 按文档划分训练、验证和测试数据。

### 第四阶段：训练与评价校准器

1. 生成特征矩阵；
2. 训练逻辑回归；
3. 增加 Isotonic Regression；
4. 在验证集上选择风险阈值；
5. 计算 ECE、Brier 和 Coverage@95% Precision；
6. 修改 Neo4j，只导入接纳事实。

## 18. 最小可行版本

如果时间和代码能力有限，可以先完成以下最小版本：

1. 保留现有标题切分和 Qwen 三元组抽取；
2. 统一切分函数并保存章节、相邻 chunk 和句子位置；
3. 使用 `find_evidence_span()` 强制证据必须来自原文；
4. 从当前句、相邻句、当前 chunk、同一小节逐级检索证据；
5. 使用证据蕴含、本体 domain/range、关系映射相似度、实体类型置信度作为四组特征；
6. 人工标注不少于 600 条三元组；
7. 使用 Logistic Regression 输出正确概率；
8. 根据验证集 95% 精度要求选择自动入库阈值；
9. 报告 ECE、Brier、Coverage@95% Precision 和人工审核比例。

这一版本不需要训练复杂神经网络，已经能够将现有工程流程转化为围绕“层级证据校准”的研究方法。

## 19. 论文方法定位

改造后的方法不应描述为三个已有模块的简单拼接，而应描述为：

> 面向长篇中文领域技术文档，利用章节层级为候选三元组构建渐进式证据检索范围，在原文证据、本体约束、关系映射和抽取稳定性等信号基础上学习三元组正确概率，并根据目标精度实现风险可控的自动入库与人工审核分流。

目录层级决定证据检索范围，子本体检索降低抽取干扰，证据与本体特征共同决定校准概率，校准概率进一步控制图谱入库风险。模块之间形成因果关联，区别于简单的流水线组合。
