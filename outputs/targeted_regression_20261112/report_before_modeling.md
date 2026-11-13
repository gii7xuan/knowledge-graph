# 定向回归与下一轮 B01 审核

第一步已完成：冻结20条用例，并在批准基线、B01候选、7批合并预览三个本体视图下检查，60/60项预期的命名约束行为均匹配。这个结果包括应拒绝和应暂缓的用例，不表示20条全部通过，更不表示已获得模型语义批准。

## 本轮回归集

|组成|数量|用途|
|---|---:|---|
|新增结构用例|8|消耗对象、部件方向、形状属性、条件例子与父类推广；每处一正一反|
|原样保留的受影响样本|9|DEV-002、004、007、013、014、015、024、025、028|
|未改动对照|3|DEV-001、019、022|

新增用例由AI根据冻结章节及本体编写，全部带来源锚点，未计入人工金标准，也没有写入业务事实库。原样本的文本、类型映射和标签均未改写。

三处关系修复让对应正例从旧约束拒绝变为接受，让原错误对象/方向负例从旧约束接受变为拒绝。父类修复阻止仅凭“惯性参考系”类型自动继承“动参考系”；它不意味着二者互斥。来源中明确选取的恒定风动参考系正例仍被接受。

DEV-028继续因关系range映射不匹配而失败，没有为让样本通过而放宽本体。DEV-001继续保持未分配/unknown。DEV-019、022虽能通过现有命名约束检查，其计算参数和投影角色问题仍未解决，不能据此准入。

详见 [冻结20条用例](D:/knowledge-huopao/outputs/targeted_regression_20261112/frozen_regression_cases.json)、[60项离线结果](D:/knowledge-huopao/outputs/targeted_regression_20261112/offline_results.json)。定义可用性另用严格guard记录，未混入结构行为断言。

## 第二步：先审 B01

已在全新隔离库登记B01第3轮：10个操作、14段逐字匹配原文证据、完整本体和无损RDF差异，另有8个结构用例的工程检查记录。第2轮未发送的原始上下文保留于`snapshot_round2`；第3轮仅移除重复引文及guard元数据，保留所有用例、三元组、证据ID和预期/实际约束结果。工程上下文由约48KB减至约8KB。

本次拟申请最多3次真实调用：Qwen、DeepSeek、Gemma各一次，仅独立建模审核，不自动重试。发生执行结果未知或账户暂停即停止，旧票和旧未知记录保留。本批审核即使收齐3张建模approve，也仍需要整章覆盖门禁，不会发布。

[冻结提案](D:/knowledge-huopao/outputs/targeted_regression_20261112/prepared_b01_proposal.json) · [实际建模材料](D:/knowledge-huopao/outputs/targeted_regression_20261112/prepared_b01_modeling_payload.json) · [调用前核验](D:/knowledge-huopao/outputs/targeted_regression_20261112/review_preflight.json)

## 第三步的范围边界

9条受影响原样本中，8条对应B02定义，DEV-028对应B05定义；B01单独通过不能代表这些批次或93个操作的合并预览已获批准。后续先取得待测本体所含修订的对应审核与整章覆盖回执，再对冻结20条用例进行新的模型定向回归，稳定后检查完整30条。

因此本轮不提前发送样本模型请求。当前新增真实调用为0、累计仍为13次，追加调用尚未授权。[执行顺序与样本对应批次](D:/knowledge-huopao/outputs/targeted_regression_20261112/sequence_plan.json)列出了各阶段状态。

[交付校验](D:/knowledge-huopao/outputs/targeted_regression_20261112/delivery_verification.json)确认来源、旧样本及原库不变，未授权执行入口会在发送之前停止。当前暂停在追加3次调用的明确授权处，未产生新请求。
