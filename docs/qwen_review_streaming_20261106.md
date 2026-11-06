# Qwen 审核流式配置

V6.3 缺席诊断记录了 Nginx HTTP 504，失败集中在约 64 秒，早于 Qwen 客户端的 90 秒上限。可以确认网关返回超时；具体哪一层网关、排队或生成触发限制，仍需服务端日志定位，不能仅凭时长确定线上 `proxy_read_timeout` 的实际值。

本次将 `config/review_agent.json` 中 `validators.judge_a.stream_responses` 设为 `true`，并同步示例配置。请求总等待和 HTTP 超时配置仍为 90 秒。现有客户端发送 `stream=true` 和 `stream_options.include_usage=true`，拼接正文、收集 usage，收到终止标记后才返回完整响应；随后继续执行 JSON、Schema 和业务契约校验。客户端不需要重写，前端也不需要改为逐 token 展示。

## Schema 能力依据

能力表按入口、模型、流式开关、thinking 设置和 Schema 哈希精确匹配。因此同时新增 Qwen 流式的能力记录，保留非流式记录供历史配置使用，避免仅改开关后新任务转为未验证的模板模式。

依据是已有的纯合成探测 `outputs/qwen_gateway_probe_20261105/large_review_stream.json` 和 `long_review_stream.json`，没有新增线上请求。两份响应均 HTTP 200、完整结束并通过结构校验；较长响应还通过业务响应契约校验。基础响应缺少反证证据引用，业务校验未通过，该响应不作为有效审核票。

V6.1、V6.2、V6.3 的响应 Schema 哈希一致：`5a6fd271c03a2abf3a19904854076affa52e06380f5c6abad3c1349f9f36ec62`。较长流式探测约 26.8 秒收到首个正文、61.4 秒完成；这些合成测试证明传输兼容，不代表真实审核的 504 已经消除。

## 服务端配合

Nginx 官方说明 `proxy_read_timeout` 默认 60 秒，限制的是相邻两次读取上游数据的间隔；`proxy_buffering off` 可让收到的数据及时传给下游。流式需要模型服务和中间转发层实际持续发送数据。若排队或输入处理使首个数据块仍超过网关等待限制，流式也会失败。

服务端可在实际代理模型请求的现有 `location` 中核对并酌情配置以下指令；这是建议，未在本地修改或部署远程网关：

```nginx
proxy_read_timeout 180s;
proxy_buffering off;
proxy_cache off;
```

客户端现有总等待仍为 90 秒；网关放宽后，超过 90 秒的调用仍会触发本地等待上限。若真实流式审核需要更长总时间，再结合耗时分布调整 `request_timeout_seconds`、`http_timeout_seconds` 与候选总预算，单独增大某一层不能保证请求完成。

文档：[读取超时](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_read_timeout)、[响应缓冲](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering)。

## 生效范围

配置在创建客户端时加载，已运行的进程需要重新加载配置后才使用新开关。已冻结审核的传输模式继续按原记录恢复，不能在原冻结任务中直接替换。已有 504 请求的执行结果仍未知，不因改成流式自动重发；应先对账，再决定恢复或新建审核。后续真实审核需要观察首个事件耗时、完整完成耗时和 504 比例。

## 本地验证

`ProviderWireTests`、`test_review_output_structure`、`test_dynamic_ontology_context` 共 36 项回归通过。另用本地 HTTP SSE 服务经过真实请求子进程验证：完整分块 JSON 正常校验并记录 usage；正文虽已组成合法 JSON，但没有终止响应就断流时，仍归为结果未知、禁止自动重试，两种场景各只发送一次请求。还核对了新配置命中严格 Schema 模式及能力记录中的已有探测文件。本次没有向外部模型发起新请求，也未重新运行真实审核批次。
