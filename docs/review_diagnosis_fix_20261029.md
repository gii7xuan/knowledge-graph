**本轮按《候选审核问题诊断与代码修改方案》补齐审核恢复、候选修订和本体任务消费。来源支持、本体准入、固定委员会与 shadow 策略继续分别核验。**

需求来源：[用户提供的诊断文件](C:/Users/P-Pxuan/Downloads/kg_candidate_review_diagnosis_20261029.md)。实现及验证记录位于 [本轮验收目录](D:/knowledge-huopao/runs/review_recovery_20261029)。

本轮没有调用 Qwen、Gemma 或 DeepSeek 的外部服务，没有发布本体或把候选入图。真实模型的小批复验仍需在 DeepSeek 服务恢复后进行。旧版已经丢失的迟到响应不能凭代码补成有效票；无法核实的旧请求保留未决。

**请求执行与恢复**

- 独立审核调用具有追加式 `request_attempts` 与事件记录，保留尝试标识、所有者、期限、原始返回引用、响应模型、提供方响应 ID、token、排队耗时和恢复来源。
- 真实请求由独立操作系统进程执行。候选进程结束后，原请求仍可完成并写回自己的尝试；迟到结果不直接修改当前决定。请求所需原文从已冻结的数据库记录读取，连接凭据只通过匿名管道传给执行进程。
- `recover_validation` 和本体审核的 `recover_ontology_validation` 只恢复服务端挑出的技术失败席位。有效的 valid、uncertain、invalid 或本体反对票保持原样。普通 `run_validation` 不再出现在相同输入已有结果的菜单中。
- 普通缓存键之外增加语义请求索引。更换凭据、命名空间或输出上限不能绕过相同输入的 running/unknown 请求。旧版有完整绑定的记录迁入索引；没有足够绑定的旧在途记录通过原审核的冻结恢复流程保持隔离。
- `recover-audit` 使用原提案、原契约与原模型席位，单独记录额外调用、token 和时间授权，保留原预算用量。已过期的审核期限只能通过该授权延长。新的当前决定或候选 revision 会阻止旧恢复覆盖它。
- 配置已实际传递每席位请求期限、HTTP 期限与输出上限。时限按剩余任务时间计算；没有根据一次超时记录直接把全局 45 秒改成更大的值。

**候选修订**

`propose_revision → review_revision → apply_reviewed_revision → 新 revision 入队独立复核` 已接通。修订审核使用独立的 `candidate_revision_review_v1` 任务契约，不能把草案审核票当作最终三元组审核票。

草案保存修改前后完整候选、逐字段依据、核验范围、原版本、行版本、草案及证据哈希。所有冻结席位均独立支持且模型身份、家族与依据满足要求后，才允许 CAS 提交。修订意见不一致、反证未处理或依据不足时保留人工处理。技术失败与意见分歧分开显示。

嵌套 patch 保留未改动的 condition、modality、time_scope、applicability 和 quantifier；合并后检查六字段及状态枚举、值与状态一致性、数字/布尔字面量类型。`not_stated` 仅表示指定证据范围未陈述。提交时重新计算映射和事实身份，并核对实际表达与独立审核的表达一致。

旧决定失效、旧订阅撤销、图谱贡献 dirty 标记、新 revision 和重审任务在同一事务中衔接。旧问题及真实反证跨 revision 保留，只有新版本的独立复核满足解决规则才关闭。批次将提交修订记为 `revised`，不把旧审核的 superseded 状态算成执行失败或完整独立复核。

**规划器与本体队列**

- 规划器现在能看到有界的 checks、修订建议、补证请求、本体诊断、技术失败及未映射类型提示。历史摘要压缩重复内容，完整票据和证据仍保留在服务端。独立委员不会读取其他委员当前票据。
- `extension_eligibility` 由菜单、动作校验和实际工具共用，按变更类型提供资格。已有关系不能走新增关系；已有术语可按专用规则增加别名；新增类必须对应完整查找确认的缺失端点名称。修改定义、约束及合并需要目标和独立诊断。
- 扩展任务、候选订阅和 job 同事务提交。缺少 job 的 proposed 任务可幂等补队；新增类别、别名及高影响变更的目标参与去重，并兼容匹配的旧任务。
- `worker` 是持续消费者，支持 job_type 过滤、心跳、租约令牌与旧所有者写入隔离。CLI 批次默认启动配套消费者，也可用 `--no-consumer` 配合独立常驻 worker。
- 402 暂停传播到相关队列，并在执行进程实际发送前再次检查。已有暂停原因保留。服务探测成功后仍需显式恢复暂停 job。
- 现有 revision 的不完整审核和不兼容契约升级进入定向恢复清单，普通批次不自动对这些候选重投整套委员会。新 revision 使用新的提案和最终票。

三元组规划使用 `planner_action_v4 / triple_planner_v4 / bounded_react_v4`。新任务配置使用 `validator_response_v4 / triple_validator_v4`：模型只填写 `schema_assessment`，服务端生成聚合所需的本体状态。旧 v3 返回仍按旧规则严格验证，没有静默修正。当前配置保留 shadow、关闭自动准入和自动发布。

页面及状态 API 展示表达缺口、原始来源意见、未决请求、修订状态、队列状态与待发布状态；预留调用预算不再标为精确 API 请求次数。

**现场状态及迁移**

修改前的只读备份确认：97 个 proposed 本体任务各有 queued job，97 个均没有 ontology_audit。这个现场的主要缺口是消费执行，而不是缺少入队记录。

迁移先在备份副本运行，再核对所有原表原列的行数和 SHA-256；第二次初始化验证幂等性。旧候选、修订、票据、决定和 case 预算用量必须保持一致。迁移及队列保留情况以 [迁移回执](D:/knowledge-huopao/runs/review_recovery_20261029/migration_receipt.json) 为准；[迁移预演](D:/knowledge-huopao/runs/review_recovery_20261029/migration_dry_run.json) 保留副本验证结果。消费者不会随本轮迁移启动。

正式库已完成第 7 版迁移：原业务数据和 case 预算用量校验一致，重复初始化幂等，SQLite 完整性检查为 `ok`。已为 970 条历史请求记录补入账本和语义索引；这不是 970 次新增 API 调用。DeepSeek 的历史余额故障已登记为服务暂停，97 个本体 job 保留为 paused。迁移前备份：[before_apply_20261029_101209_833304.sqlite3](D:/knowledge-huopao/runs/review_recovery_20261029/before_apply_20261029_101209_833304.sqlite3)。

**验收对应关系**

最终逐项结果见 [validation.json](D:/knowledge-huopao/runs/review_recovery_20261029/validation.json) 和 [回归日志](D:/knowledge-huopao/runs/review_recovery_20261029/regression.log)。下表说明验证的行为；模拟模型测试不等于事实准确率测试。

主回归运行 279 项，256 项通过、23 项按隔离 Neo4j 环境要求跳过，失败和错误均为 0。另有 1 项 R20 发布竞态补充用例通过；合计 257 项通过、23 项跳过。

| 编号 | 对应验证 |
| --- | --- |
| R01 | 技术失败席位单独恢复，有效席位调用数不增加 |
| R02 | 同一尝试接收迟到响应；本机 HTTP 实测请求进程存活至候选进程退出以后 |
| R03 | unknown 请求不重发；更改凭据、命名空间和输出上限仍被拦截 |
| R04 | 原 revision 的恢复票不能替换新 revision 的当前票 |
| R05 | 相同输入已有验证时，菜单不提供普通重验 |
| R06 | 有效 uncertain 与反对票不能被技术恢复覆盖 |
| R07 | 保留有界补证、修订、检查说明和失败信息；委员视图继续独立 |
| R08 | unknown 不默认变肯定；有依据且独立审核通过后提交修订 |
| R09 | 只改极性时保留条件和模态 |
| R10 | 六字段完整，非法状态及值状态冲突被拒绝，数字和布尔类型保留 |
| R11 | 已批准草案在基线变化后 CAS 失败 |
| R12 | 旧版本问题和反证继承到新版本，不能靠 revision 增长消除 |
| R13 | 已有映射时不提供新增关系动作 |
| R14 | 别名使用现有目标专用规则 |
| R15 | 未映射类型提示保留，完整查找确认缺类后才能申请 |
| R16 | 入队故障回滚订阅及费用；补队幂等 |
| R17 | 持续消费者真正执行本体审核，并验证本机 SDK/子进程路径 |
| R18 | 租约转移后旧 owner 不能心跳、完成或写入业务事务 |
| R19 | 审核建议通过不等于发布，关闭自动发布时保持原 release |
| R20 | 发布提交后、唤醒前发生人工修订，已撤销的订阅不再重绑定；见[发布竞态专项](D:/knowledge-huopao/runs/review_recovery_20261029/r20_validation.json) |
| R21 | 历史矛盾 v3 返回拒绝；v4 唯一本体状态经明确转换进入聚合 |
| R22 | 同模型别名不冒充独立席位；未配置的冻结席位不计完整复核或健康缓存 |
| R23 | 402 保留暂停及检查点，不解释为事实错误 |
| R24 | shadow 和未就绪校准器继续阻止自动准入 |

前端 9 项单元测试及生产构建已通过。需要独立 Neo4j 测试地址的用例按测试声明跳过，未触碰生产图谱；数量以本轮验证 JSON 为准。真实模型延迟分布、修订质量与人工标注准确率尚需服务恢复后的实验验证。

**服务恢复后的操作入口**

以下命令需在项目 Python 环境中执行。本轮未执行这些外部服务调用。

```powershell
# 充值或修复服务后，核验原来的模型服务；只发送可用性探测，不发送文档。
python -m review_agent.cli probe-services

# 恢复已暂停的本体队列，再启动常驻消费者。
python -m review_agent.cli resume-jobs --job-type ontology_extension --reason "原模型服务已核验恢复"
python -m review_agent.cli worker --job-type ontology_extension

# 按原提案恢复技术失败席位；预算追加有单独记录，原用量不会清零。
python -m review_agent.cli recover-audit --candidate-id <候选ID> --audit-id <原审核ID> --reason "服务恢复后定向恢复失败席位" --extra-calls <追加调用数> --extra-tokens <追加token预算> --extra-seconds <追加秒数>

# 委员已完整但表达/映射需要修订时，复用原观察进入修订调查。
python -m review_agent.cli revise-candidate --candidate-id <候选ID> --reason "核验并修订表达或映射"
```

unknown 记录只有取回原响应，或核实原执行单元已停止且请求未执行并记录恢复依据后，才可推进。其余记录继续待核实。不能把改参数、清缓存或 force 整批执行当成原请求恢复。
