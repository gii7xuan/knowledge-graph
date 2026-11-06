# 审核输出结构约束探测与接入

验证时间：2026-11-04T10:24:08.310437+00:00

|当前入口/模型|实测结果|新审核模式|
|---|---|---|
|Qwen（43.143.151.41）|两次唯一值约束测试及完整九维度Schema通过|严格JSON Schema|
|Gemma（同入口）|接受参数但响应未遵守Schema；固定模板通过本地审核契约|固定模板＋JSON模式＋本地校验|
|deepseek-flash（DeepSeek官方Chat入口）|json_schema返回HTTP 400；固定模板通过本地审核契约|固定模板＋JSON模式＋本地校验|

新契约：validator_response_v6_1。相关离线回归 160 项全部通过。

共7次外部能力探测，仅发送合成任务和输出结构，没有发送教材、候选或本体内容。没有重发未知请求。
Qwen能力结论绑定本次非流式配置，Gemma模板探测使用流式，DeepSeek使用当前thinking=disabled配置。
模式选择按入口、模型、流式配置、thinking配置与Schema哈希匹配，未知能力使用模板。
请求冻结后不再读取能力表重选模式；HTTP 400、504或格式错误不会静默切换模式另发请求。
所有模式均检查结构、未替换的模板占位符、证据引用和审核语义。Schema合规不代表事实审核正确。
未对30条样本发起新版重跑，未改写旧票、生产候选或人工工作簿。旧V5.1实验保留原配置与结果。

当前Chat入口的结论不代表供应商其他API协议。参考：[DeepSeek Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)；[vLLM结构化输出说明](https://docs.vllm.ai/en/latest/features/structured_outputs/)。
完整探测响应见本目录各probe JSON文件；冻结模式和测试摘要见verification_summary.json。
