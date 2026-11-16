# Agent 审核改造交付记录（2026-11-14）

基于 `ebe0a820750e3ac98cf95e01af680513f9b9484e` 实现用户提供的新方案。改动在当前工作区，未提交、未发布。重点是让补证、定向复核、映射和修订的结果进入最终判断，同时保留原始票、反证和请求历史。

## 已实现的代码

| 批次 | 实现及边界 |
| --- | --- |
| A | `experiment_profiles.py` 集中构建 diagnostic / agent_resolution；规划调用接入独立请求进程、原回执复用、状态绑定和通道暂停。固定初始化、批准草案的审核/应用和满足义务后的结束由程序调度。技术待办与领域裁决分开呈现。 |
| B | `resolution_state.py` 为当前候选、正式定义、证据和政策生成派生快照；仅关闭复核覆盖的具体问题。新证据影响其他维度时新增核验义务。runtime、最终判定、报告与工作台共同消费快照；原票不覆盖。晚到材料可只读重新判断，不执行过期规划动作。 |
| C | 聚合共享定义任务并展示影响候选数；修复 datatype 属性转换丢失 definition / description。增加有实体定位、类型依据和竞争关系排除材料的 `propose_mapping`。批准草案仅在隔离目录应用，排队独立核验新版本；拆分子候选分别核验且共享根候选预算。本体发布保留现有人工门禁，晚订阅者在已发布版本重新绑定后仍需新票。 |
| D | 对已完整返回且有具体 missing finding 诊断的无效回复，执行一次独立完整语义复审；允许维持失败或未知。未知请求不能用该工具重发。结束前检查仍有资金和信息增益的合法动作；无进展、持续分歧或预算不足时保留明确终止原因和人工问题卡。 |
| E（工具） | 扩展原有质量报告：结案、准入/排除质量、技术和全部未完成率、分模型故障/用量、正确结案成本、真实人工分钟数。共享定义工时计入，缺工时不是 0。提供分层抽样和加权估计、按实际材料等价复用已署名人工标签的入口。 |

## 实验参数和入口

旧 diagnostic 的实际公开设置与历史 23 条运行保持一致：规划 3 步、工具 8 次、单轮完整核验、18 次候选调用、禁止自动修订应用和本体执行。

新 agent_resolution：规划 8 步、工具 16 次、每问题至多 2 次补证/复核、每根候选至多 1 个批准修订版本；单次审核至多 48 次调用、800,000 token、1,200 秒。跨修订和拆分子候选的根账户至多 64 次调用、1,200,000 token、40 次工具，最后完整三席审核预留预算。每根候选最多处理 8 个后续复核任务；耗尽后停为技术待办。三席和正式发布、图谱导入门禁没有放宽。

```powershell
# 新目录准备，冻结原 30 条中的前 3 条；准备本身不调用模型
python -m evaluation.agent_resolution prepare --output outputs/agent_resolution_pilot_20261114_r2 --limit 3
python -m evaluation.agent_resolution verify --output outputs/agent_resolution_pilot_20261114_r2

# 真实执行入口；只允许一次启动，禁止自动重启旧 launch
python -m evaluation.agent_resolution run --output outputs/agent_resolution_pilot_20261114_r2

# 零调用报告；可附真实人工标签及工时
python -m evaluation.agent_resolution report --output outputs/agent_resolution_pilot_20261114_r2
python -m evaluation.agent_resolution report --output outputs/agent_resolution_pilot_20261114_r2 --annotations <人工标签.json> --work-log <实际工时.json>

# 可复查的合成 Mock 四路径验证，必须使用新目录
python -m runs.validate_agent_resolution_offline_20261114 --output outputs/agent_resolution_offline_20261114
```

准备的 3 条真实样本总调用上限为 192；本次代码修改未启动真实模型样本。后续启动应明确采用这份新预算，不能把旧运行的 540 次授权当作已批准额外调用。

影子修订另外导出 `revision_review_packets.json`（原版本与全部子版本）、操作清单和人工评价模板。用 `--revision-annotations`、`--revision-assessments` 输入署名评价后生成 `revision_quality_report.json`；缺标签时实际应用数仍计入，成功率保持未知。比较用的父版本不会加入主报告分母。

## 验证记录

- 原有 44 项定向基线通过。扩大回归共 291 项：290 项通过，1 项本机 HTTP 桩启动等待超时；日志为 `outputs/agent_resolution_verified_tests_20261114.log`。该用例单独复验通过后，增加了测试冷启动余量，未调整业务请求超时。最终 29 项定向复验全部通过，日志为 `outputs/agent_resolution_final_paths_20261114.log`；新增历史修订兼容用例单独通过，日志为 `outputs/agent_resolution_ancestry_compatibility_20261114.log`。
- 集成覆盖：补证后精确结案、未覆盖限定继续阻断、反对复核保留反证、晚到/504/调用进程退出/重复回执/状态过期、missing finding 独立复审、共享定义影响与晚订阅唤醒、来源类型映射、批准修订后的新三席核验、无信息增益转人工。
- 新增只读恢复检查：原票哈希与数据库文件哈希保持不变；语义已完成的超时审核可生成派生建议，执行超时和技术待办仍然显示。
- 工作台 35 项测试通过，Vue 生产构建通过；日志分别为 `outputs/agent_resolution_frontend_tests_20261114.log` 和 `outputs/agent_resolution_frontend_build_20261114.log`。
- `outputs/agent_resolution_offline_20261114` 保存四条合成 Mock 工作流、独立 SQLite 快照、完整审核材料及零标签质量报告。它用于验证代码流程，不属于真实教材模型质量结果。

准备目录 `outputs/agent_resolution_pilot_20261114_r2` 已通过 verify，报告显示 3 条尚未开始、0 次模型调用、0 条人工标签。初版准备目录因报告兼容修正已标记 superseded，冻结文件未重写。历史保护检查见 `outputs/agent_resolution_protection_20261114.json`。

## 仍未完成的验收

当前有效人工真值仍为 0，实际人工工时未记录。真实样本中的可映射/可修订实例、质量改善及人工时间下降尚未验证；批次 C 的真实案例验收和批次 E 的效果验收仍待完成。不能以本地测试、合成样本结案或三席一致声称精确率提升。

`现代火炮自动机设计理论` 继续保留为用户确认未参与调试的独立材料；本次没有将它加入调试或把其未标注样本称为独立验证结果。独立集还需要真实标签、材料绑定和重复事实泄漏检查。

历史两批共 30 条、183 次调用的原始结果不回写；28 笔网关未知继续保留为待人工对账，原严格工程门禁不改为通过。新流程可以区分当前语义判断与历史技术对账，但不将未知执行、未知费用或缺票填成成功。
