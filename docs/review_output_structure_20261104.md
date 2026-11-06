# 按入口能力选择审核输出模式

本次实现“加强输出结构约束”，范围为三元组审核的三模型席位。规划器、候选修订审核和本体审核继续沿用其冻结协议，没有自动改动其他Agent的输出契约。

2026-11-04用7个合成小请求探测当前配置的Chat Completions入口：

|模型|观测|采用方式|
|---|---|---|
|Qwen|两次Schema唯一值测试均遵守约束，实际九维度Schema也通过结构及审核契约校验|`response_format.type=json_schema`、`strict=true`，发送实际审核Schema|
|Gemma|HTTP正常返回，但忽略Schema所要求的唯一值；完整固定模板测试通过|JSON模式＋固定完整模板＋本地校验|
|deepseek-flash|当前官方Chat入口拒绝json_schema，返回HTTP 400；完整固定模板测试通过|JSON模式＋固定完整模板＋本地校验|

以上能力结论绑定协议、URL、模型、流式开关、thinking配置和Schema哈希。不同入口或配置不复用未验证能力；Qwen当前验证的是非流式配置。仅观察到HTTP 200不足以确认支持Schema。探测没有发送教材原文、候选或本体内容，也没有因未知结果而重试。

新增`validator_response_v6_1`，保留V6的九维度意见和程序汇总规则，仅增加输出交付契约。支持的入口发送实际严格Schema；不支持或未验证的入口收到与Schema同源的固定模板。模板使用无效枚举占位符，没有默认“通过”判断；未替换的占位符会被拒绝。

无论使用哪种方式，模型响应都先通过本地Schema子集检查，再执行完整审核契约、证据引用和程序准入校验。供应商结构约束只解决响应形状，不证明事实正确。没有增加外部库依赖；本地Schema校验只支持本契约所用关键词，遇到未知关键词明确失败。

输出模式在proposal的`validator_generation[].output_transport`冻结，并进入proposal摘要、请求缓存身份和持久化请求元数据。请求子进程及恢复流程使用原模式；更新能力表不会改变已有票或已冻结请求。运行时HTTP错误不会触发隐式降级再调用；改变输出模式也不能绕过同一语义请求的未知结果屏障。输出模式或Schema不一致的请求在发送前拒绝。

默认配置和示例配置对新审核采用V6.1，能力记录在`config/review_output_capabilities.json`。历史V6及更早契约、提示和请求保持原规则。旧30条V5.1实验没有被转换为新版，也没有把探测响应计作审核票。

代码落点：`review_agent/output_structure.py`、`schema_validation.py`、`response_contract_v6_1.py`、`llm_clients.py`及请求冻结/恢复链路。探测原始结果和验证报告在`outputs/review_output_structure_20261104`；回归日志为`runs/review_output_structure_full_20261104.txt`。

当前结果证明这些合成请求的结构交付方式可用，仍需用真实候选验证新版审核质量和长请求稳定性。当前Chat入口的能力结论不推广为供应商全部协议能力，未切换到其他API地址。
