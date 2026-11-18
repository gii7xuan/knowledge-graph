# 七项争议处理与原页核证（2026-11-18）

七项均已形成有来源绑定的处理意见。PILOT-011 的圆下标疑问已通过原页解除，PILOT-036 的双划记号确认应保留；其余问题落实为施事、篇章范围、公式身份或推导条件的待审修订。这里的来源核证和工程判断不等于人工裁决或修订获准。

当前记录以 [七项处理结论](D:/knowledge-huopao/outputs/priority_disputes_20261118/seven_dispute_resolutions.json) 为准。初始 `dispositions.json` 中“011 尚缺原页”是获取 PDF 前的历史状态，原文件及当时执行报告保留。

## 七项处理意见

| 样本 | 核查结论与后续表示 | 当前复核状态 |
|---|---|---|
| PILOT-001 | `it` 指 covolume。前句 `has been described as` 是被引述的解释，后句 `Think of it as` 是作者解释口吻，不能仅据表面差异判为直接事实矛盾。保留原主谓宾与否定，仅补作者口吻和局部范围。 | 首版改为 `this passage` 的方案不继续采用；反对理由完整保留。新的最小改动是离线待审方案。 |
| PILOT-002 | thermodynamics 不是 introduced 的施事。草案以 work and energy 为主体，表示它们在本书前文作为主要热力学概念被介绍；earlier sections 记录为篇章位置，移出 time_scope。 | 已建隔离草案，尚未发送独立复核。 |
| PILOT-011 | 原页 k 后是中文句号 `。`，Markdown 将其误接成 `k_{circ}`。原候选普通 k 的转写不因该句号而需要添加圆下标。另准备 `value=原尾项`、`datatype=xsd:string`，补齐 literal 的技术表示。 | 符号疑问已解除；新增表示方案尚待独立复核及 PDF 证据注册。字符串类型不宣称已完整表达数学结构、向量粗体或全部排版语义。 |
| PILOT-017 | 原文 `we chose a CV` 的施事是分析者，Eulerian frame 是分析场景。草案改为文中的欧拉参考系分析使用选定的真实或假想控制体。 | 已建隔离草案，尚未发送独立复核。 |
| PILOT-020 | 原书连续位置明确：式 2.129 后紧接 `This equation…` 和右端第二项解释。草案将第二项绑定至该式，保留原文 material 的局部解释。 | 公式身份已补明；草案尚未独立复核。未增加累计质量或其他超出原文的物理解释。 |
| PILOT-027 | 有势质量力是本段理想流体欧拉方程沿流线积分推导的前提之一，不能压成所有质量力的普遍性质。其余条件补为定常且（正压或不可压缩）。 | 已建隔离草案，尚未发送独立复核；AND/OR 的作用域保留。 |
| PILOT-036 | 英文原页 δ 后确有双划记号，保留 `δ''`。`would require` 表达规范性要求，作者随后明确为本文目的忽略该惯例。 | 准备 modality 值改为 `unknown` 的离线表示方案，关系和附着仍保留 would；未知状态继续阻止完整表达准入。 |

## 原页核证

用户提供的《火炮弹道学》（钱林方）PDF 第 75 页确有 3.2.2，但小节标题是“弹丸绕质心运动方程组”，与 PILOT-011 的冻结出处不同。本次未使用该页的专门运动方程解释候选。

在同一目录找到并核对对应原书：

- [《火炮设计理论》原 PDF](D:/长理研究生/火炮方面/火炮设计理论%20(张相炎、郑建国、袁人枢)%20(Z-Library).pdf)：PDF **108**，书内 **96**，3.2.2“连续方程”。[局部图](D:/knowledge-huopao/outputs/priority_disputes_20261118/operator_original_detail.png) 清楚显示 k 后的中文句号。
- [Ballistics 第三版原 PDF](D:/长理研究生/火炮方面/Ballistics%20%20Theory%20and%20Design%20of%20Guns%20and%20Ammunition,%20Third%20Edition.pdf)：PDF **41**，书内 **22**，匹配 PILOT-036 的热力学记号段。[页面图](D:/knowledge-huopao/outputs/priority_disputes_20261118/third_edition_page41.png) 显示 δ 后两个上置划痕。仅据该图不额外断言其精确 Unicode 字符或专门数学含义。

先前还检查过[出版社第四版预览](https://api.pageplace.de/preview/DT0400.9781040333310_A50181192/preview-9781040333310_A50181192.pdf)的同一段落，亦见相同双划形态；该检查仅作跨版本旁证。本次局部结论以已找到的第三版原页为直接材料，没有声称第四版就是本地 Markdown 的已验证版本。

[视觉核证附录](D:/knowledge-huopao/outputs/priority_disputes_20261118/source_visual_addendum.json) 保存 PDF、渲染图的 SHA-256、从 1 开始的 PDF 页码、书内页码及原候选/Markdown 定位。视觉检查是编码 agent 的核证，未冒充人工盲标或已注册到文本证据包的审核证据。原 Markdown 和冻结候选均未覆盖。

## 独立模型复核与未知请求

预先准备五个新上下文输入及隔离修订草案：001、002、017、020、027。新输入补入同一原书的精确定位段落，并保留原候选引用和完整当前片段；没有把旧标签或旧票绑定到新输入。

冻结的本轮上限为 15 请求、每草案最多 3 个不同模型、串行发送、零重试；出现未知结果即停止后续发送。实际只发出 **2 个请求**，都属于 PILOT-001：

1. Qwen 返回完整回复，原始判断为 **reject**，但漏审两项变更状态字段，属于 `invalid_contract`，不能算有效票。其“原主语是 covolume”“首版改写改变表达焦点”等实质反对意见完整保存，也用于制定新的最小方案；没有因格式无效而忽略反对理由。
2. Gemma 流读取超时，最终回执仍为 `unknown`。网关请求 ID 为 `2026111810593843420934404042437`，客户端请求 ID 为 `attempt_a1fb18ef6c93469db606951a68e3b5ca`。收到 HTTP 200/流片段不证明已获得完整结果或已知计费；没有重发。

DeepSeek 座位未发送，不计为实际请求；其余四项未启动复核。001 状态为 `review_incomplete`，三个座位有效权重均为零，不能据无有效票得出通过或否决。已知用量为输入 **12,277**、输出 **1,321** token；Gemma 用量及总费用未知，没有记为零。

运行时修复了修订服务对 `max_parallel_validators` 的遵守：串行上限有效，遇未知结果阻止队列中未发送座位。13 项相关回归通过，覆盖串行调度、未知请求阻断、原文义务、修订绑定及补证交接。[回归日志](D:/knowledge-huopao/outputs/priority_disputes_20261118/revision_preflight.log) 已保存。

## 当前产物与下一步

- [初始五份隔离草案](D:/knowledge-huopao/outputs/priority_disputes_20261118/prepared_cases.json)：四份 proposed、一份 review_incomplete；首版 001 的历史记录不覆盖。
- [三份新增离线方案](D:/knowledge-huopao/outputs/priority_disputes_20261118/offline_revision_plans.json)：001 最小范围修订、011 字符串 literal 表示、036 保留词汇语气的 unknown 编码。三份均通过严格补丁结构检查，未注册为可自动执行的获准草案。
- [完成核验](D:/knowledge-huopao/outputs/priority_disputes_20261118/completion_verification.json)：原表、160 条冻结候选、来源库、原草案库、生产本体目录及本次初始执行材料哈希未变；应用数、人工金标准数、jobs/outbox 均为零。未赋正式映射、更新可靠性或计算论文准确率。

下一步将这份核证附录用于人工辅助复核，先评估 001 的最小方案及其反对意见，确认 011 的字符串表示与 036 的模态表示，再逐项复核其余草案和映射。由于材料已显示 AI 预标和模型意见，这一轮应记为辅助复核，不应记成盲标。Gemma 未知请求保持挂账，本轮不自动续跑或扩大实验。
