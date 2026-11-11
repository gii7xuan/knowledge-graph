# 11月11日下一步改进

本轮以 `outputs/dev30_v8_full_20261111` 的30条封存材料为输入，复用现有审核、映射、字段补丁与修订服务。离线交付位于 `outputs/dev30_next_20261111_verified`。原数据库、报告、comparison及旧人工标签均有SHA256保护记录。

## 已确认根因与代码

- `review_agent/mapping_trace.py`：增加映射入口、逐项阶段原因及独立里程碑。16条等待映射均有缺失关系定义，8条同时有实体身份/类型材料缺项。7条已分配候选关系ID且结构约束通过，但完整guard为0；不能把候选ID视为正式语义映射。人工确认/正式登记无记录时显示未知。
- “正式有效映射0”原统计来自选定审核的四维 mapping.status，不是发布数量。尚未发现映射已验证成功却因关闭发布被丢弃的证据。
- DEV-019 的 field_standardization.plans 为空。Gemma把规则FREQ-001当成计划ID，现有来源绑定拒绝正确。新提示v4明确有限合法计划及无计划时的合法未确定表示，不替模型猜补。
- DEV-022 为真实反证角色/pass冲突。新提示明确pass表示未发现未解决反证，现有冲突校验不变。语义澄清v3单独留痕、仅解释、永远不替换通过票。
- DEV-024 声称matched却漏relation_id、competing_relation_ids，不能从注释猜ID。已有契约允许unassigned的null和空数组，新提示明确这些字段必须存在。
- `correction_context.py` 对大本体使用可逆记录表、共享字符串/URN前缀，逐值保留全部18个同名候选、定义、方向、domain/range与类层级；仅移除重复schema/模板和由本地计划验证的重复哈希包装。v2补丁使用紧凑JSON；旧v1不改变传输形状。
- `semantic_clarification.py` 保留全部证据值及完整三元组，去掉候选重复包装。冻结预算仍16,000 bytes，不截断决定语义的材料。已保存DEV-022/024的对应新输入约10,210/15,882 bytes，实际能否合格由新轮验证决定。
- `validator_contracts.py`、`response_contract_v6_2.py` 继续读取历史v3提示与v1补丁，按原哈希检查；未将历史票套用新规则重写。

## 离线交付

`blocker_breakdown.jsonl` 保存30条四维、有效票、全部阻塞和下一步；`mapping_trace.jsonl` 保存16条阶段材料与里程碑。`annotation_packets/` 为30份去掉模型建议的完整材料，`annotation_current.jsonl` 为精确绑定的空白人工模板。先独立判定，再看 `model_suggestions_after_annotation.json` 和 `prior_labels_after_annotation.json`；旧5条仅提供逐维对照，未自动改绑。

窗口反事实结果为90/120/150/180秒：25/26/27/27份完整提案。按持久请求开始到结果入账计算，DEV-028约149.87秒，距150秒仅约0.13秒；不能用worker内部149.4秒冒充完整结算时长。原90秒成绩不变，模拟不代表实测吞吐。

委员90次初审与9次字段补丁用量完整；规划91次中20次api_error缺用量，71次有报告。已报告委员1,882,815 token、规划1,542,890 token，合计3,425,705只表示已报告量，不能作为完整费用总量。无价格表不估金额。

`revision_validation.jsonl` 保存两条修订预览及版本差异。另在 `outputs/dev30_revision_followup_20261111` 的数据库副本调用既有RevisionService准备两条proposed草案：DEV-017关系修改需完整新版本语义复核；DEV-020仅提出重复频率词清理，保留原quantifier。完整frequency迁移仍是派生表达，不越过候选契约强行持久化。两条均未批准/应用，未继承旧票，尚不能评价真实修订成功。

## 验证与入口

最终两组后端回归87+81项通过，新增10项覆盖记录表null/缺键、字符串标记转义、完整本体无损恢复、反证保留、历史提示/补丁读取、150秒边界、缺ID不猜补、修订版本隔离。日志为 `outputs/followup_targeted_final_tests_20261111.log`、`outputs/followup_validation_tests_20261111.log`。未改前端。

```powershell
# 只读盘点，输出目录必须全新
& 'D:\miniconda3\envs\knowledge\python.exe' -X utf8 -B -m evaluation.review_followup --source outputs/dev30_v8_full_20261111 --output outputs/<全新目录> --old-annotations outputs/dev5_v8_human_20261110_150238/annotations.json --comparison outputs/dev30_v8_comparison_20261111_final/comparison.json

# 三条Gemma定向新轮；不带--run仅准备，最多6次调用，不凑三席
& 'D:\miniconda3\envs\knowledge\python.exe' -X utf8 -B -m runs.regress_missing_votes_20261110 --source outputs/dev30_v8_full_20261111 --derived outputs/dev30_v8_full_20261111 --samples DEV-019 DEV-022 DEV-024 --voters judge_b --current-correction-policy --output outputs/<全新定向目录> --run
```

本轮3条真实定向验证使用新的审核ID、提案摘要、v4提示、v2补丁政策与独立数据库，保持旧材料；不借旧合格票拼完整委员会。结果单列在 `outputs/dev3_contract_followup_20261111`，不能算全30条新复跑。

实际完成4次请求：3次初审、1次语义澄清，未超6次授权上限，未知/运行中请求0。DEV-024首轮契约合格（unassigned、null、[]）；DEV-019仍将unresolved finding与fail维度组合，继续无效；DEV-022仍混用实际反证与已排除反例引用，澄清响应没有可见JSON，继续无效。没有追加调用凑成功。

真实澄清暴露原有传输元数据沿用初审的问题，代码随后记录原始/澄清两套元数据，明确空响应及提供方截断的终态。补充47项复验通过；这项观测修复尚未再次真实调用。汇总与交付保护核验见 `outputs/dev30_next_20261111_verified/targeted_live_validation.json`、`delivery_verification.json`。

当前版本人工标签仍0/30。正式关系定义与实体材料、两条修订的独立审核及当前四维人工标注是后续门槛；尚未满足时不启动全30条冻结回归或新材料泛化评估。生产设置和历史记录保持原样。
