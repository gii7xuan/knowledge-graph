# 共性问题闭环与回归准备（2026-11-13）

旧30条仅用于开发回归。每条归因见 case_causes.jsonl；知识缺口、合法分歧、技术失败和评价盲区分别记录，类别可重叠。

|编号|根因|代码|状态|
|---|---|---|---|
|C01|反证存在被无条件当成委员分歧|review_agent/review_axes.py:aggregate_axes|fixed|
|C02|同轮与跨轮恢复反馈不一致，瞬时失败覆盖契约修复要求|review_agent/request_execution.py:judge_typed|fixed|
|C03|同源文档及表面变体重复事实可跨验证分区|evaluation/split_integrity.py|fixed|
|C04|存在未知请求或空人口时，冒烟门禁可错误放行扩批|evaluation/regression_gate.py; runs/regress_v8.py|fixed|
|C05|冻结未覆盖本体审核及图交付代码；旧入口无法先准备再单次执行|runs/regress_v8.py:fingerprints; evaluation/frozen_regression.py|fixed|
|C06|冻结快照后晚到回复被丢弃，用量未回记，504被当成已知失败|evaluation/request_meter.py|fixed|
|E01|原文判断、必要修订、合理及可避免转人工建议缺独立指标与覆盖分母|evaluation/generalization_metrics.py; evaluation/review_quality.py|implemented; current truth remains unavailable|

30条存在知识材料缺项；20条存在模型维度分歧；5条存在契约或传输失败。以上来自历史轨迹，不是新模型结果，也不意味着各项都是代码缺陷。

B01保持1票批准、2票修订；未发布，不转用旧票。它不阻塞采用已批准本体的代码开发回归。

开发回归：outputs/dev30_frozen_20261113_r2/experiment.json。冻结原候选、原文、批准本体、提示与契约、代码、模型和预算；7条检查后仅跑剩余23条；未结算原请求阻断后续阶段；启动标记使用排他创建，不自动重启。

独立材料：outputs/independent_materials_20261113/。用户确认《现代火炮自动机设计理论》未参与调试。先冻结12窗口抽样规则，再按固定位置选择原文段落，不按模型结果替换。当前没有候选抽取结果，重复事实检查仍待抽取后完成。

新增指标保留实际标签来源、可判定分母及未知数量。转人工相关指标明确仅针对建议；缺少人工标签时值为null。当前不宣称独立语义准确率改善。

工程测试与真实重跑状态分别记录；新材料尚未发起模型调用。

全量871项检查发现6项错误，均已修复；最终140项定向兼容复验全部通过。23项隔离Neo4j集成检查未运行。SQLite 3.10错误分类与旧版投票展示兼容已补齐。详见 engineering_verification.json。

真实启动命令被自动审批拒绝，原因是目的地址及样本/原文外发授权不明确。未创建启动标记，审计0、请求0。具体目的端点、外发内容类别和预算见新轮 external_transfer_plan.json；取得明确授权前不执行。

用户随后明确授权重跑，已实际处理前7条，发起43次模型请求。3笔504原请求结果未知，门禁停止，剩余23条未启动。实际执行回执见 outputs/dev30_frozen_20261113_r2/execution_receipt.md。


## 用户指定人工挂起后的续跑结果

用户明确无法取得网关入口或日志，要求将不能对账的部分保留为未完成、需人工审核，继续剩余样本。新增 `evaluation/continue_regression.py`：只选择未启动、未请求的候选；旧未知请求及原票不导入新轮，不重发；排他启动标记防止重复启动；保留原540次总授权与每候选18次上限。23项离线测试通过。

剩余23条已全部处理，连同原7条共30条，未启动0条。两批共183次真实模型请求。原3笔和本批新增25笔结果未知请求保持未知；另有2份选中回复契约不合格，合计30项技术未完成部分、涉及24条样本，已导出逐条完整材料和空白人工标注模板。严格工程门禁仍为未通过，人工继续规则未把它改成通过，也未创造人工真值。

原时限内委员材料完整14/30；已有晚到原回执经离线校验可额外补齐9条，合计23/30；未改写原票或决策。当前建议为转人工21条、等待映射8条、排除1条，不作为语义准确率结论。旧数据库、历史材料与生产候选哈希未变；未自动发布或入图。

最终回执：`outputs/dev23_continuation_20261113/continuation_receipt.md`；技术人工审核清单：`outputs/dev23_continuation_20261113/manual_review_all/index.md`。独立新书材料仍仅完成抽样冻结，尚未做独立语义验证。
