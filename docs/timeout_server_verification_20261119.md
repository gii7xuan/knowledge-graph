# 超时抽取请求：服务端结果核实进展

已确认服务端返回HTTP 200响应头；尚未确认生成是否完成、最终内容和计费用量。原始请求继续保持unknown，未重发，也没有恢复剩余12包。本轮新增模型调用为0。

## 核对的原始证据

请求开始时间为北京时间2026-11-19 09:43:51.614，目的地`https://api.deepseek.com`，模型`deepseek-flash`。

| 事件 | 原请求内相对时间 | 证据含义 |
|---|---:|---|
| HTTP请求正文发送完成 | 5.264毫秒 | 客户端发送事件完成；请求体26581字节 |
| 收到HTTP响应头 | 约216.9毫秒 | 服务端响应为200，类型application/json |
| 开始读取响应正文 | 约217.1毫秒 | 进入正文读取阶段 |
| 正文读取失败 | 约180234.9毫秒 | 客户端超时，没有取得完整结果JSON |
| 关闭响应连接 | 约180251毫秒 | 该客户端连接结束，不能继续等待同一连接的内容 |

本地attempt为`attempt_8373599cfa9a43288473df4a3371a6ce`。没有取得服务端response ID、网关request ID或客户端跟踪ID，token字段为空。

原数据库还记录了一次late_result，但其内容是迟到约0.34秒的`api_error / APITimeoutError`，不是迟到的模型答案。没有找到可用于把unknown改成completed或failed的完整生成回执。

## 为什么HTTP 200还不能完成对账

[DeepSeek官方保活说明](https://api-docs.deepseek.com/quick_start/rate_limit/)解释，非流式请求在等待最终结果时可以保持连接并发送空行。它说明响应已经开始与模型生成结束是不同状态；不能只凭200响应头判定推理完成或计费结束。本次只能确认收到响应头，不能推断具体是排队、推理、正文传输还是其他服务端环节造成等待。

已检查[聊天生成API文档](https://api-docs.deepseek.com/api/create-chat-completion/)，在所查公开参考中没有找到可以凭这个本地attempt ID或输入哈希查询该笔历史请求的接口。没有猜测接口或再次提交生成请求。[余额接口](https://api-docs.deepseek.com/api/get-user-balance/)只提供账户余额，单独读取当前余额不能定位这笔请求，因此没有用余额变化推算其成功与否。

## 尚缺的服务端证据

尝试查看浏览器账号用量页面时，原生Chrome访问审批等待超时，页面未被访问。没有自动登录、读取浏览器凭据、修改账号或联系服务商。

下一步需要在[DeepSeek用量页面](https://platform.deepseek.com/usage)中核对对应API Key、北京时间2026-11-19 09:43:51附近的deepseek-flash记录，取得足以对应这一笔请求的状态、用量或服务端请求ID。若页面只提供汇总，仍不能把汇总直接当成这一笔的完整结果；需要相应的服务端日志或请求明细。

现有对账材料已经具备时间、模型、原始输入绑定、请求体字节数和本地事件链。不能把本地request_key或proposal_digest冒充为服务端颁发的请求ID。

## 文件

- [本轮核实结果与HTTP事件链](../outputs/formal_experiment_20261119_launch_v2/server_verification_20261119.json)
- [原始请求对账材料](../outputs/formal_experiment_20261119_launch_v2/request_reconciliation.json)
- [原始物理请求日志](../outputs/formal_experiment_20261119_launch_v2/extraction_request_journal.jsonl)
- [此前抽取结果与27条恢复候选](formal_extraction_20261119.md)

本轮以只读方式核对数据库，数据库文件哈希在核对前后相同；原请求状态、回执和27条派生候选均未改写。
