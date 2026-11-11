# V8 7条开发样本隔离复跑

工程门槛：通过。
已启动 7/7；三席完整 5/7；执行状态 {'completed': 5, 'partial_failure': 2}。
本阶段新委员请求尝试23次；规划请求21次。缓存回放不计独立重复。
模式：fresh_complete_pipeline；来源实验的真实委员请求23次、规划请求21次。

候选、原文、本体、规则、提示词、输出契约、代码及服务设置已冻结。生产准入、修订应用、发布与导入均关闭。
执行终态不等于审核完成，三席齐全不等于事实正确；本报告是开发回归，不是独立验证。

|样本|执行|原文（有效席位）|表达（有效席位）|映射（有效席位）|范围（有效席位）|建议|
|---|---|---|---|---|---|---|
|DEV-006|completed|supported (3/3)|standard (3/3)|context_incomplete (3/3)|include (3/3)|wait_mapping|
|DEV-008|completed|supported (3/3)|standard (3/3)|context_incomplete (3/3)|include (3/3)|wait_mapping|
|DEV-016|partial_failure|unresolved (2/3)|unresolved (2/3)|context_incomplete (3/3)|include (3/3)|manual_review|
|DEV-021|completed|unresolved (3/3)|unresolved (3/3)|context_incomplete (3/3)|include (3/3)|manual_review|
|DEV-028|partial_failure|unresolved (2/3)|unresolved (2/3)|context_incomplete (2/3)|unclear (2/3)|manual_review|
|DEV-029|completed|supported (3/3)|standard (3/3)|context_incomplete (3/3)|exclude (3/3)|exclude|
|DEV-030|completed|supported (3/3)|standard (3/3)|context_incomplete (3/3)|unclear (3/3)|wait_mapping|

四维人工标签0条；实际自动准入指标与六项影子建议指标均暂不可评估。
修订机制在隔离测试中验证了版本生成、三席独立审批、原子拆分、旧票不继承和新版本重审。实际修订有效性仍须执行影子案例并补充绑定全部子版本的人工评价。
未形成有效正式映射时，应继续补齐绑定本体的定义与实体身份材料；不能将相似召回、约束通过或检索未命中作为语义等价/本体缺口证明。
