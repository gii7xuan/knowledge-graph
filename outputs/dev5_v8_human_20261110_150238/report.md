# 首批5条人工标注及人机对比

录入时间：2026-11-10T15:02:38.905448+08:00；标注者：本对话用户。
用户确认完整阅读每条材料、未看过模型结论；映射依据为头尾实体定义可找到，关系经本人核对符合原文。
5条均标为支持、规范、有效、纳入，已校验完整版本/证据/本体/规则绑定。四维标签已resolved，人工标签覆盖5/30。

|样本 / 版本|人工四维|系统原文 / 表达 / 映射 / 范围|系统建议|
|---|---|---|---|
|DEV-004 / v2|支持 / 规范 / 有效 / 纳入|supported / standard / context_incomplete / include|wait_mapping|
|DEV-007 / v3|支持 / 规范 / 有效 / 纳入|supported / standard / context_incomplete / include|wait_mapping|
|DEV-008 / v2|支持 / 规范 / 有效 / 纳入|supported / standard / context_incomplete / include|wait_mapping|
|DEV-014 / v1|支持 / 规范 / 有效 / 纳入|supported / standard / context_incomplete / unclear|wait_mapping|
|DEV-015 / v2|支持 / 规范 / 有效 / 纳入|unresolved / standard / context_incomplete / include|manual_review|

逐维结果：原文4条一致、1条系统unresolved（DEV-015）；表达5条一致；范围4条一致、1条系统unclear（DEV-014）；映射5条人工认为有效、系统context_incomplete。
系统尚未确定的状态不当作明确的反向事实判断，也不直接统计成模型事实错误。

## 第一版错误分析

- DEV-015：人工认为原文支持，系统原文维度仅2/3可用。优先检查主证据、标题语境及证据角色，不先修改候选事实。
- DEV-014：人工判断纳入，系统范围未确定。优先核对可复用的几何关系与局部符号约定的分类边界；保留此次人工纳入结论。
- 5条映射：人工确认关系与原文相符，作为当前包的有效映射标签保留；绑定发布版缺正式关系定义的工程事实仍保留。先处理现有本体定义批准/发布及重绑定流程，不能把人工标签直接写成系统映射完成。

## 评价口径

已标注子集的可用候选建议通过为0/5：系统仍4条等待映射、1条人工。这描述当前准入建议覆盖，不能解释为模型准确率为0%。自动准入策略关闭，也不能用实际入库数评估模型能力。
自动准入精确率无实际准入分母；修订成功率没有实际修订；正式映射正确率没有已完成正式映射分母，均暂不可评估。已标注子集没有不必要修订建议（0/5），仅适用于首批材料完整的开发样本。
25条尚未标注，不填成失败或成功。本批是开发集按材料完整度选择的5条，不能外推30条或独立文档准确率。

文件：[人工标签](annotations.json)、[逐条对比](comparison.json)、[首批质量报告](quality_first5.json)、[30条部分标签覆盖](quality_dev30_partial_labels.json)、[人工原回复](human_submission.json)。
本次未增加模型调用，未修改原票、原决定、本体发布版或生产数据库。
