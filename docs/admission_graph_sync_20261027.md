# 统一准入与图谱同步（方案 E）

实现范围为方案第 12—14、16 章及 T31—T35：自动审核、人工审核和导入调用相同准入检查，Neo4j 按当前有效贡献对账。默认配置继续保持 shadow，自动接纳和自动图谱导入关闭。

## 准入规则

`review_agent/admission.py::validate_admission` 检查当前 v4 修订、决定绑定、已发布本体的 release/version/profile、实际来源快照与索引摘要、标准关系和端点类型、来源内实体 ID、完整六项限定、有效原文锚点、支持判断、反证处置与 canonical fact_key。人工可以替代模型语义判断，但仍须通过来源、本体与版本检查。

自动路径额外核验最终提案的完整摘要、固定委员会的当前票、未解决问题、冻结策略摘要、校准器就绪状态、概率与阈值。提交时再次核验；过期、超时或已取消的审核不能覆盖当前修订或人工决定。单纯提交 `auto_accept`、概率、`review_event_id` 或缓存准入标志都不能获得导入权限。

`admission_bundles` 保存不可变证据与判断，`admission_authorizations` 将准入报告绑定到具体决定和候选内容。导入重新读取实际制品并复核授权；来源、提案或本体被篡改时，当前期望贡献变为空，并生成更高投影版本。

新候选使用 `fact_identity_v2`。事实键包含稳定端点身份、正式关系 ID 与关系语义摘要、typed literal、完整限定和身份版本。显示名不参与；关系定义或端点类约束发生变化时不会混入旧 Fact。数量保留 datatype、单位、语言，兼容的十进制词法形式会规范化。当前实体采用保守的来源内局部 ID，不按相似名称跨文档自动合并。

历史候选和决定原样保留。缺少已核验 v4 来源与准入授权的旧接纳记录不能继续作为新导入凭证；需要重新抽取并审核，不能给历史记录批量补一个通过标志。

## 人工页面和接口

工作台支持选择当前审核提案中的证据，逐条标明支持、反驳、上下文或无关，并填写依据。反证必须保留并明确给出处置说明与支持引用；涉及候选表达的问题须先建立新修订。支持编辑全部限定、识别状态以及字面量的类型、值、单位和语言。

没有可用模型提案时，可填写规范全文中的逐字引文和出现次数。服务器从已验证全文定位，不接受客户端自行编造的 quote/offset 组合。

1. `POST /api/triplets/candidates/{id}/human-bundles`，绑定 `revision` 和 `expected_row_version`。
2. 选择精确提案时提交 `audit_id`、`selected_round_id`、`selected_proposal_digest`、`selected_evidence_hash`、`evidence_ids`、`evidence_assessments`；从全文选择时提交 `spans`。
3. `POST /api/triplets/decision` 提交 `human_bundle_id`、当前版本、幂等键与人工动作。接纳也可直接提交上述证据选择，由服务器创建并验证包。

空证据、虚假 ID/引文、跨候选或过期证据包返回 422；候选修订或 row_version 并发变化返回 409。相同幂等请求返回原事件。modify 创建新修订并重新计算身份，旧票不再授权新修订。

## 图谱当前状态对账

`graph_outbox` 与旧 `import_outbox` 仅提供 candidate_id 触发。`current_admitted_contributions` 从 SQLite 当前修订、最新有效决定和授权计算期望贡献，不重放旧 payload，也不会因决定已更新而跳过旧撤回所需的清理。

每个候选有单调的 `projection_version` 和状态摘要。一次 Neo4j 写事务会：

1. 锁定 CandidateProjection，拒绝旧版本；同版本不同摘要报错。
2. 找到该候选所有旧贡献，包括旧版 `SUPPORTS_FACT`、`FACT_SUBJECT`、`FACT_OBJECT` 结构。
3. 按固定顺序锁定受影响 Fact、实体、字面量和共享证据，然后停用旧贡献与旧判断。
4. 仅激活当前决定的贡献，并按所有有效贡献重算受影响 Fact 和直接边。
5. 最后保存投影版本；SQLite 确认只覆盖已完成的版本。决定在写图期间变化时继续读取新状态，不执行可能误撤回新贡献的补偿操作。

贡献区分 candidate、revision 和 decision；同修订的新人工决定也会停用旧判断。Neo4j 提交后、SQLite 确认前崩溃可安全重试。全量 `--scan` 不依赖 outbox，能够补偿遗漏触发并重建缺失派生边。同版本相同状态仍执行幂等修复。

Fact 是查询权威。否定、条件、时间、适用范围、能力/可能性、显式量词保存在完整 Fact 中；字面量使用 LiteralValue。仅无显式限定、肯定、断言模态的实体事实生成带 fact_key 的 `KG_FACT` 边。来源锚点与带审核者、候选、提案归因的 EvidenceAssessment 分开保存，不把一次模型判断写成来源的永久语义属性。来源锚点数、候选贡献数和委员数分别统计。

锁机制使用事务内依赖属性更新获取写锁，不能假设默认隔离级别提供串行化，依据 [Neo4j 并发访问说明](https://neo4j.com/docs/operations-manual/current/database-internals/concurrent-data-access/)。驱动事务会重试可重试的并发错误。

## 使用

安装驱动：`python -m pip install -r to_neo4j/requirements.txt`。已有 Neo4j 连接环境变量继续适用。

```powershell
# 查看当前期望状态；会初始化 SQLite 和冻结对账版本，不连接 Neo4j
python -m to_neo4j.save_to_neo4j --run-id <run_id> --dry-run

# 根据当前权威状态应用，而非按事件历史盲写
python -m to_neo4j.save_to_neo4j --run-id <run_id> --apply

# 周期全量补偿任务应调用此入口，运行频率由部署环境设置
python -m to_neo4j.save_to_neo4j --all-runs --scan --apply
```

`--database` 指 SQLite 文件，`--neo4j-database` 指 Neo4j 数据库名。导入范围需显式指定 run 或 all-runs；可同时处理不同发布版本的候选，每条贡献单独验证其 release。`to_neo4j.query_facts.query_facts` 返回当前完整 Fact、限定、字面量、发布版本集合、有效贡献与证据判断，支持实体 ID、关系 ID 和数量筛选。

## 迁移与验证

schema 6 增量增加证据包、准入授权、投影状态和 outbox 表。升级前创建可恢复备份，不覆盖候选、历史票或决定；已有接纳/导入记录会进入待对账队列，而不会自动取得新授权。恢复演练在副本上执行，生产数据库仅只读打开。

- 完整 Python 回归日志：`runs/phase_e_tests_verified.log`（303 项，14 项隔离 Neo4j 场景由独立入口另行执行）。
- 汇总报告：`runs/review_validation/admission_graph_20261027.json`。最终人工反馈回归 59 项通过；独立 Neo4j 验证 26 项通过，报告保存了实例版本、独立目录和停止状态。
- 前端 9 项测试和 Vite 构建通过。
- 真实 Neo4j 2025.11.2：`scripts.validate_admission_graph` 使用独立数据目录、随机回环端口运行后停止进程。覆盖版本切换、旧撤回触发、共享 Fact 撤回、并发、重复/乱序/旧 worker、事务回滚、提交后重试、同修订新决定、旧图结构清理、限定与字面量、周期修复及 HTTP 422/409。
- 迁移与恢复报告：`runs/review_validation/admission_migration_9770d81dc93c404b8c7ea910781a0e68/validation_report.json`。历史行数保留、完整性检查 ok、恢复副本与原库逻辑摘要一致、原库未变。
- 离线 demo 的六项检查全部通过，使用真实 v4 来源和隔离本体发布；模型判断与校准响应为 Mock，仅验证控制流，不代表真实模型准确率。

本次未修改生产 SQLite、当前本体发布或生产 Neo4j；真实图谱测试不连接默认生产端口。后端首次使用新代码时才对实际配置的数据库执行增量迁移。自动入库仍需显式启用及有效校准策略；本次不启动真实模型质量实验。
