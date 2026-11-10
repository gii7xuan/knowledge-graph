# 30条开发样本 V8 复跑与质量评估

更新时间：2026-11-09T15:30:53.699871+08:00

已开始 30/30；处理结束 30/30；独立票完整 2/30。

本轮为开发回归。旧人工表的修改后事实判断、概念可表达性未迁为当前版本真值。

自动准入、自动应用修订、发布本体和图谱导入均关闭；全部转人工不能视为审核质量提高。

|质量指标|结果|分子/分母|缺失标注或结果|
|---|---|---|---|
|自动准入精确率|暂不可评估|0/0|0|
|错误放行率|暂不可评估|0/0|30|
|可用候选自动通过率|暂不可评估|0/0|30|
|修订成功率|暂不可评估|0/0|0|
|不必要修订率|暂不可评估|0/0|30|
|本体映射正确率|暂不可评估|0/0|0|

没有自动准入候选时，自动准入精确率为空，不写成0%或100%。已收齐票、运行结束与正确准入分别统计。

当前有效四维人工重标：0/30。待重标清单：annotation_template.json；完整材料：review_packets.json。

|样本|旧流程|新流程|独立票完整|原文|表达|映射|范围|
|---|---|---|---|---|---|---|---|
|DEV-001|mapping_review|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-002|revision_required|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-003|mapping_review|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-004|mapping_review|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-005|revision_required|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-006|revision_required|evidence_insufficient|是|insufficient|unresolved|context_incomplete|include|
|DEV-007|mapping_review|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-008|mapping_review|mapping_review|是|supported|standard|context_incomplete|include|
|DEV-009|mapping_review|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-010|mapping_review|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-011|revision_required|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-012|mapping_review|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-013|mapping_review|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-014|mapping_review|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-015|mapping_review|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-016|revision_required|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-017|revision_required|review_incomplete|否|unresolved|semantic_revision|context_incomplete|unclear|
|DEV-018|revision_required|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-019|revision_required|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-020|revision_required|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-021|revision_required|—|否|—|—|—|—|
|DEV-022|manual_review|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-023|mapping_review|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-024|revision_required|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-025|mapping_review|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-026|revision_required|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-027|mapping_review|review_incomplete|否|unresolved|semantic_revision|context_incomplete|unclear|
|DEV-028|manual_review|review_incomplete|否|unresolved|unresolved|conflict|unclear|
|DEV-029|excluded_by_scope|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|
|DEV-030|manual_review|review_incomplete|否|unresolved|unresolved|context_incomplete|unclear|

DEV-016将在首轮结束后选用同一份冻结提案做两次额外独立审核，比较九项判断与正式关系ID；不复用缓存票。

配置固定 temperature=0；top_p 和 seed 不发送。保存请求/返回模型标识；服务别名不能证明后端权重版本未变化。

未参与调试的独立验证集另行登记，在明确来源与防泄漏分组后单独评估，不能从这30条中改名切出。
