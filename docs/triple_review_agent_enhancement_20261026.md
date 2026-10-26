# 三元组审核 Agent 增强（方案阶段 C）

本轮按 `kg_quality_implementation_plan_20261025.md` 的第 8、10.1–10.3、10.5、13 章及阶段 C 改造现有审核 Agent。保留真实的规划—工具观察—再规划循环、SQLite 仓储、固定委员会和 shadow 默认策略。本轮实现三元组侧的扩展请求与等待接口；第二个本体审核 Agent、发布后重绑定/唤醒和完整 Fact 投影属于后续阶段。

## 已完成

### 契约、共识与版本

- 审核契约升级为 `validator_response_v3`，提示词 `triple_validator_v3`；新增必填 `schema_issue`、可为 null 的 `schema_change_hint`，以及可选的逐证据 `evidence_assessments`。
- `invalid` 要求原文反驳/内容错误及反证引用。只有 Schema 未覆盖或约束待复核时保留 `source_support=supported`、`verdict=uncertain`，不能仅因本体问题判事实错误。
- 分别计算 `source_support_consensus` 和 `admission_consensus`。原文支持要求有效锚点、限定与方向检查和反证检查已完成；正式准入还要求当前本体通过、关键问题解决及现有风险策略、校准器和 active 模式门槛。
- 保留当前 Qwen、Gemma、deepseek-flash 三席三票配置；错误、缺席不缩小分母。按配置的 canonical `model_identity` 去重，同时利用服务端返回的实际 model 字段发现别名重复。服务端未报告时记录 unavailable；配置就绪不代表真实连通性、实际模型版本或统计独立性已经验证。
- 票据绑定任务类型、候选/修订、audit/round/digest、来源快照/索引、证据、本体 release/profile、委员会、提示词和输出契约。三元组票不能成为本体任务的票。
- 冻结候选全文摘要、guard 摘要、委员会成员及门槛摘要、提示词内容摘要和契约摘要。缓存命名空间升级为 `validation_single_flight_v2`；旧契约不能复用。校准训练导出及兼容性检查同步保留这些摘要，不能在同一版本字符串下静默改门槛或提示词。

### 问题生命周期与来源义务

- 问题包含 `issue_id/status/opened_in_proposal/resolved_in_proposal/resolution_rule/evidence_ids`；`issue_events` 追加记录开启、继承、解决及证据相关性处理。
- 候选正确、第一轮证据不足：必须新增原文片段，再由当前轮独立审核确认，才能解除；仅变更检索元数据或规划文本无效。
- `CONDITION_MISSING/QUALIFIER_MISMATCH/DIRECTION_MISMATCH` 等候选表达问题在当前 revision 持续有效。规划器只能提交修订草案，由现有确认接口创建新 revision；补证不会覆盖旧候选或洗掉旧问题。
- 来源冲突只有在当前独立审核达到固定门槛、明确引用此前支持和反证并核验适用范围后才能解除。旧反证、原票和处理记录保留。
- 同一候选、同一修订启动新 audit 时继承未解决的语义义务及有关原文，不能通过重跑丢掉问题。预算/网络等执行问题不会被当作永久语义问题继承。
- 召回材料保留在 archive。重要来源义务要求覆盖；有独立相关性判断的无关材料可保留排除依据后移出最终包。后续出现支持或反证引用时撤销旧排除，保留变更记录。

### 完整本体查询与扩展分支

- `inspect_ontology` 接受 nullable `relation_id`、原始关系、query 和端点类型，检查完整冻结本体的正式 ID、标签/声明别名和端点类，返回定义及有限召回建议。建议不是映射确认，已有项/歧义不能直接冒充新属性。
- `request_ontology_extension` 必须先有当前提案的原文支持共识和完整本体查询。新增属性/类要求相应未覆盖诊断；约束/定义修订及合并要求独立的疑似错误/不兼容诊断，并标为高影响。guard 的 fail 保持不变。
- 扩展是独立 `ontology_extension_tasks` 任务，不创建假的三元组。按 base release/profile、变更种类、来源关系和端点去重；不同触发候选各有订阅，草案建议与 audit/round/digest/证据引用均保留。
- 创建订阅使用候选 revision/row_version/human_status 的 CAS 检查。最终决定提交后同步订阅的 row_version；人工修改、拒绝、延期或确认会撤销旧订阅。
- 保存 `awaiting_ontology_review` 后结束 worker；批量审核即使 force 也跳过正在等待的候选，避免空轮询和反复花费。状态查询支持 `awaiting_ontology_release/reaudit_required/schema_unresolved`，但本轮没有执行本体审核、发布或自动唤醒。
- 请求记录 `ontology_review_executed=false`、`auto_publish_enabled=false`。原文共识不是本体批准，也不是正式入库依据。

### 最终决定、API 与页面

- 决定保存 `selected_round_id/selected_proposal_digest/selected_evidence_hash` 和委员会、本体绑定。提交和自动导入都核对冻结内容，导入只采用所选轮次中符合绑定与身份要求的票，不能混合历史轮次。
- 缺少精确选择信息的旧自动决定不能通过导入适配器猜测证据；需要重新审核或人工明确处理。历史记录保留。
- 新增候选 `workflow_state/pending_extension_id`，与 job 执行状态和人工状态分开。等待本体或 Schema 未解决时不产生新的自动入库 outbox。
- `GET /api/triplets/items` 支持 `workflow_state`；summary 返回 `workflow_states`。审核详情返回问题事件。`GET /api/ontology/extensions/{extension_id}` 只读展示请求及订阅。
- 三元组审核页增加流程筛选、原文/准入共识、问题历史和本体请求详情，不显示虚构的第二 Agent 审核/发布成功。

## 迁移与启用

迁移号为 4。`ReviewRepository.initialize()` 在发现旧候选表时先使用 SQLite backup API 生成 `migration_backups/*.pre_v4.*.sqlite3`，再增量增加两列及三张表。新增生命周期 DDL 和迁移标记在事务内提交，重复初始化幂等，不覆盖原候选、旧票或旧决定。

本次没有初始化或迁移正在使用的 `runs/v3/review.sqlite3`。迁移演练对该库以只读连接做一致性副本：原库有 2,645 个候选、1,610 次 audit、5,031 张票；副本升级后现有行数保持不变，`integrity_check=ok`；将备份还原到另一副本后，全部表的逻辑摘要与原库一致。运行/重启使用新代码的服务时，现有初始化入口会执行带备份的迁移。

回退应先停止使用数据库的进程，将 `pre_v4` 备份恢复到独立路径，核对完整性和备份时间，再切换数据库路径。不要在有新审核写入后直接覆盖数据库；备份之后的新事件须先保留。

迁移与离线样例报告：

`runs/review_validation/upgrade_v4_35e8fab9754f43c5913c6541340757ab/validation_report.json`

演练脚本：`python -m scripts.validate_review_upgrade_v4 --database runs/v3/review.sqlite3`。它只操作只读源库的副本，使用临时/独立审核库和可控 Mock 响应，不调用真实模型或 Neo4j。

## 验收与边界

新增 `tests/test_review_enhancement_v4.py` 覆盖真实循环补证、条件错误不可洗白、反证复核、跨 audit 义务、精确轮次导入、nullable 完整本体查找、类缺口/约束复核请求、任务聚合、订阅撤销、等待 worker 释放、任务/版本重放、重复模型、契约绕过与迁移恢复。对应本阶段的 T08、T09/T10 三元组侧、T22–T28 及 T38 三元组侧；不宣称通过全部 40 项。

最终验证：全部 239 项 Python 测试通过（新增 28 项）；前端 9 项测试通过；Vite 生产构建通过；`git diff --check` 通过。离线完整提案样例位于上述报告同目录的 `current_contract_smoke/`，两条候选都形成原文支持共识；已覆盖关系保持 shadow 人工路由，缺口候选保存独立待审任务并释放 worker，入库 outbox 为 0。

再次核对生产库逻辑摘要与迁移前一致，生产本体仍为 `release_d0a02b846433380cb05473219174df431d53e483c13d5cc0281c9dac02528d71`，`ontology/ontology.json` 的 SHA-256 仍为 `fe94b15a6f36719cb4b55e223559f3189e6529cd99fd1bca4029d0b2c43de9d8`。

仍待后续实现：本体专用工具与 `ontology_review_response_v1`、共享任务适配器和跨 Agent case 总预算、差异提案的独立审核、发布后候选受控重绑定和自动唤醒、新 release 下的重新审核，以及完整 Fact/Contribution 对账（阶段 D/E）。当前等待任务只表示真实保存的请求，不表示这些步骤已经完成。

本轮验证是控制流、版本安全和数据迁移验证。真实模型质量、真实委员会服务可用性、OWL 推理、Neo4j 图状态和准确率没有由这些离线测试证明。默认继续 shadow，自动接纳/拒绝和自动发布均未开启。
