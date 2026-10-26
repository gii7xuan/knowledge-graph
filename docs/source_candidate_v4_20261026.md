# 文档来源与候选抽取改造

日期：2026-10-26。对应修改方案 final_plan_v2 的第 6、7、11、12 章，以及审核与存储字段贯通要求。本次完成文档来源和候选抽取阶段；没有启动整书模型抽取，没有调用真实模型或连接 Neo4j，也没有覆盖历史数据库或本体 V1。

## 已完成

| 要求 | 实现 |
| --- | --- |
| 共享规范全文 | `document/snapshot.py`；仅去除 UTF-8 BOM 和统一换行，保留链接、列表、公式、代码围栏与原始用词。原始文本和规范文本分别保存 |
| 可核验来源 | document_version 为规范文本 SHA-256，另存 raw_file_hash、normalizer_version、snapshot_id、structure_hash 和 index_content_hash |
| 真实全文坐标 | unicode_code_point、左闭右开；明确 PDF 字符映射 unavailable，不声称提供 PDF 精确坐标 |
| 先定位标题再切块 | `index_builder.py`；同名标题按原文位置区分，支持 ATX、Setext、MinerU 标题并识别代码围栏 |
| 全文分句 | 共用全文句子 ID，支持中英文标点、常见缩写和小数；长句跨块时保留原句身份和切片范围 |
| 上下文补证 | 新索引下 inspect_context、证据上下文 API 可跨块读取同一章节的相邻全文句子，保留章节边界 |
| 索引完整性 | 加载复算来源、结构与索引摘要，并与 run 的 source_index_hash 比较；正文、偏移、结构篡改会失败 |
| 本体引导抽取 | `prompts/extraction_v4.txt`；由领域配置和发布本体提供上下文，不固定“火炮专家”或 14 类，不强行替换关系 |
| 完整限定 | polarity、condition、time_scope、applicability、modality、quantifier 及各自 qualifier_status；漏填极性为 unknown |
| 原文引文 | evidence_quotes 必须逐字定位后才能获得 evidence_id；找不到或存在多处歧义时保留候选并标为待确认 |
| 来源候选优先 | 默认路径绕开旧的置信度后处理和全局事实去重；不同来源分别登记，同一来源相同输出才去重；重叠上下文不单独产生来源候选 |
| 本体外知识保留 | 未匹配关系保留 surface_relation、建议和来源，relation_id 保持 null，进入后续审核；模型建议 ID 不直接成为标准 ID |
| 统一身份 | 共享 RelationRegistry 使用标准 ID 或声明且无歧义的标签/别名；实体使用区分大小写的来源内身份，不按相似名称跨文档合并 |
| v4 契约与事实键 | 保持外围 SQLite 与审核运行结构，候选为 extraction_candidate_v4；限定、实体身份、typed literal、语言及命名空间参与事实键 |
| 临时身份 | 未确认时存内部 provisional 占位；API 返回 fact_key=null。新修订重建局部实体身份并使事实键重新待确认 |
| 下游字段贯通 | 修订白名单、planner 参数、validator payload、提案摘要、API、页面和评价统计传递完整字段，不丢模态或量词 |
| 请求与覆盖记录 | 保存实际范围、请求/模板摘要、输出契约摘要、请求/返回模型标识、参数、原始响应、失败、重试、截断、时延与可获得的 token 用量 |
| 截断恢复 | 不再任取最重要 8 条；在总调用预算内将不完整范围细分。无法恢复时退出码 2，留下不完整状态和未请求范围 |
| 中断与原子登记 | 调用前保存来源快照与完整范围清单，每个请求开始/结束追加日志并刷盘；候选池与 run 在同一个 SQLite 事务登记，避免成功登记半个池 |

## 使用方式

默认抽取入口现在生成 v4 候选，要求使用已发布本体：

```powershell
python -m extract_triplets.extrct --input path/to/document.md --output-stem sample --ontology-release current --review-mode shadow
```

可以指定具体 release 目录。`--max-chunks 3` 可用于限制实际模型范围，其他范围会记为未请求，运行状态为 incomplete，不会宣称整本文档已抽取完成。串联入口 `scripts.run_ontology_then_extract` 同样接受 `--max-chunks` 并传给抽取器。

将 [增量配置示例](../config/extraction_v4.example.json) 的 extraction 部分加入现有审核配置即可调整抽取策略；无需替换完整 review config。默认每文档最多 64 次实际调用（包含失败和格式重试）、每请求最多 2 次尝试、细分最多 3 层、输出上限 4096 token。达到上限时保存未处理范围，不静默忽略。

目录按明确政策不生成事实；真正的习题章节默认 defer，可配置 include。正文中的“假设”、短术语、符号、图注、表格不会仅因形式被屏蔽。模型对 coverage_status 的声明不是覆盖证明。

历史路径仅通过 `--legacy-extraction` 显式使用；它继续用于兼容，不具有 v4 的全部语义保证。旧索引可读取，但标记 legacy_unverified；不能用于声明 v4 的来源已验证。旧候选在 API 中标记 legacy_semantics_unverified，此标记不改写旧决定或人工标签。

## 保存的制品

每次 v4 抽取先创建 `runs/v3/<run_id>.pending/`，保存 source_index.json、scope_manifest.json、status.json 和逐请求 requests.jsonl。中断时仍保留这些记录；本阶段不自动重发中断的未知结果请求。重复使用同一 run_id 会被拒绝，避免覆盖来源或重复消耗。

完成后 `runs/v3/<run_id>/` 保存：

- 完整 document_index_v3、原始 `.raw.md`、规范 `.canonical.md`。
- 整套本体 release 副本与 release/profile 绑定。
- 提示模板副本与摘要、请求记录和 extraction_report.json。
- SQLite 中的来源候选修订，以及 exports/candidates.json。

兼容导出的 accepted/rejected 为空，来源候选进入 review_queue；本次抽取不会自动产生正式贡献。候选事实键为 canonical 只表示身份可计算，不表示原文事实已被独立审核。

## 验证结果

- 新增来源专项 27 项通过；全量 Python 回归 211 项通过，45.324 秒。最后修正切分参数的版本标记后，27 项专项再次通过。
- 前端 9 项测试通过，Vite 生产构建通过。
- 测试覆盖 T05/T06/T07/T29/T30/T40 对应的身份、限定、来源和坐标要求，以及跨块条件、同名标题、英文小数缩写、大小写敏感符号、超过 8 条候选、截断恢复、失败分母、预算、中断刷盘和事务回滚。
- 全量旧测试涉及的临时目录、前端构建的 esbuild 子进程在沙箱内受限，已在允许的环境验证。

机器可读核对记录见 [验证摘要](source_candidate_v4_verification_20261026.json)，同时复核了生产本体 V1 和原始本体文件摘要未变化。

可重复执行的离线样例：

```powershell
python -m scripts.validate_source_candidate_v4
```

本次最终样例目录为 `runs/source_v4_validation/1ea1b21ce6c84bdc96a59757f582fe9c/`。其 [运行清单](../runs/source_v4_validation/1ea1b21ce6c84bdc96a59757f582fe9c/runs/v3/source_fixture_v4/manifest.json)、[候选池](../runs/source_v4_validation/1ea1b21ce6c84bdc96a59757f582fe9c/runs/v3/source_fixture_v4/exports/candidates.json)和[核验报告](../runs/source_v4_validation/1ea1b21ce6c84bdc96a59757f582fe9c/validation_report.json)均可检查。

样例使用独立合成本体、固定响应和独立 SQLite，保留 5 个来源候选：同一条件/能力事实的两处来源、一个否定、一个带单位字面量、一个未匹配关系。5 个来源锚点全部核验通过；6 次请求均为模拟调用，真实模型调用与 Neo4j 连接均为 0。模型没有报告 token 用量，因此报告明确记录 unknown，不以已知用量之和 0 声称实际零 token。

## 后续阶段边界

本次没有把审核 Agent 重写为固定流水线。问题生命周期、原文支持与准入双维共识、本体扩展任务属于后续 C/D 阶段；统一准入与完整 Fact 投影属于 E 阶段。

现有 Neo4j 适配器对否定、条件、模态、量词及字面量 v4 候选会明确拒绝不支持的投影，不将其扁平为无条件肯定边。临时身份不能进入 Fact 聚合。完整 Fact 投影仍需后续实现，不能把本次保存候选表述成这些事实已入图。

句界是确定性规则，对复杂缩写、特殊表格/公式仍需实际语料核对；当前工程测试不证明真实模型的抽取准确率、限定识别率或完整覆盖率。真实实验应在冻结来源范围和候选全集后单独进行。
