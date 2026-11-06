# 模型维度意见与程序汇总

本次实现用户要求的“减少重复判断字段”：模型审核完整候选、分别给出九个维度意见，程序生成总判定与处理状态。新契约为`validator_response_v6`，旧V3、V4、V5、V5.1、V5.2的契约和提示词保留，不转换或混入历史票。

## 模型只填写一份原始意见

顶层必填字段从V5.2的15个减少到6个：`dimensions`、`evidence_assessments`、`primary_evidence_ids`、`counterevidence_reviewed_ids`、`requested_context`和`suggested_revision`。`schema_change_hint`继续可选。

`dimensions`包含九个固定键，每项附证据ID、解释和可选诊断码：

|维度|模型给出的唯一状态|
|---|---|
|triple_support|supported / contradicted / insufficient|
|entity_reference、relation_semantics、relation_direction、qualifiers、evidence_sufficiency、counterevidence|pass / fail / unknown / not_applicable|
|mapping|matched / ambiguous / unassigned / not_covered / constraint_conflict / candidate_mapping_error / suspected_schema_error；另给正式ID和竞争ID|
|knowledge_scope|general / conditional / local_definition / example / derivation / unresolved|

不再要求模型在顶层重复填写`verdict`、`source_support`、`expression_status`、`schema_assessment`、`schema_status`、`schema_issue`、`mapping`、`knowledge_scope`、`checks`或`reason_codes`。支持和反证集合直接从证据角色生成，模型也不再重复填写两组ID数组。主证据提名和已排除反例仍单独保留，因为它们承担不同审核含义。

## 程序生成可追溯的汇总

生成字段沿用内部原有格式，规划器、分流、准入和导出继续消费统一记录。典型规则如下，属于离线规则示例，不是第一章真实模型结果：

|模型原始意见|程序结果|
|---|---|
|核心来源支持，表达检查全部解决，映射matched|来源supported，表达faithful；满足其余检查时生成valid|
|核心来源支持，但qualifiers或实体/语义/方向检查fail|保留核心来源supported，表达needs_revision，总判定uncertain|
|核心来源支持，映射not_covered|本体unknown / not_covered，总判定uncertain，不算来源反驳或约束失败|
|映射constraint_conflict|本体fail / incompatible，生成约束冲突诊断，不改变来源维度|
|来源insufficient|总判定uncertain，表达未解决或保留已知修订问题|
|来源contradicted并有有效实质反证|生成invalid并保留反证；委员会和证据核验仍须独立成立|
|来源真实，但范围是已确认的局部定义|保留来源共识，按项目政策excluded_by_scope|

程序不会替模型猜测缺失维度、缺失映射或证据。真实语义分歧依旧记录；模型对映射的意见与程序guard分开。三席门槛、模型多样性、当前版本与冻结证据绑定、原文定位、本体定义与约束、shadow/active策略仍约束最终准入。原始维度、原始JSON与规范化记录均保留；辅助诊断码的空值兼容继续有效。

汇总规则的`summary_policy_version`和`summary_policy_hash`加入冻结元数据、proposal摘要及请求身份。`summary_derivation`说明规则版本与来源字段，程序独立诊断放入`program_reason_codes`。重新校验持久化票时会重新计算汇总，不能用手工覆写的valid、来源标签或本体状态替代原始维度。

审核调用、技术失败席位恢复、规划器摘要、委员会聚合、范围分流、准入和导出均已接入V6。页面在总判定后标注“程序汇总”。已有API中保留程序生成的旧字段，方便界面和历史数据继续使用；这些字段不再是模型输入的输出任务。

## 验证与使用边界

- 后端相关回归138项通过，覆盖九维度输出、汇总规则、来源与表达分离、反证保留、三席与程序guard、持久化票校验、原契约席位恢复、冻结策略以及旧版本兼容。
- 前端9项测试通过，工作台生产构建通过。
- 本次没有发送新模型请求，没有把旧V5.1/V5.2响应转换成V6票，没有修改生产候选或原人工工作簿。真实模型按V6返回的稳定性需要单独验证，离线测试不代表真实审核准确率。
- 默认配置和示例配置对新启动审核采用V6。正在运行的30条V5.1实验仍使用原冻结配置，不改变其票、预算或统计。

实现：`review_agent/response_contract_v6.py`、`prompts/triple_validator_v6.txt`及审核链路的版本分派。验证摘要见`outputs/dev30_v6_dimensions_20261104/verification_summary.json`。
