# Gemma定向复跑结果

已执行本次授权范围内的2次调用，累计11/11次。新隔离审核库：review.sqlite3；原9次审核库及全部历史记录保持不变。

|项目|回执状态|决定|
|---|---|---|
|Gemma建模重评|api_error|GATEWAY_REQUEST_OUTCOME_UNKNOWN|
|Gemma整章引用修复|api_error|GATEWAY_REQUEST_OUTCOME_UNKNOWN|

## 三席整章有效结果

|席位|回执状态|覆盖|能力问题|
|---|---|---|---|
|Qwen|ok|complete|pass|
|Gemma|api_error|未知|无有效决定|
|deepseek-flash|ok|partial|fail|

整章汇总：unknown；有效建模批准2/3；最终共识：unresolved。

Qwen与DeepSeek已有的有效建模票、整章票完整保留；DeepSeek的有效fail没有复投或覆盖。两条旧Gemma未知请求仍为unknown，没有改成未执行或批准。

引用修复只把Gemma本人的旧响应、错误位置及已冻结章节内唯一匹配的原文候选发送给同一模型；完整章节、完整本体和原能力问题不变，不向模型发送其他席位意见。原非法引用未由程序改成有效票。

本次没有发布新版本，也未切换生产。已实际检查发布入口，因独立审核门槛未满足而拒绝。

本次已报告输入0、输出0 token；2次请求用量缺失，不能按零计费。

证据文件：authorization.json、baseline_state.json、modeling_rerun_result.json、competency_repair_result.json、new_request_receipts.json、effective_competency_result.json、final_summary.json、delivery_verification.json。

本副本当前保留4条未知尝试：原Gemma建模2条、本次建模1条、本次整章引用修复1条。详见gateway_unknown_requests.json；这些记录均不允许自动重发。
