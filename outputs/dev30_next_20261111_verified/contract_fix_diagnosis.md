# 契约诊断（仅离线）

- DEV-019 / judge_b：normalization must reference the exact source-bound deterministic plan；下一步 manual_review，压缩后输入 None bytes。
- DEV-022 / judge_b：unresolved material counter_evidence_ids conflict with counterevidence=pass; clarify roles; dismissed counterexamples and unresolved material counterevidence have different roles；下一步 semantic_clarification，压缩后输入 10210 bytes。
- DEV-024 / judge_b：$.dimensions.mapping.relation_id: required; $.dimensions.mapping.competing_relation_ids: required；下一步 field_patch，压缩后输入 15882 bytes。

DEV-019 的 plans 为空，FREQ-001 是规则而非合法计划；拒绝正确，禁止猜补。DEV-022 保留反证冲突；澄清单独记为新尝试且不计票。DEV-024 matched 缺ID需模型补齐，不从注释猜ID；unassigned 允许 null/[]。
