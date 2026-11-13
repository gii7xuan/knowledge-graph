# 无后台权限的Gemma超时排查

第一阶段完成历史回执分析和2次只读GET检查，新增推理0次；第二阶段经用户授权完成2次固定JSON测试。原11次审核记录、有效票与未知状态未改。

## 已确认的事实

|项目|观测|
|---|---|
|历史Gemma尝试|5次：4次HTTP504，1次返回模型正文但引用契约不合格|
|4次504的本地耗时|62.968秒, 63.289秒, 75.376秒, 62.625秒|
|Gemma客户端设置|HTTP超时170秒，调用等待180秒，stream=true，SDK自动重试0次|
|504错误正文|均含nginx/1.24.0 (Ubuntu)|
|Gemma曾返回正文的SDK耗时|116.610秒|
|当前状态接口|GET /api/status：HTTP200，375毫秒|
|当前模型列表|GET /v1/models：HTTP200，219毫秒，包含Gemma|
|当前TCP/TLS|状态接口TCP约78毫秒、TLS约47毫秒；模型列表TCP约47毫秒、TLS约31毫秒|

以上GET没有执行推理。模型列表能读取不等于Gemma推理健康；当前网络检查也不代表历史每次网络状态。

## 当前结论及边界

高置信度：本轮失败由HTTP服务器返回504，发生在本地170秒HTTP超时及180秒等待期限之前。因此，客户端超时设置过短不能解释这4次失败；单纯把本地等待继续调大不能修复服务器已返回的504。

可优先验证的假设：Gemma路径上的上游排队、长输入处理/首输出慢、流式传输间隔或代理缓冲及读空闲超时。无法仅由HTML页确定是哪一层Nginx产生504，也不能确认它实际配置了60秒。

NGINX官方文档说明proxy_read_timeout默认60秒，控制相邻读取之间的等待，而不是整条响应的总时长；proxy_buffering影响数据向客户端转发的时机。这里仅用作解释候选机制，不把默认值当作该部署的实测配置。[读取超时与缓冲文档](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_read_timeout)

Gemma有一次116.610秒成功返回正文、同网关Qwen也有超过60秒的返回，因此不能声称该地址一律在请求总时长60秒截断。

历史建模输入user JSON约176,701字符/200,158字节；成功返回的Gemma整章输入由服务报告为74,307 token。输入较长是候选影响因素，尚不能据此确定上下文超限、GPU不足或推理队列拥堵。

部署所报One API版本为v0.6.11-preview.7。该版本官方文档列有RELAY_TIMEOUT，但未读取到服务器环境变量，不能假定其实际值。[对应版本文档](https://github.com/songquanpeng/one-api/blob/v0.6.11-preview.7/README.md)

## 客户端可观测性缺口

- OpenAICompatibleClient已采集流式首chunk、首正文和分块间隔；ontology_review.validators.judge_once与semantic_competency.judge_once没有把transport_metrics保存到审核票。历史回执因此无法重建首输出和流中断时刻。
- 历史HTTP错误没有保存响应头。新只读GET能看到X-Oneapi-Request-Id，说明后续可采集该字段；不能保证Nginx产生的504一定带该字段，也不能用GET的ID替代旧审核请求ID。
- SDK配置max_retries=0、trust_env=false。本次未发现SDK自动重试或本地环境代理造成重复调用的证据。没有改动审核代码和冻结指纹。

## 无后台条件下可继续的测试

prepared_synthetic_probe_plan.json准备了最多2次极小合成请求：先流式、再非流式；每次max_tokens=32，只要求返回固定JSON，不发送项目章节或本体。两次均独立记录TCP/TLS、上传、响应头、首字节/SSE和结束事件。第一条若结果未知则停止，不自动切换模式重发。该方案现已获用户明确授权并执行，2次均HTTP200且传输结束；均在32-token上限终止，标准content字段未取得目标JSON。详见[小请求测试报告](client_probes_20261112/report.md)。

- 极小请求也504：优先排查Gemma路由、上游可用性或排队，不能把错误归因于项目文档。
- 极小请求正常：再按明确预算对比长输入和首输出时间，验证输入处理压力；保持原审核标准及完整证据。
- 流式有及时事件而非流式超时，或事件聚集到最后才到达：结合HTTP阶段与流时间序列，进一步定位流式转发、缓冲或响应等待的差异。
- 精确的Nginx/One API运行配置、模型服务器队列/GPU状态，以及旧请求是否真正执行，仍需服务端日志或管理员协助。客户端诊断不能代替这些记录。

## 交付文件

- historical_request_timing.json：全部11次历史请求的耗时、输入大小、配置和结果。
- offline_diagnosis.json：事实与推断分开保存的摘要。
- read_only_connectivity.json：两次GET的安全响应头和TCP/TLS/HTTP事件时间。
- prepared_synthetic_probe_plan.json：执行前准备快照；实际授权及结果保存在client_probes_20261112。

## 最新小请求结果

流式1.172秒、非流式1.281秒，均HTTP200；首SSE事件0.375秒，流式最大分块间隔0.078秒。正式审核11次加诊断2次，累计13/13次；没有新增未知诊断，也未改变正式审核状态。详细结果见[客户端测试报告](client_probes_20261112/report.md)。
