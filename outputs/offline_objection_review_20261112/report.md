# 130 项异议复核与客户端日志改进

已完成本轮第 1、2 项：离线核验 DeepSeek 的 130 个异议标签，并给实际模型客户端补齐 HTTP/SSE 观测。新增真实模型调用为 0。

## 异议复核结果

|口径|保留|修订|暂缓|合计|
|---|---:|---:|---:|---:|
|异议标签|40|84|6|130|
|独立本体 ID|44|116|6|166|

130 标签映射到 86 个类和 80 个关系 ID。同名关系分别判断，不能只按44个关系名称投一票。已绑定130段逐字相等的原文证据，包含字符区间、文档版本及索引哈希。

“修订”包含元数据缺失、定义范围不全和结构角色问题，不能将116项都称为事实错误。“保留”表示本条异议未充分成立；其中8项只是保留原有未批准关系定义草案，不能视作正式发布。87个 ID 在该冻结合并快照中缺定义，不能因此推断概念无来源。

已列出19项优先语义/结构修订规格，并生成80项未批准定义草案（类 24 项、关系 56 项）。二者有交叉，不能相加为新增本体项。尚有104个原本体 ID 不属于这130个异议的映射范围，未在本轮获得完整逐项认证。

## 优先处理的问题

1. `R_2a66075367666d54e7e7` 的“消耗”应复核动能对象，当前 range 是弹丸实体。
2. `R_e1de72d5c68b765c28b9` 的“具有部件”方向与分离波由正激波、斜激波组成的原文相反。
3. 投影、作用于、描述、夹角取决于等属性存在公式、结果、受力对象或依赖项角色错位，补定义不足以修复。
4. 高射火炮编表、气象修正等原句中的适用对象或依据被当成施事，应先确定关系角色。
5. 阻力系数、坐标系和惯性参考系存在定义或父类范围问题，需要定义与结构联动核验。

`R_fbc54f6800679e25d95d` 当前 range 为科氏加速度，与本章相符；不应为 DEV-028 错误候选“地面曲率”放宽约束。弹丸与空气的黏附关系须明确方向约定，不能只凭词面倒转。

6项暂缓分别是：中间层、亚同温层的原文术语冲突；同温层父类疑似断行误分；标准定律、标准分布的通用名称与特定函数指称；横风影响弹道关系的条件范围与既有未知审核。均保留原证据和旧记录。

## 交付物

- [130项完整逐项报告](D:/knowledge-huopao/outputs/offline_objection_review_20261112/term_review.md)：每个ID的现状、判断、证据和草案。
- [166个ID结构化标注](D:/knowledge-huopao/outputs/offline_objection_review_20261112/per_id_review.json)、[逐字证据](D:/knowledge-huopao/outputs/offline_objection_review_20261112/source_evidence.json)。
- [19项结构修订规格](D:/knowledge-huopao/outputs/offline_objection_review_20261112/structural_revision_candidates.json)、[80项未批准定义草案](D:/knowledge-huopao/outputs/offline_objection_review_20261112/unapproved_definition_drafts.json)。
- [后续修订顺序](D:/knowledge-huopao/outputs/offline_objection_review_20261112/revision_plan.json)、[客户端日志说明](D:/knowledge-huopao/docs/model_transport_diagnostics_20261112.md)。
- [交付校验](D:/knowledge-huopao/outputs/offline_objection_review_20261112/delivery_verification.json)、[回归测试结果](D:/knowledge-huopao/outputs/offline_objection_review_20261112/regression_test_result.json)。

## 边界及后续

本轮由本会话 AI 按证据和本体逐项判断，`annotated_by=ai`、`human_gold=false`。没有伪造人工金标准，没有变更旧委员会票或将 DeepSeek fail 改为 pass。未改生产本体、未生成发布版本，累计实际模型调用仍为13次；4条正式未知请求继续保留未知状态。

日志代码已经改变，因此不能沿用旧冻结材料中的代码指纹开启新审核。后续应先处理结构和暂缓项，再合并定义并生成新的冻结提案；新提案重新满足三席建模和整章覆盖要求。本轮结果不能替代这两项发布门禁。
