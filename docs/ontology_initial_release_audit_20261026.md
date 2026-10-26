# 初始本体接入与版本发布核对

日期：2026-10-26。依据用户提供的 `kg_quality_implementation_plan_20261025.md`（final_plan_v2）。用户已明确本次只补齐初始本体接入和版本发布；扩展审核 Agent 与候选重审不在本次范围。

## 已发布 V1

- release：`release_d0a02b846433380cb05473219174df431d53e483c13d5cc0281c9dac02528d71`
- ontology_version：`9e323886218061df56ac60c7970b2fa0007c0ad98a7d718498e212a1e07b5718`
- profile_hash：`d02c9beb60ee9d15cce9f02918012932f8c055ed24b8518f0809bb79a63fa5ae`
- 来源：现有 `ontology/ontology.json`；原始字节 SHA-256 为 `fe94b15a6f36719cb4b55e223559f3189e6529cd99fd1bca4029d0b2c43de9d8`。
- [完整发布清单](../runs/ontology_releases/release_d0a02b846433380cb05473219174df431d53e483c13d5cc0281c9dac02528d71/release_manifest.json)。当前版本由同目录 `release_catalog.sqlite3` 的事务登记维护，发布历史有基线和单调版本。
- 这是 `legacy_quarantine_v1` 过渡基线：49 类、14 个对象属性、209 个数据属性；20 个冲突/悬空属性被隔离，完整来源仍随 release 保存。未新增占位类，未在冲突定义中任选一个作为真值。
- `publication_mode=shadow`、`auto_review_blocked=true`、`source_semantics_verified=false`。本次授权来自用户的发布要求，授权记录未冒充专家语义审核。

## 对照方案

| 方案要求 | 核对与补齐结果 | 实现/证据 |
| --- | --- | --- |
| §4.1 作者方法先接制品，保留旧构建 | 支持 NeOn TTL 与报告导入；旧 pipeline 保留。实际 V1 明确使用 legacy 原件，未声称运行作者方法 | `providers/neon_import.py`、`providers/legacy.py`；release 的 method manifest |
| §4.2 统一 provider 与 artifact | 已有公共请求、制品与 provider 接口；CLI 和工作台调用同类 provider | `providers/base.py`、`import_service.py` |
| §4.3 具名类、标签、定义、IRI | 保存 RDFS/SKOS 标签、定义、别名、IRI；IRI 映射不受同名局部名称影响 | `owl_to_project_schema.py`；OWL 样例测试 |
| §4.3 多父类 | snapshot 保存全部 parents；Guard 与子本体视图检查全部父类，兼容 parent 显示 | adapter、guard、subontology retriever |
| §4.3 对象/数据属性分开 | 分开存储，XSD 走字面量检查；错误类型不会通过 | guard 与字面量测试 |
| §4.3 domain/range 语义 | OWL 多条声明在应用 profile 下按全部满足检查；JSON 保留自己的 allowed_any。明确不是 OWL 推理器 | application profile、双 domain 测试 |
| §4.3 复杂表达式与属性特征 | 原始制品和报告保留；未执行语义阻断自动审核。补上旧 JSON 的 enum、required、基数等未执行约束记录 | 两类 provider、guard |
| §4.3 冲突不可静默覆盖 | 原始定义冲突先于 adapter 合并检查；受限 V1 仅隔离问题定义，不猜测补齐 | 原始 source、mapping_report 中 20 项隔离清单及前后差异 |
| §4.3 profile 差异确认 | 发布需要与 profile、mapping report 摘要一致的明确确认；改动后旧确认失效 | publisher、预览发布测试 |
| §4.4 完整制品和版本 | 来源、snapshot、profile、IRI 映射、报告、需求、CQ、方法与批准记录全部入包；ontology_version/profile_hash/release_id 分工明确 | 完整 release manifest |
| §4.4 未运行工具如实记录 | 推理器、外部校验、领域语义核验为 not_run；独立领域能力问题为 not_supplied，不伪造通过 | validation_report、competency_questions |
| §4.4 显式传递已发布版本 | 补上工作台入口；串联脚本、抽取器支持显式版本或当前版本；未来 run 与候选保存 release/profile 绑定 | workbench、extractor；隔离 SQLite 测试 |
| §5.1 旧版本不可覆盖 | 整包内容寻址、逐文件校验、目录身份校验；旧 run 不改标签、不迁移旧票 | 重复发布、篡改、目录身份测试 |
| §5.4 发布并发 | SQLite BEGIN IMMEDIATE 比较当前 base；不同提案竞争只能有一个成功，另一个需 rebase | 两个独立 Python 进程实际事务测试 |
| §5.4 重试与失败 | 完全相同制品和授权的重复提交不新增事件；失败不移动 head；预览后变化必须重新检查 | 幂等、失败回滚、冻结预览测试 |
| §15 页面与接口衔接 | 本体页面支持导入预览、差异确认、发布与历史；三元组任务展示实际 release | Vue、workbench API、前端构建 |
| §18 小样本验证 | 使用固定 TTL、独立 SQLite、模拟模型响应；本次没有启动整书抽取、真实模型或 Neo4j | 回归结果 |

## 此次补齐的具体遗漏

1. 工作台抽取此前未传 release，现与 CLI 使用同一发布版本选择逻辑。
2. 增加当前版本登记、发布历史、基线比较更新和相同操作重试幂等。
3. 应用范围差异的确认绑定具体摘要；工作台先冻结可核对预览，发布时不重新读可能已变更的源文件。
4. JSON 来源逐字节保存，来源 SHA-256 可直接复算；原始示例实例不进入可执行 schema。
5. 补上声明的类别别名、关系同名歧义、SKOS 定义、旧 JSON 未执行约束和跨进程摘要稳定性检查。
6. 校验同时覆盖 release 目录身份、制品内容、批准绑定和应用 profile，不只比较 snapshot。

## 明确保留的后续工作

Extended NeOn-GPT 作者生成脚本尚未运行，真实作者制品、独立领域能力问题和专家语义审核尚未提供。V1 不声称完成作者方法复现或完整领域语义验证。接入实际作者制品时应创建新导入预览，核对差异，再基于当前 release 发布新版本。

复杂 OWL 推理、最小影响范围的精细白名单仍采用显式受限策略，不把未执行功能写成通过。本体扩展 Agent、发布后候选重绑定和重审属于后续阶段；发布事件中的 `candidate_rebind_applied=false` 如实记录未执行这些动作。本次没有修改历史候选、投票、决定或图谱。

## 最终验证

- Python 全量离线回归：184 项通过、0 失败，39.372 秒。
- 前端测试：9 项通过、0 失败；Vite 生产构建通过。
- 实际 V1 逐文件完整性校验通过；原始 JSON 字节与当前源文件一致，摘要未变化。
- 当前版本登记、工作台读取及共享版本选择器一致，发布事件仅 1 条。
- 20 个隔离属性未进入正式关系注册表，自动准入保持关闭。
- 没有调用真实模型或连接 Neo4j。全量回归和前端构建在允许访问临时目录/启动构建子进程的环境完成。

机器可读结果见 [发布复核记录](ontology_initial_release_verification_20261026.json)。
