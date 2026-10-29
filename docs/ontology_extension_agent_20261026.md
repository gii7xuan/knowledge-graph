# 本体扩展审核 Agent：实现与验收

本轮实现修改方案阶段 D，接通当前项目 JSON 本体的“来源缺口 → 本体调查与独立审核 → 受控发布 → 新候选修订重审”。保留三元组 Agent 和现有三席服务，两个规划角色使用独立提示与状态，两种审核任务使用不同契约和票据。默认仍为 shadow，自动发布关闭；本次没有调用真实模型、写入 Neo4j 或更改生产本体。

## 运行链路

1. 三元组 Agent 在原文支持共识、完整本体查询和候选表达门槛满足后创建真实扩展任务。启用 `ontology_extension` 时同时排入专用 job，三元组 worker 结束等待，不占用线程轮询。
2. 本体 worker 重新打开触发提案、核对选中轮次、修订、证据及来源索引；只使用仍符合订阅 CAS 条件的候选。全文或锚点不一致时停止。
3. 独立规划器依据实际工具结果调查已有表达、定义、分类、方向、约束与影响。`propose_schema_delta` 保存完整差异，新增标准 ID 由服务端分配；每项操作必须有当前来源引用。
4. 对完整 base+diff+合并后结构+证据+检查记录运行固定委员会。每位审核员审核完整对象，不读取其他委员当前票，三元组票不能用于本体批准。
5. 服务器复算检查和共识，shadow 只保存建议。显式人工发布绑定准确的 task row_version、base release、所选提案和理由；高影响变更还须核对影响清单。
6. 发布采用现有 `ReleaseCatalog` 的 base CAS，不覆盖旧 bundle。成功后按订阅中的 revision/row_version/human_status 检查创建 `reason=ontology_rebind` 的新修订，绑定新制品、重做标准映射和 guard，并排入三元组重审。旧票不能成为新版准入票。

## 实现模块

| 模块 | 职责 |
| --- | --- |
| `ontology_review/contracts.py` | 动作契约、`ontology_review_response_v1`、提示和内容摘要、任务票据绑定 |
| `ontology_review/runtime.py` | 真实规划—观察循环、预算、工具记录、checkpoint、精确最终轮次、恢复 |
| `ontology_review/tools.py` | 本体检索、定义补证、邻域、差异、结构、推理器、影响、能力问题及审核工具 |
| `ontology_review/delta.py` | 七类差异的显式应用、服务端 ID、引用/层级/别名/数据类型/profile 检查 |
| `ontology_review/evidence.py` | 重新核验原提案和不可变文档坐标，保留历史建模证据 |
| `ontology_review/validators.py` | 独立模型调用、专用缓存、响应及引用检查、固定分母和模型身份去重 |
| `ontology_review/repository.py` | 独立本体 audit/proposal/vote/step 表、共享 case 用量及发布意图 |
| `ontology_review/publisher.py` | 发布前复查、事务意图、base CAS、显式 rebase、恢复和受控唤醒 |
| `ontology_review/service.py`、`__main__.py` | HTTP/CLI、专用 job、可终止进程、取消和硬超时 |
| `review_agent/task_adapter.py`、`case_budget.py` | 共享任务协议、三元组适配器和跨 Agent 持久预算 |

复用现有 JSON LLM 客户端、参数化后的 `LLMPlanner`、`AgentBudget`、SQLite 仓储连接、job 租约、制品发布器和来源检索。没有创建假的三元组迁就旧外键，也没有另建一套事实数据库。

### 工具与校验

实现方案中的十个本体工具：`search_existing_schema`、`retrieve_definition_evidence`、`inspect_schema_neighborhood`、`propose_schema_delta`、`validate_schema_structure`、`run_reasoner_checks`、`analyze_schema_impact`、`run_competency_checks`、`run_ontology_validation`、`finish_extension_review`。

支持 `add_class/add_object_property/add_data_property/add_alias/revise_definition/revise_constraint/merge_terms`。新增项要求定义及适当端点/数据类型；变更只修改明确列出的字段。类/属性种类、悬空引用、父类循环、别名冲突、不支持的执行字段及数据类型不能被模型自报 pass 绕过。完整查重扫描基线，近似匹配只作为召回建议。

影响检查扫描全部当前候选，列出受影响项及新 guard 结果，不把“已分析”解释为旧事实自动通过。能力检查运行触发候选和有明确定义的结构化 CQ；自然语言 CQ 记录 unknown，空实例数据不当作建模错误。

`run_reasoner_checks` 默认返回 `not_run/executed=false`。可在受信配置 `ontology_extension.reasoner.command` 提供 argv 数组形式的外部适配器；工具以 JSON stdin 传入合并后本体和 base/diff/profile 摘要，使用无 shell、隐藏进程及超时调用。报告必须返回相同绑定，以及 `engine/executed/status/consistent`；失败、超时、缺绑定不能变成通过。`require_reasoner=true` 时 not_run/unknown 阻断批准。本轮没有运行真实 OWL 推理器。

### 独立票与重投约束

每张票绑定 ontology task、delta revision、audit/round、proposal digest、base release/schema/profile、diff hash、完整来源证据摘要、委员会与契约。审核包含必要性、复用/重复、定义、层级/方向、约束强度、结构、影响七项完整检查。approve 要求来源引用及完成的建模检查；确定性结构/影响/CQ 门槛由程序复算。

委员缺席、服务错误、格式/引用错误和重复实际模型身份不缩小分母。记录请求模型、服务返回的 model/response id；未报告的实际版本保持未知，不把不同家族当成统计独立性证明。

本体请求使用 `ontology_validation_single_flight_v1` 命名空间。改 diff、证据、base、契约或委员会后旧票失效；相同完整输入复用已完成有效票。单纯改变搜索 query、历史反证标签或人工“重看”意见不能制造有利重投。人工要求修订后，必须有实际差异或证据变化并重新检查，旧批准不能直接发布。旧反证及其可定位原文保留，新提案必须明确复核；不会把一次少数反证写成无法解除的永久否决。

### 预算和恢复

`review_cases` 按 candidate_id 关联所有 revision、两类 Agent 及发布后重审。默认上限为 96 次模型调用、240,000 个已报告 token、64 次工具调用、1 次扩展请求和 2 次重绑定。新版本和新 audit 不清零 case 账本。

调用先持久预留，明确未调用/命中缓存时退回；未知结果不自动重发。共享扩展只记录一次任务成本事件，并对每个参与 case 记全额成本，避免靠分摊扩大预算；报告明确该保守口径。token 只累计服务上报值，缺失上报在 Agent 的 unknown 请求数中保留，不能把账面 0 当作真实免费调用。升级时从历史尝试及未完成 checkpoint 预留重建已有用量，旧断点退回本地额度不会错误扣减历史成本。

发布前先在 review DB 保存意图，再调用 release catalog。若 catalog 已提交而唤醒前崩溃，重试同一意图恢复，不重复发布或重复创建新修订。另一版本抢先发布时转 `needs_rebase`；显式 rebase 更新 base 和 delta revision，保留历史，重新调查/投票。人工修改或撤销订阅后，旧 worker 不会覆盖新候选。

## 页面、接口和使用

本体审核页增加扩展任务区：状态、真实规划/工具步骤、固定委员票、检查记录、完整 diff、影响清单和跨 Agent 预算；支持启动/恢复、要求修订、拒绝、显式发布和更新基线。原初始本体导入/发布页面继续保留。

- `GET /api/ontology/extensions`：任务列表及启用状态。
- `GET /api/ontology/extensions/{id}`：详情、历史 audit、票和可发布预览。
- `POST .../{id}/review`：创建/恢复专用 job。
- `POST .../{id}/decision`：带 row_version 和理由的 revise/reject。
- `POST .../{id}/publish`：绑定所选提案和 base 的发布请求。
- `POST .../{id}/rebase`：绑定当前 head 的显式重新设基线。
- job 详情/取消沿用 `/api/triplets/audit-jobs/{job_id}` 的共享接口，job_type 区分类别。

CLI：`python -m ontology_review list`；`show/review/preview` 配合 `--extension-id`，`review` 可传 `--audit-id` 恢复；`publish/rebase/reject/revise` 使用 `--request` 指定含预期版本、理由及确认字段的 JSON。所有 CLI/服务初始化均使用带备份的迁移入口。

当前配置启用第二规划角色，复用原三席独立审核服务。`ontology_extension.planner` 可分别使用 `KG_ONTOLOGY_PLANNER_MODEL/FAMILY/API_KEY/BASE_URL` 环境变量，不复制凭据。自动发布仍为 false。配置加载器统一方案中的 `agents/committees/routing.mode` 别名，发现冲突报错。

## 迁移与离线验收

迁移号为 5。在旧候选 workflow 表已存在、Agent 表尚不存在时先生成 `migration_backups/*.pre_v5.*.sqlite3`；从更早的 v3 库升级会先生成 pre_v4 快照。新增表与迁移标记增量提交，原候选、原审核票和旧决定保留。

本次只对生产库的只读副本演练：2,645 个候选、1,610 次原 audit 和 5,031 张原票保持不变，完整性检查为 ok，备份还原副本的全表逻辑摘要与原库一致。没有在生产库执行迁移。

可复查样例：

`runs/review_validation/ontology_extension_1ce3d78e3caf46ccab695745b72f66db/validation_report.json`

同目录保留来源审核、本体审核、发布前后对照、发布结果、新 release 下的三元组审核、独立 SQLite 和不可变制品。演练中 revision 1 变为 revision 2，使用新的三元组提案和票；shadow 最终仍为 manual_review，入库 outbox 为 0。发布只发生在该 fixture 的 release root。

重现：`python -m scripts.validate_ontology_extension_agent`。脚本调用集成测试夹具、真实工具、仓储及发布代码，模型全部 Mock；可用 `--skip-migration` 跳过生产库只读副本演练。

专项测试覆盖自主观察循环、七类差异约束、固定委员会、任务/版本隔离、超时/余额暂停、缓存不可重投、历史反证、人工修订、发布竞争/rebase、崩溃恢复、订阅 CAS、完整限定保留、新修订新票、共享预算、旧断点及迁移恢复。另通过本地模拟 HTTP 服务和真实 SDK/后台进程检验协议接线，不能据此声称真实服务可用或质量提高。

最终验证结果：全量 280 项 Python 测试通过，其中本轮新增 41 项；前端 9 项测试通过，Vite 生产构建通过。另直接验证了批准别名映射、integer 字面量属性及类合并后的引用更新。迁移副本完整性检查与恢复摘要比对均通过。生产库逻辑摘要仍为 `8ea70462dd9dec5bb0c54763f7f945c7f815b5135543ec2500884677f6cd3799`，生产 release 仍为 `release_d0a02b846433380cb05473219174df431d53e483c13d5cc0281c9dac02528d71`。

## 执行边界

- 2026-10-26 的发布器仅支持项目 JSON profile。2026-10-27 已补齐保留完整 RDF 来源的 OWL 扩展发布适配器，详见 [OWL 扩展发布说明](owl_extension_publication_20261027.md)；本报告其余测试数字为当日历史结果。
- 未配置推理器时不提供 OWL 一致性结论；不支持自动执行的自然语言 CQ 保持 unknown。高影响操作始终需要明确人工核对，默认 shadow 不自动发布。
- 全部闭环数据和模型回复为验证夹具，不是准确率实验；真实三席服务的在线健康和领域质量尚未测量。
- 本轮接通版本与审核流，不实现阶段 E 的完整 Fact/Contribution/Neo4j 并发对账，也不把本体批准视作事实接纳。
- 当前运行中的服务需重启以载入新代码；初始化会先备份再迁移到 v5。生产本体 release 未被本次验证更改。
