# 模型客户端 HTTP/SSE 诊断记录

实际同步 `OpenAICompatibleClient.complete_json` 现在记录 HTTP 响应头、首个原始响应体块、SDK 首个 SSE chunk、首段可见内容和流间隔。记录会随建模票、整章覆盖票及请求台账保存，HTTP 错误、流中断和无效 JSON 也尽量保留已取得的观测值。

## 字段含义

|字段|含义|
|---|---|
|`transport_metrics.trace`|httpcore 连接、TLS、发送、接收阶段的事件名及相对毫秒数；最多256条，不保存事件参数。|
|`response_headers_ms`|HTTP 客户端取得响应头的时间。|
|`first_body_byte_ms`|HTTP 客户端迭代到第一个非空原始响应体块的时间；是客户端可观测的响应体到达时间，不是 TCP 抓包时间。|
|`body_chunk_count` / `body_bytes_received`|HTTP 层非空响应体块数量及字节数，和 SSE 事件数不同。|
|`max_inter_body_chunk_gap_ms`|两个 HTTP 响应体块之间的最大观测间隔；少于两个块时不填。|
|`first_chunk_ms`|SDK 解析出的第一个 SSE chunk，含仅推理或元数据的 chunk。|
|`first_content_ms`|第一段非空可见 `content`，不能用推理 chunk 冒充。|
|`max_inter_chunk_gap_ms`|SDK chunk 之间的最大间隔，少于两个 chunk 时为 null。|
|`terminal_ms`|流正常结束或发生错误时的时间；结束是否成功仍由原有响应状态判断。|
|`transport_elapsed_ms`|HTTP 观测上下文到生成该快照的耗时。|
|`response_headers`|白名单响应头，包含网关 ID、server、日期、内容类型等。|
|`gateway_request_id`|优先 `x-oneapi-request-id`，其次 `x-request-id`；与模型响应体 `response_id` 分开保存。|

HTTP 字段使用 `perf_counter`，SDK 字段沿用原有 `monotonic`，均相对于本次调用入口附近的时点。它们用于阶段定位，跨字段的亚毫秒差不应解释为真实网络顺序。没有经过实际 HTTP transport 的 Mock/自定义客户端可不具备底层字段。

## 落库和失败处理

- `LLMResponse` 及 `response_metadata` 带上网关 ID 和诊断信息。
- 建模与整章覆盖包装器在解析 JSON 前先保存元数据，解析失败仍能查询耗时、用量及 ID。
- HTTP 错误或流中断把已有诊断附到异常上，`api_error_fields` 只复制时序、ID、用量及计数。
- 新请求台账的 `provider_request_id` 优先保存网关 ID；完整结果仍分别保留响应体 ID。历史行没有回填或改写。
- 原有 504/408、连接中断和超时的“执行结果未知”及禁止自动重试规则保持；有诊断信息不代表已经执行或尚未执行。

日志不保存 Authorization、Cookie、请求体、推理原文或半截响应文本；推理只记录字符数。响应头使用白名单、长度限制及本次 API key 脱敏。原本票据为业务审核所保存的有效回复或无效回复记录不属于新增传输日志。

每次请求用 `ContextVar` 隔离观测上下文；HTTP 响应流只包裹迭代和关闭，字节原样交给 SDK。未修改提示词、输入材料、输出上限、超时时间、TLS 设置或重试策略，也没有发起诊断模型调用。

## 离线验证与使用边界

专项测试位于 `tests/test_transport_diagnostics.py`，使用真实 OpenAI SDK、httpx MockTransport 和临时 SQLite，覆盖流式、非流式、504、中断、并发隔离、无效 JSON、有效 fail 及双 ID 落库。相关回归与最终结果保存在 `outputs/offline_objection_review_20261112/regression_test_result.json`，详细输出在同目录的 `regression_tests.log`。

本次只增强后续调用的客户端观测，不能补出旧 Gemma 请求缺失的响应头或执行结果，也无法仅据客户端事件确认后端排队、GPU 状态或代理配置。已有两次极小合成探针只证明小请求传输可用；正式长材料请求仍未被重新验证。

本次代码指纹发生变化。旧冻结提案、旧票及其代码哈希留作历史；后续真实审核必须重新准备并冻结材料、代码和请求绑定。当前批次没有剩余真实模型调用额度，累计13次不变。
