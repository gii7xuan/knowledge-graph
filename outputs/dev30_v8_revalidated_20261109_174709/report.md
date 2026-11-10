# V8 离线重验与重聚合

派生ID：be899e146ce51688008674969bcd355898f9c574ae39f2822998705a85b8a273

仅重验已有返回，模型调用0次；历史票、历史决定和生产数据保持原样。
已知误判35张票：35张恢复有效；覆盖23个候选、23个候选版本、23份提案。
当前选定提案三席完整15份，对应15个不同候选；不混合不同轮次。

三席完整的定义：同一冻结提案、候选版本、证据/本体/规则/提示词绑定通过，三个固定席位契约有效、证据引用有效且模型身份和家族满足原门槛。席位不代表统计独立。
四维全部可判定0条；当前四维人工标签0条，质量指标暂不可评估。

judge_b 三轮结构合法，均为 changed meaning 与 compound_entity 等非 semantic_error 类型自相矛盾。保留原始票无效；判断稳定性不可评估。
35票逐张解释见 vote_revalidation.jsonl；15份提案去重见 proposal_index.json；终态和缺步骤见 decision_reaggregation.jsonl。

|样本|执行终态|三席完整|原文|表达|映射|范围|建议|
|---|---|---|---|---|---|---|---|
|DEV-001|partial_failure|False|unresolved|unresolved|context_incomplete|unclear|manual_review|
|DEV-002|partial_failure|False|unresolved|unresolved|context_incomplete|unclear|manual_review|
|DEV-003|completed|True|supported|standard|context_incomplete|unclear|wait_mapping|
|DEV-004|completed|True|supported|standard|context_incomplete|include|wait_mapping|
|DEV-005|partial_failure|False|unresolved|unresolved|context_incomplete|unclear|manual_review|
|DEV-006|completed|True|unresolved|standard|context_incomplete|include|manual_review|
|DEV-007|completed|True|supported|standard|context_incomplete|include|wait_mapping|
|DEV-008|completed|True|supported|standard|context_incomplete|include|wait_mapping|
|DEV-009|partial_failure|False|unresolved|standard|context_incomplete|unclear|manual_review|
|DEV-010|partial_failure|False|unresolved|unresolved|context_incomplete|include|manual_review|
|DEV-011|partial_failure|False|unresolved|unresolved|context_incomplete|unclear|manual_review|
|DEV-012|completed|True|unresolved|standard|context_incomplete|include|manual_review|
|DEV-013|completed|True|unresolved|standard|context_incomplete|include|manual_review|
|DEV-014|partial_failure|False|unresolved|unresolved|context_incomplete|unclear|manual_review|
|DEV-015|completed|True|unresolved|standard|context_incomplete|include|manual_review|
|DEV-016|partial_failure|False|unresolved|unresolved|context_incomplete|include|manual_review|
|DEV-017|partial_failure|False|unresolved|unresolved|context_incomplete|include|manual_review|
|DEV-018|completed|True|unresolved|standard|context_incomplete|include|manual_review|
|DEV-019|partial_failure|False|unresolved|unresolved|context_incomplete|unclear|manual_review|
|DEV-020|completed|True|unresolved|lossless_normalization|context_incomplete|unclear|revise|
|DEV-021|interrupted|False|unresolved|unresolved|context_incomplete|include|manual_review|
|DEV-022|partial_failure|False|unresolved|unresolved|context_incomplete|unclear|manual_review|
|DEV-023|completed|True|unresolved|standard|context_incomplete|include|manual_review|
|DEV-024|completed|True|unresolved|unresolved|context_incomplete|include|manual_review|
|DEV-025|partial_failure|False|unresolved|unresolved|context_incomplete|unclear|manual_review|
|DEV-026|partial_failure|False|unresolved|unresolved|context_incomplete|unclear|manual_review|
|DEV-027|completed|True|unresolved|unresolved|context_incomplete|include|manual_review|
|DEV-028|partial_failure|False|unresolved|unresolved|context_incomplete|unclear|manual_review|
|DEV-029|completed|True|supported|standard|context_incomplete|exclude|exclude|
|DEV-030|completed|True|unresolved|standard|context_incomplete|include|manual_review|
