# 转人工候选修订实验

入口为 `python -m evaluation.handoff_revision`。`prepare` 和 `verify` 都不调用模型；只有显式执行 `run` 才会派发审核请求。原五组固定候选入口保持不可修订，已有实验结果不作追溯重算。

## 配置和预算

新配置 `handoff_revision` 在隔离数据库内开放映射、完整表达修订、草案审核、应用和新版本复审。关闭本体发布、扩展提案和图谱写入。每个原候选最多一个语义修订版本，草案批准不代替新版本独立复审。

2026-11-20 起配置版本为 `handoff_revision_profile_v2`。`propose_revision` 可以修改头实体、尾实体、关系、方向、语义上下文和限定信息，包括纠正已有字段；不再将所有修订路由到仅能补未知限定词的校验器。每个实际修改字段都须提交 `field_evidence` 和 `field_notes`，引用当前已验证原文。修订草案仍完整保留修改前后陈述，经过独立审核、隔离应用及新版本完整复审；旧票与 GPT 原始标签不沿用到新版本。限定词专用补全入口继续保留其较窄的任务边界。

三席委员会下，每次审核最多 24 次模型调用、8 次规划和 24 次工具操作；同一原候选跨版本共用 `CaseBudget`，上限 32 次模型调用、120 万已报告 token、48 次工具操作。首审 3 次、一次定向复核 3 次、草案审核与新版本审核 6 次，再加 8 次规划，共需 20 次模型调用。预算还约束后续动作，未派发或复用回执按已有记账规则处理，未知请求不自动重发。

证据选取的 `max_evidence_chars` 和定向复核请求的大小是两项预算。旧实现把整个证据包 JSON（含元数据）的长度与选取预算 12,000 字符比较，超出即在本地转人工，未向模型发送；这不是服务端上下文上限或超时检测。新配置按完整复核提案、输出 Schema 和系统提示的 UTF-8 序列化大小检查 `max_focused_input_bytes=262144`，保留全部原证据和新增证据，不为塞入预算截掉关键材料。该值是可冻结的本地派发预算，不代表模型的实际 token 上下文容量。定向复核另用 `focused_review_timeout_seconds=180`，仍受候选总截止时间约束；输入较大可能增加耗时，但超过某个字符数不等于发生超时。超过本地请求预算记为明确未派发的技术待办 `FOCUSED_INPUT_BUDGET_EXCEEDED`，不会伪装成来源不足或未知执行。

## 输入与命令

默认输入沿用已有冻结开发集。也可通过 `--data` 指定冻结清单，包含 `source_database`、`bindings` 和 `selected_raw_candidates`。每条 binding 包含 `sample_id`、`candidate_id`、`revision`、`candidate_content_hash`。支持多个 run 和文档；逐一核验原候选、原文索引和批准本体的哈希。可附 `source_canonical_text` 或按文档 ID 索引的 `source_canonical_texts` 进一步核验原文。标签不进入模型输入。

```powershell
python -m evaluation.handoff_revision prepare --output outputs/handoff_revision_trial --limit 3
python -m evaluation.handoff_revision verify --output outputs/handoff_revision_trial
# 需要正式执行时，显式使用 run；不能重复启动同一个冻结目录。
python -m evaluation.handoff_revision run --output outputs/handoff_revision_trial
python -m evaluation.handoff_revision report --output outputs/handoff_revision_trial
```

## 裁决边界

已知参数的本地补证不占模型调用，即使规划额度用尽仍可执行。补证历史绑定问题、范围和证据版本；空结果仅关闭相应范围。提前结束的策略拒绝保留成功收到的原回执，并返回具体合法动作；多次不合法结束后执行有验证锚点的本地补证。需要进一步语义判断但 Agent 无法判断的事项仍可具体列入人工待办。

`mapping_preflight_v2` 区分可提案的 `proposal_ready` 和正式映射齐备的 `judgment_ready`。类型候选从绑定本体的类名、别名、定义与原文召回，领域和值域只筛除不兼容项。映射草案必须附各端点的身份与类型证据以及竞争关系排除依据。

同时缺少限定与映射且原表达无需改写时，以 `propose_mapping.qualifier_changes` 提交一个联合草案。限定值与状态成对提交，附 `field_evidence`、`field_notes`；`not_stated` 还须附确实经工具核查的 `reviewed_scopes`。未决字段保留未知。该映射入口保留原始谓词；若头尾或谓词也有错误，使用完整表达的 `propose_revision` 提交统一修订，随后进行三席草案审核、隔离应用和三席新版本审核。

范围复核使用独立的 `candidate_scope_review_v2` 契约，返回类别及证据，按冻结范围策略计算收录。收录与排除的实质分歧保留为范围待办；`general` 与 `conditional` 仅在策略都收录时等价。已完成相关证据义务的确认排除项不再要求主图映射。
