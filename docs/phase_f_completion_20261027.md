# 记忆隔离、标注及阶段 F 代码补齐

日期：2026-10-27。接续[代码完成度复核](implementation_plan_code_audit_20261027.md)，补齐 R1～R4。真实领域标注和真实模型质量实验仍需使用独立数据执行；本轮验证使用 Mock。

## 记忆范围与版本

`review_agent/memory_scope.py` 规范化允许/排除的 document_id、run_id 和 candidate_id，并将配置与任务权限求交。受限范围的空允许列表表示不读取任何案例。训练、校准、测试文档不得重叠；实验强制只允许训练文档的历史人工案例进入记忆。

普通业务默认 `all_published`，保留已有历史案例使用方式；显式 `restricted` 执行允许范围，关闭记忆为 `disabled`。检索在应用范围过滤之后才取前 K 条，不让近期禁止案例遮蔽较早允许案例。任务恢复保留冻结范围，显式扩大范围或策略变化要求新审核。

实际范围及配置策略摘要贯通工具、检查点、提案、模型请求缓存、健康审核复用和校准兼容。旧缓存/校准未绑定新策略时不作为新策略下的正式授权。

## 独立标注与事实评价

工作台增加独立的本体兼容性、任务相关性和本体覆盖缺口标注。来源默认未判断；等待/修订可保留部分标注。后端校验二元维度为 0/1/null，来源为 supported/contradicted/insufficient/unknown/null；人工接纳不能与明确的不兼容或不相关标注冲突。

`evaluation.annotations` 从只读数据库导出当前有效事件及历史，按候选和修订选择最新事件；不初始化或迁移来源库。旧修订与新修订分别记录。仅供校准的 `feedback_rows()` 继续筛选有效二分类标签，评价导出保留部分标注；后者可用于创建独立金标准。

重复率和事实级匹配复用规范事实身份，包括实体 ID、关系语义、类型字面量及完整限定。完整候选重新计算事实键以发现篡改；未知、临时身份单独统计，不按显示名猜测合并。多个来源对应同一事实最多匹配一次独立金标准事实。

动态组仍以初始冻结候选池为覆盖分母。最终修订的正确性必须使用绑定其内容摘要的新修订标签，缺失时保持未知，不把原修订标签套到新版上。

## 受控实验入口

示例：[config/experiment.example.json](../config/experiment.example.json)。按实际文档 ID 设置 train/calibration/test，所有组共享冻结候选、来源、初始 release、模型池、工具基础集合和相同 case 预算。

| method / 消融 | 实际执行行为 |
| --- | --- |
| rules | 规范来源中严格词项共现及 guard 的简单规则基线，仅给实验建议 |
| single | 同一初始证据与完整审核契约，单个模型判断 |
| majority | 相同证据与模型池的简单多数，平票 unresolved，不借用正式委员会授权 |
| fixed_workflow | 已有固定补证工作流 |
| agent + fixed | 自主三元组审核，关闭本体扩展分支 |
| agent + dynamic | 自主审核、本体调查、独立本体票、隔离新版本发布及新修订重审 |
| no_targeted_retrieval | 移除三元组 Agent 的 search_evidence/inspect_context 能力，保留初始证据与其他共同工具 |
| no_issue_resolution | 保留问题与调查，阻止生命周期将问题解除 |
| no_counterevidence_resolution | 保留反证和审核要求，阻止反证问题解除，不删掉反证来制造通过 |
| no_extension | 关闭扩展能力，保留其余自主调查能力 |

每组使用新 SQLite 和本体目录。动态组仅在独立本体审批及已有低风险自动发布规则满足时，在该目录发布；高影响或 profile 差异保持人工处理。不会为完成实验强制通过。新修订、扩展与重审共享 case 预算；物理模型请求另用唯一 ID 记账，多个订阅不重复计入实验总成本。

实际业务决定保持 shadow。`predicted_decision` 是实验建议，`decision` 为实际业务路由，正式贡献数单独保存；实验运行器不连接 Neo4j。初始池哈希、源码/提示摘要、模型身份与返回标识、实际配置/回退策略、生成参数、请求、token、时延、扩展及新修订均有记录。未验证的服务能力/上下文上限和未返回用量保持 unknown/not_run。

运行阶段不接收测试真值；允许读取未标注测试全文进行调查/扩展，按传导式设置披露。金标准只在单独评价入口读取。价格由用户提供，缺少价格或 token 时总成本为未知，保留已知部分，不填成零。

## 使用顺序

以下命令是入口示例，本轮不会据此对生产运行真实模型。

```powershell
# 只读冻结全集，同时导出完全相同的修订及来源索引路径；可重复 --run-id 合并文档
python -m evaluation.manifest --database runs/v3/review.sqlite3 --run-id <run_id> --output runs/experiment_inputs/population.json --candidates-output runs/experiment_inputs/candidates.json --documents-output runs/experiment_inputs/documents.json

# 从冻结清单生成独立人工标注模板；填写后另存 labels.json
python -m evaluation.annotations --template --manifest runs/experiment_inputs/population.json --output runs/experiment_inputs/labels_template.json

# 或只读导出已有人工事件，保留部分标注及历史
python -m evaluation.annotations --database runs/v3/review.sqlite3 --manifest runs/experiment_inputs/population.json --output runs/experiment_inputs/annotations.json

# 按实际 ID 编辑实验 spec；指定当前基线的不可变发布目录，输出目录必须是新的
python -m evaluation.experiment --spec config/experiment.example.json --manifest runs/experiment_inputs/population.json --candidates runs/experiment_inputs/candidates.json --documents runs/experiment_inputs/documents.json --initial-release <release_directory> --config config/review_agent.json --output runs/experiments/<new_id>

# 模型执行结束后，另读独立标签评价
python -m evaluation.evaluate_experiment --experiment runs/experiments/<new_id> --labels labels.json --revision-labels revision_labels_by_group.json --gold-facts gold_facts_by_group.json --extension-labels extension_labels_by_group.json --output runs/experiments/<new_id>/metrics.json
```

标签记录用 candidate_id、revision、candidate_content_hash 绑定。`label`/`valid_for_kg` 是 0/1/null；ontology_gap 为独立人工判定。新修订标签按实验组提供并绑定最终候选摘要；扩展标签按 extension_id、delta_revision、proposal_digest 绑定。gold_facts 为规范身份的独立事实记录。未准备某类金标准时可以省略相应参数，相关正确性指标保持未知。

价格示例为 `{ "model_id": { "input_per_million": 1, "output_per_million": 2 } }`；数值由实际计价约定提供，不代表当前服务价格。通过 spec 的 prices 或评价入口的 --prices 指定，所有金额使用用户约定的同一币种。

报告区分实际接纳与建议精度，包含覆盖/失败分母、错误拒绝、锚点验证、人工语义有效率、冲突解除行为、修订正确性、独立标注缺口的最终已知正确保留率、扩展审批精度/精确重复率/历史影响、来源级与去重事实级正确数，以及角色分解的调用/重试/token/时延/成本。系统自报解除仅是行为统计，不能当作人工正确性证明。未运行图谱投影报告 not_run。

## 验证

最终验证结果：

- 阶段 F 专项 **16/16 通过，无跳过**：[执行报告](../runs/phase_f_validation/258c6807bce44dd8adcb1092ee592ab9/validation_report.json)、[逐项日志](../runs/phase_f_validation/258c6807bce44dd8adcb1092ee592ab9/tests.log)。覆盖记忆权限交集/空允许集/留出文档/旧案例召回、断点拒绝扩大权限、缓存与校准失效、部分标注/旧新修订分离/候选导出、完整事实键、精确金标准匹配、标签绑定、共同预算及十组实验。
- [十组受控实验汇总](../runs/phase_f_validation/258c6807bce44dd8adcb1092ee592ab9/demo/controlled_experiment/experiment_result.json)：同一冻结候选池和模型池，失败组 0、未处理任务 0、正式接纳 0。动态组完成 V1→本体独立审核→隔离 V2→revision 2→新三元组审核；固定组与禁用扩展组保持原修订。完整原始请求、SQLite、发布和新修订制品保留在同目录。
- [人工夹具评价示例](../runs/phase_f_validation/258c6807bce44dd8adcb1092ee592ab9/demo/controlled_experiment/fixture_metrics.json)仅用于验证公式与标签接线；其中的“正确”标签是测试数据，不是领域实验结果。未提供金标准的组/维度保持未知。
- 最终 **T01～T40 全部通过，77 个独立测试，无跳过**：[验收报告](../runs/acceptance/6508739c525e44d9bd4eb50616e85241/acceptance_report.json)。含真实隔离 Neo4j 和独立进程发布竞争，测试服务器已停止；本轮补充的记忆、标注、事实身份与实验场景纳入 T37。
- 全量回归 **358 项，335 通过、23 跳过、0 失败**：[结果](../runs/phase_f_regression.json)、[日志](../runs/phase_f_regression.log)。23 项为常规回归未设置图环境时跳过的图测试，相关链路另由上述隔离验收执行。首次全量中的测试夹具导入错误已修正，保留首次日志并完整重跑。
- 前端 9 项测试及 Vite 生产构建通过；源代码差异空白检查通过。四个已知生成文件恢复为测试前字节。
- 本轮验收绑定的 54 个实现文件及实验制品记录的源码摘要均与最终代码一致。生产数据库、本体 release 和原始本体摘要未变，仍为 shadow，自动接纳、自动发布和自动导入均未开启。

重现：`python -m scripts.validate_phase_f`。该入口保存十组受控实验、原始请求、本体发布、候选新修订及人工夹具评价；模型全为 Mock，不测量真实服务准确率。
