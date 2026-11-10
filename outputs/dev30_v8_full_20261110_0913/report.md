# V8 30条开发样本隔离复跑

工程门槛：未通过，停止扩批。
已启动 30/30；三席完整 14/30；执行状态 {'completed': 14, 'partial_failure': 16}。
本阶段新委员请求尝试133次；规划请求90次。缓存回放不计独立重复。
模式：fresh_complete_pipeline；来源实验的真实委员请求133次、规划请求90次。

候选、原文、本体、规则、提示词、输出契约、代码及服务设置已冻结。生产准入、修订应用、发布与导入均关闭。
执行终态不等于审核完成，三席齐全不等于事实正确；本报告是开发回归，不是独立验证。

|样本|执行|原文（有效席位）|表达（有效席位）|映射（有效席位）|范围（有效席位）|建议|
|---|---|---|---|---|---|---|
|DEV-001|completed|unresolved (2/3)|standard (3/3)|context_incomplete (3/3)|include (3/3)|manual_review|
|DEV-002|partial_failure|unresolved (2/3)|unresolved (2/3)|context_incomplete (2/3)|unclear (2/3)|manual_review|
|DEV-003|completed|supported (3/3)|standard (3/3)|context_incomplete (3/3)|unclear (3/3)|wait_mapping|
|DEV-004|completed|supported (3/3)|standard (3/3)|context_incomplete (3/3)|include (3/3)|wait_mapping|
|DEV-005|partial_failure|unresolved (2/3)|unresolved (2/3)|context_incomplete (2/3)|unclear (2/3)|manual_review|
|DEV-006|partial_failure|unresolved (1/3)|standard (3/3)|context_incomplete (3/3)|include (3/3)|manual_review|
|DEV-007|completed|supported (3/3)|standard (3/3)|context_incomplete (3/3)|include (3/3)|wait_mapping|
|DEV-008|completed|supported (3/3)|standard (3/3)|context_incomplete (3/3)|include (3/3)|wait_mapping|
|DEV-009|partial_failure|unresolved (1/3)|standard (3/3)|context_incomplete (3/3)|unclear (3/3)|manual_review|
|DEV-010|partial_failure|unresolved (2/3)|unresolved (2/3)|context_incomplete (2/3)|unclear (2/3)|manual_review|
|DEV-011|completed|unresolved (2/3)|unresolved (3/3)|context_incomplete (3/3)|include (3/3)|manual_review|
|DEV-012|completed|unresolved (2/3)|standard (3/3)|context_incomplete (3/3)|include (3/3)|manual_review|
|DEV-013|completed|unresolved (2/3)|standard (3/3)|context_incomplete (3/3)|include (3/3)|manual_review|
|DEV-014|completed|supported (3/3)|standard (3/3)|context_incomplete (3/3)|unclear (3/3)|wait_mapping|
|DEV-015|completed|unresolved (2/3)|standard (3/3)|context_incomplete (3/3)|include (3/3)|manual_review|
|DEV-016|partial_failure|unresolved (1/3)|unresolved (2/3)|context_incomplete (3/3)|include (3/3)|manual_review|
|DEV-017|partial_failure|unresolved (2/3)|unresolved (2/3)|context_incomplete (3/3)|include (3/3)|manual_review|
|DEV-018|partial_failure|unresolved (1/3)|unresolved (2/3)|context_incomplete (2/3)|unclear (2/3)|manual_review|
|DEV-019|partial_failure|unresolved (0/3)|unresolved (0/3)|context_incomplete (0/3)|unclear (0/3)|manual_review|
|DEV-020|partial_failure|unresolved (1/3)|unresolved (2/3)|context_incomplete (2/3)|unclear (2/3)|manual_review|
|DEV-021|partial_failure|unresolved (2/3)|unresolved (2/3)|context_incomplete (2/3)|unclear (2/3)|manual_review|
|DEV-022|partial_failure|unresolved (2/3)|unresolved (2/3)|context_incomplete (3/3)|unclear (3/3)|manual_review|
|DEV-023|partial_failure|unresolved (1/3)|unresolved (2/3)|context_incomplete (2/3)|unclear (2/3)|manual_review|
|DEV-024|completed|unresolved (2/3)|unresolved (3/3)|context_incomplete (3/3)|include (3/3)|manual_review|
|DEV-025|completed|unresolved (3/3)|unresolved (3/3)|context_incomplete (3/3)|include (3/3)|manual_review|
|DEV-026|completed|unresolved (2/3)|standard (3/3)|context_incomplete (3/3)|unclear (3/3)|manual_review|
|DEV-027|partial_failure|unresolved (1/3)|unresolved (2/3)|context_incomplete (2/3)|unclear (2/3)|manual_review|
|DEV-028|partial_failure|unresolved (2/3)|unresolved (2/3)|context_incomplete (2/3)|unclear (2/3)|manual_review|
|DEV-029|completed|supported (3/3)|standard (3/3)|context_incomplete (3/3)|exclude (3/3)|exclude|
|DEV-030|partial_failure|unresolved (1/3)|unresolved (2/3)|context_incomplete (2/3)|unclear (2/3)|manual_review|

四维人工标签0条；实际自动准入指标与六项影子建议指标均暂不可评估。
修订机制在隔离测试中验证了版本生成、三席独立审批、原子拆分、旧票不继承和新版本重审。实际修订有效性仍须执行影子案例并补充绑定全部子版本的人工评价。
未形成有效正式映射时，应继续补齐绑定本体的定义与实体身份材料；不能将相似召回、约束通过或检索未命中作为语义等价/本体缺口证明。
