# 供网关维护者核查的材料

仅准备材料，未向任何人发送。诊断对象：43.143.151.41:3000，别名Gemma。公开状态报告One API v0.6.11-preview.7、nginx/1.24.0 (Ubuntu)。

本地4次真实HTTP504，接收耗时62.968、63.289、75.376、62.625秒；客户端HTTP超时170秒、等待180秒、stream=true。Gemma也有一次约116.610秒返回正文。

请结合gateway_unknown_requests.json中的北京时间/epoch核对以下项目：

1. 出504的是入口Nginx、One API中继还是模型服务前的另一层代理；相关upstream地址及读响应头/响应体的错误阶段。
2. 当前proxy_read_timeout、proxy_send_timeout、SSE转发/缓冲、One API RELAY_TIMEOUT及渠道超时设置。
3. Gemma对应实际模型、上下文上限、请求队列、GPU负载/显存及长输入处理耗时；是否仍在执行已向客户端返回504的请求。
4. 这些旧请求的服务商请求ID、执行结果和用量；本地request_key不能当成服务商ID。

没有建议直接改生产配置；应先确认实际故障层和超时阶段。该材料不包含API密钥、完整章节、本体正文或模型思维内容。
