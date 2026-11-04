# 候选修订与人工分歧处理记录

截至 2026-10-30T17:04:44.298659+00:00，已对基线 161 条待修订/人工处理候选逐条重新分流。
本次新增 82 份草案；本轮应用 66 份修订（其中恢复历史草案 1 份），涉及 60 条候选、66 次版本递增。
当前修订后的候选中，57 条已收齐三席独立审核，48 条达到来源支持共识。其余保留待补审或分歧，不能把“修订已批准”当作“新版本已通过”。

| 指标 | 处理前 | 当前 |
|---|---:|---:|
| 全章独立审核完成 | 231/320 | 239/320 |
| 全章达到来源支持共识 | 170 | 216 |
| 图谱导入任务 | 0 | 0 |
| 准入授权 | 0 | 0 |

独立完成数按同一规则读取各候选的最近审计标记；来源支持数读取当前权威决定。

计数以候选为单位，不是去重事实数或人工金标准准确率。有依据的计划项共 82 份，尚未形成实际草案 0 份。

## 实际处理

- 所有应用的草案均经原 Qwen、Gemma、DeepSeek 三模型独立审核一致批准；新版本重新独立来源审核。
- 修正条件、频率/模态、极性、指代和主宾角色；将个别不及物变化/比较结论提交为属性表达审核。
- 针对三项分解与单项关系的真实反对意见，先审核“分量”草案，再依据反对意见改为原文完整三项之和；后三份草案均获一致批准，旧草案和原票继续保留。
- 检索并保留遗漏的直接定义及相邻分支句。新增证据使用真实文档偏移 ID，经过原文锚点核验。
- 未应用仅为满足本体 domain/range 而猜测实体类型的建议。当前人工状态全部保留 pending。

- 后续批次逐条补回肯定定义、量词、可表示模态和“我国/上述”范围；发布包读取修复后，映射变化额外提供真实类及关系定义，仍要求三席独立批准。
- 一条磁盘 I/O 失败审核已核验原检查点、提案和三席原票后零调用恢复；没有重发已成功的模型请求。

## 仍需处理

详细分流与逐条证据、反对意见见 [人工分歧清单](candidate_human_disagreements_20261030.md)，完整机器可读数据见 `runs/candidate_revision_resolution_20261030/human_disagreements.json`。

| 当前处理方向 | 条数 |
|---|---:|
| 本体映射需审核 | 14 |
| 请求结果未知，待服务端对账 | 81 |
| 来源共识已具备，待本体/问题义务闭合 | 42 |
| 有效票缺少检查项证据引用 | 13 |
| 修订草案分歧或契约失败 | 4 |
| 反证分歧或原票字段需澄清 | 4 |
| 表达或来源义务仍未解决 | 2 |
| 独立审核未齐 | 1 |

## 代码修复与验证

- 确认失败的原请求会收到原错误、准确变更字段、保护证据 ID 及字段状态约束的反馈；有效票和未知请求不会因此重发。
- 无 planner checkpoint 的确定性审核，可由已选定且内容哈希完全核验的原冻结提案恢复失败席位。
- 恢复后同步问题账本与决策；已解决的旧问题不再因投影未同步而继续显示为未解决。
- 新版本继承已应用草案使用的全部核验证据；校验草案/祖先版本绑定，篡改即阻断，并明确不复用旧版批准。
- 已核验的真实表达变化经当前三席完整且有依据的复核后，可关闭旧表达的证据不足；当前反对、缺少引用或仅版本变化均继续阻断。
- 已完成恢复、证据交接、未知请求保护、问题闭合、发布绑定、规范化与映射上下文回归；结果见 `targeted_regression.txt`。真实候选的证据交接核验记录见 `evidence_handoff_verification.json`。
- 优先加载候选绑定的正式发布包并校验 release/version/profile；不能用独立 JSON 快照的目录冒充发布包。
- 规范关系或类型变化需要新的语义指纹及独立草案审核；元数据变化不重用为新批准。撤回映射后不保留旧 canonical 标志。

- 为原文明示的建模假设增加 assumed 模态，中央允许值同时贯通解析、规划参数、严格修订和准入校验；抽取提示明确保留假设作用范围，旧票及旧版本不迁改。
- 提交后的检查点写入再次失败时，已提交的完整审核不会被降级为 failed；异常仍正常报告。

## 历史保护

已逐字节核对原有效票：三元组 933、修订 20、本体 5；均未变化。
另核对原候选版本 322、原来源提案 347，内容保持不变。历史未知请求状态变化 0 条。
待服务端对账的请求键共 113 个（按请求计，非候选数），已导出 `unknown_request_reconciliation.json`，包含模型、绑定、尝试时间和提供商请求 ID；未知请求不会自动重发。
运行保持 shadow、关闭自动准入与自动发布；没有导入 Neo4j。

## 本轮请求成本记录

新增服务请求尝试 562 次；已知输入 3465415 tokens、已知输出 533223 tokens；8 次缺少完整用量，4 次结果未知。未估算收费金额。逐候选成本见 `resolution_summary.json` 的 request_cost_by_candidate。

阶段状态：{"status.json": "completed", "recovery_status.json": "completed", "followup_status.json": "completed", "followup_recovery_status.json": "completed", "supplement_status.json": "pilot_blocked", "supplement_recovery_status.json": "completed", "supplement_remainder_status.json": "completed", "supplement_remainder_recovery_status.json": "completed", "continuation_status.json": "completed", "continuation_recovery_status.json": "completed", "source_mapping_status.json": "completed", "source_mapping_recovery_status.json": "completed", "final_followup_status.json": "completed", "final_followup_recovery_status.json": "completed"}

早期 pilot_blocked 是首三份草案的过程记录；补审后是否继续及最后结果，应同时查看 supplement_recovery 与 supplement_remainder 两阶段。过期草案的原票仍保留；CAS 行版本变化后不得直接应用。

处理前快照：`D:\knowledge-huopao\runs\candidate_revision_resolution_20261030\before_20261030_080358_394282.sqlite3`
当前快照：`D:\knowledge-huopao\runs\candidate_revision_resolution_20261030\after_20261030_170428_099756.sqlite3`
