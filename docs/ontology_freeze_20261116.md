# 共同本体预实验冻结与映射验收（2026-11-16）

后续进展：错例回复一致性已单独修复并复测，详见[修复报告](D:/knowledge-huopao/docs/response_consistency_fix_20261116.md)。本文保留本轮冻结与验收的原始结论。

81条恢复的关系定义已逐项完成三模型独立核验。最终生成包含189个原有类、48条关系的预实验共同本体，33条关系隔离保留。**映射总验收仍未通过：正例、材料不足例和规则类型冲突通过，语义错例的回复一致性仍待修复。**

本轮结果属于工程准备，不是论文准确率评估。本体审核和来源阻断均为机器核验，没有人工金标签；189个类的定义与层级保持原样，尚未整体完成语义复审。原30条样本继续只作为调试集合，不赋予统计意义。

## 冻结结果

审核固定来源、81条定义材料、端点类定义和原文锚点，分9批、每批3家模型独立判断。核查定义忠实性、方向、端点约束和知识范围。任一席缺失、存在实质疑点或来源语义阻断，关系均不能进入冻结版。此处采用保守的本体准备门槛；候选三元组的实验仍使用既有动态权重机制。

- 三模型一致通过50条；31条未获得完整一致通过。隔离不等于全部判错，也包括材料不足、范围问题和分歧。
- 额外记录3条“积分起止点不能充分证明具有部件关系”的来源语义阻断；其中1条已在模型审核中隔离，另外2条虽然三方通过，仍被阻断。
- 最终纳入48条，隔离33条；保留全部原始投票、引用、理由和补充记录。
- 保留关系的ID、方向、domain、range不变；189个类及其层级不变。冻结TTL与JSON已读回核对，所有纳入关系均具有定义。
- 版本只发布到本轮输出目录。正式本体目录的当前版本仍是原始基线，未更改Neo4j或生产配置。

冻结入口：[frozen_release.json](D:/knowledge-huopao/outputs/ontology_freeze_20261116/live/frozen_release.json)。复现实验时读取其中的完整路径和版本绑定，不自行拼接版本号。

所有方法共用的版本绑定：[shared_ontology_binding.json](D:/knowledge-huopao/outputs/ontology_freeze_20261116/live/shared_ontology_binding.json)。固定本体对照组应关闭扩展，固定候选与语义修改实验分开，测试期间不更新历史可靠性。

最终审核：[final_definition_audit.json](D:/knowledge-huopao/outputs/ontology_freeze_20261116/live/final_definition_audit.json)。待处理清单：[final_gaps.json](D:/knowledge-huopao/outputs/ontology_freeze_20261116/live/final_gaps.json)。类的完整语义复审、人工标签及“类似于”的精确关系/尾部指代仍是独立待办。

## 映射验收

固定采用几何概念用例，检查当前真实V8审核链路；没有搜索一批样本后挑选通过者。三个用例分别独立获得新回复，运行时冻结本体、证据和动态权重规则，关闭规划器、自动修订、记忆、扩展和图导入。测试是人为构造的工程用例，不是独立人工标注的自然样本。

| 用例 | 结果 | 观察 |
| --- | --- | --- |
| 法线方向—垂直于—切线方向 | 映射通过 | 来源支持、表达规范、映射有效；知识范围仍不明确，不能据此自动入库 |
| 将谓词故意改成“平行于”，保留“垂直于”的正式ID | 验收未通过 | 未被放行；仅1家有可独立使用的表达/映射冲突判断，未达到预设的两家有效识别标准 |
| 原“涡流阻力—类似于—……”用例 | 通过 | 来源支持、表达规范、映射材料不足；没有强行分配关系或尾部类型 |
| 正例的尾部类型改错（纯规则检查） | 通过 | 端点约束产生硬冲突，无额外模型调用 |

首轮完整导出的总验收未通过，有限补正后的总验收也未通过；两份结论均保留。补正只使用已持久化的晚到原回复和既有定点补正规则，未覆盖原决定或有效意见。

最终验收：[recovered_acceptance_report.json](D:/knowledge-huopao/outputs/ontology_freeze_20261116/mapping_acceptance/recovered_acceptance_report.json)。本轮最需要修复的问题：[mechanism_issue.json](D:/knowledge-huopao/outputs/ontology_freeze_20261116/mechanism_issue.json)。

## 当前首要机制问题

语义错例暴露三类输出问题：一席声明来源矛盾但缺少必需的反证引用；一席把同一证据同时作为实质反证和已排除反例；另一席使用不允许的表达检查枚举。服务器保留原判断并阻止放行，没有将它们改写为有效票。

这三份真实失败回复已加入回归材料：[mapping_semantic_mismatch_20261116.json](D:/knowledge-huopao/tests/fixtures/mapping_semantic_mismatch_20261116.json)。下一步先收紧反证角色与表达检查输出的一致性，保留每轴可用意见及其出处，再重跑同一工程错例；总门槛通过之后，再进行新候选试标和小规模公平对照。不能将本轮未通过改报为通过，也不能用这些机器结果代替人工金标签。

## 请求、成本与验证

本轮共41次新请求：本体准备30次，映射原始审核9次，补正/澄清2次。已知输入输出合计501,269 tokens；没有未结算的新请求，价格未配置，因此不估算金额。旧504仍按此前“无网关日志、执行/计费未知”的历史处置保留，没有重发。

初始已知token停止线为500,000，本轮因已发出的补正超出1,269 tokens。已修复共享余量未传入补正保守预留检查的问题；修复后没有再发模型请求。调用次数在48次上限内。

首批本地契约校验器不支持去重关键字，三份完整原回复在本地重新验证，未增加调用，也未改写原失败请求记录。冻结阶段的数组顺序比较已改为按ID核对实际类内容，后续读回证明类层级没有改变。相关执行代码与指纹分别归档，原始失败记录保留。

主回归101项通过，定点补正专项合并检查31项通过，新增真实错例后的定义/映射检查16项通过（这些集合有重叠）。另已核实正式版本仍为原基线、来源数据库未变、导入队列为空、原始审核文件和有效票未变。

验证记录：[verification.json](D:/knowledge-huopao/outputs/ontology_freeze_20261116/verification.json)。本轮代码入口：[freeze_shared_ontology.py](D:/knowledge-huopao/evaluation/freeze_shared_ontology.py)、[finalize_ontology_freeze.py](D:/knowledge-huopao/evaluation/finalize_ontology_freeze.py)、[mapping_acceptance.py](D:/knowledge-huopao/evaluation/mapping_acceptance.py)、[recover_mapping_acceptance.py](D:/knowledge-huopao/evaluation/recover_mapping_acceptance.py)。已开始过的输出目录禁止自动重跑；需要新真实实验时使用新目录、新冻结版本和明确的有限预算。
