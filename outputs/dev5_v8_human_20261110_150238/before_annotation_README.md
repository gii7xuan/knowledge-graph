# 首批5条当前版本盲标

先只阅读本页、各样本页及blind_packets.json，不打开上一层报告或ai_pre_review.md。原始包的system已清空，不含模型意见；证据、规则、本体上下文和材料哈希保留。
按材料完整度选择，完整定义仍可能缺失，允许部分维度不可判断。此批属于开发回归集，不能据此评价独立泛化效果。

|样本|候选版本|材料|
|---|---|---|
|DEV-004|2|[DEV-004.md](DEV-004.md)|
|DEV-007|3|[DEV-007.md](DEV-007.md)|
|DEV-008|2|[DEV-008.md](DEV-008.md)|
|DEV-014|1|[DEV-014.md](DEV-014.md)|
|DEV-015|2|[DEV-015.md](DEV-015.md)|

## 填写方法

1. 独立核对原文、指代、限定词、实体类型、正式关系和范围规则。原文引用中的图片未自动加载；不能将图像未核验内容当成已支持。
2. 在annotations.json填写标注者、时间、证据ID、理由、四维判断，以及是否看过模型结论。未知可填null，不能猜测。
3. 人工确认完成且完整材料已读后，才把status改为resolved、full_packet_reviewed改为true；当前全部pending。

|维度|JSON允许值|
|---|---|
|source|supported / contradicted / insufficient / unresolved / null|
|expression|standard / lossless_normalization / semantic_revision / unresolved / null|
|mapping|valid / conflict / unassigned / context_incomplete / null|
|scope|include / exclude / unclear / null|

admission_eligible、needs_revision、mapping_correct填写true/false/null。准入为true必须同时source=supported、expression=standard、mapping=valid、scope=include。
needs_revision只指候选事实/表达是否需改动；等待正式定义、映射或范围排除不自动等于需要事实修订。mapping_correct只评价实际形成的映射，材料不足可为null。
证据和候选等绑定字段不要编辑；有材料更新应另生成版本和标注包，旧标签不能直接迁移。

## 当前字段规范（原始冻结内容）

```json
{
  "deterministic_frequency_tokens": [
    "常常",
    "经常",
    "通常",
    "总是",
    "始终",
    "有时",
    "偶尔",
    "often",
    "usually",
    "always",
    "sometimes",
    "occasionally"
  ],
  "fields": {
    "applicability": "命题适用的对象、环境或范围；频率不写在此字段。",
    "condition": "命题成立所需条件；迁移和去重不得删除或扩大条件范围。",
    "correspondence": "原文明确的对象配对；拆分前逐条核对，禁止按顺序猜测配对或生成笛卡尔积。",
    "frequency": "命题成立的频率，如常常、总是；保留原词、强度、条件和语义附着，不等同于数量或适用条件。",
    "modality": "结合完整语境判断断言、假设、能力、可能性和必要性；定义或等价表示中的可不能仅按单字判为can。",
    "quantifier": "对象数量或量化范围，如至少三个、所有；分别表示对象之间的对应，不是数量。",
    "surface_relation": "关系表达本身；独立频率放入frequency，正式本体关系另行核验。"
  },
  "normalization_policy": "确定性结果是候选版本绑定的派生表达，不覆盖原始候选、不自动准入；模型仍核验来源、限定附着与完整语义。",
  "rules": [
    {
      "automatic": false,
      "checks": [
        "qualifiers",
        "entity_reference"
      ],
      "id": "PAIR-001",
      "rule": "分别、respectively表示对应。先明确原文的对象与配对，必要时拆分；每条新事实均需独立原文支持，不凭词语出现直接推断配对。",
      "title": "对应关系与数量分开"
    },
    {
      "automatic": true,
      "checks": [
        "qualifiers"
      ],
      "id": "FREQ-001",
      "rule": "旧applicability或quantifier中独立、完整的频率词可以原样迁入派生frequency。仅执行程序提供的有锚点计划，保留强度、条件与附着；混合短语、冲突频率和不明附着需语义审核。",
      "title": "频率与适用范围分开"
    },
    {
      "automatic": true,
      "checks": [
        "qualifiers",
        "relation_semantics"
      ],
      "id": "DUP-001",
      "rule": "仅清除surface_relation边界上与独立frequency完全相同且有原文锚点的重复词。频率仍独立保存，关系不得变空，不能用近义改写或改变条件。",
      "title": "重复频率只保留一份"
    },
    {
      "automatic": false,
      "checks": [
        "qualifiers",
        "relation_semantics"
      ],
      "id": "MOD-001",
      "rule": "结合完整句子、公式及上下文区分定义、等价表示与能力或可能性。可表示为不自动等于can，也不自动改成asserted；材料不足则请求上下文。",
      "title": "结合语境判断模态"
    },
    {
      "automatic": false,
      "checks": [
        "entity_reference",
        "relation_semantics"
      ],
      "id": "SPLIT-001",
      "rule": "拆分前检查每条新事实及原配对、联合、集合语义。包含组成集合不等于包含各成员；不将集合关系自动分配到每个成员。",
      "title": "复合实体拆分逐条举证"
    },
    {
      "automatic": false,
      "checks": [
        "entity_reference",
        "relation_semantics",
        "relation_direction",
        "qualifiers"
      ],
      "id": "MEANING-001",
      "rule": "关系替换、指称变更、方向或极性修改、条件增删和限定强度改变都需要新版本及证据审核。字段位置本身不构成事实矛盾；必须说明丢失或改变的含义。",
      "title": "实质语义修改重新审核"
    }
  ],
  "scope": "审核字段口径与派生规范化表达；原始候选与正式映射分别保留",
  "version": "review_fields_v1"
}
```

范围规则已附在各样本页；全量材料见blind_packets.json。AI预审另存，待独立标签提交后用于比对，不作为人工真值。
