# 三模型回复一致性修复（2026-11-17）

本次处理上一轮工程验收中的三类失败：映射 ID 字段错层、限定信息“通过”与 unresolved 发现并存、实际反证角色与 counterevidence=pass 并存。原始失败回复、历史验收结果和未知请求记录均保留。

## 代码行为

- 新提示词 `triple_validator_v8_consistency_v6` 与一致性策略 V2 要求模型在首次回复中共同构造检查状态、发现和证据角色；真实疑点保留为 unknown，实质反对意见保留。
- `mapping_field_shape_projection_v1` 只允许将 `dimensions.relation_id`、`dimensions.competing_relation_ids` 原值移动到 `dimensions.mapping`。目标字段必须缺失，不能有其他未知同层字段，类型必须合法；冲突不合并。该操作不调用模型，不改判断、角色、发现或候选。
- 原始回复独立保存。轨迹绑定原始/投影视图哈希、移动路径及字段值哈希、提案、证据与冻结策略；计票前重新计算。篡改视图、存储判断或轨迹，或者删除必需轨迹，均不能获得有效票。
- 语义矛盾继续作为无效票拦截。启用澄清且预算允许时，可请求一次非计票说明；说明不能替换原票。新诊断将 unresolved 发现与 pass 状态的冲突纳入该流程。
- V5 及更早提示词仍按原文件、末尾指令和策略分派，不追溯启用结构纠正。新策略进入提案摘要和请求缓存身份。

V8 回复 schema、动态权重规则、0.70 判定门槛及证据要求未放宽。

## 验证设计

离线回归覆盖真实三份失败回复、无损移动、冲突拒绝、轨迹篡改、缓存来源绑定、历史 V5 分派、非计票澄清和已有四维审核机制。

在线验证使用上一轮四个**已完成、执行结果已知**的工程用例，在独立数据库中以 V6 新方法复验：空气近似理想气体、错误谓词“完全等同于”、映射材料缺失、气温影响空气密度并发组。前三组串行，第四组三席并发；继续使用冻结的 48 个关系、相同来源和可靠性先验。初始请求 12 次，最多 3 次安全字段补正，已知 token 停止线 600000，出现未知请求即停止扩展。

旧 504 未知用例未重发；这些工程用例不是人工金标准，不能据此计算论文准确率。回复自洽、语义确定和满足收录条件分别报告。

## 记录入口

- [修复前快照](D:/knowledge-huopao/outputs/committee_reply_fix_20261117/baseline.json)
- [本轮实验材料](D:/knowledge-huopao/outputs/committee_reply_fix_20261117/live/experiment.json)
- [原始验收记录](D:/knowledge-huopao/outputs/committee_acceptance_20261117/live/report.json)
- [结构纠正实现](D:/knowledge-huopao/review_agent/response_projection.py)
- [针对性回归](D:/knowledge-huopao/tests/test_response_projection.py)

## 实测结果

12 次初始请求均 HTTP 200，**12/12 原始回复直接满足 schema 与一致性检查**，上一轮为 9/12。本轮未调用字段补正，未触发结构投影兜底，无新增 504 或未知请求。三类历史错例均已有回归覆盖；本轮相同工程任务未再次出现这三类错误。这是有限用例上的工程验证，不能外推为以后不会再出现模型输出矛盾。

| 用例 | 有效、自洽回复 | 四维结果（来源/表达/映射/范围） | 研究流转 |
|---|---:|---|---|
| 空气可近似看成理想气体 | 3/3 | unresolved / unresolved / valid / include | manual_review |
| 将近似谓词改为“完全等同于” | 3/3 | contradicted / semantic_revision / conflict / include | revise |
| 正式映射材料缺失 | 3/3 | unresolved / unresolved / context_incomplete / include | manual_review |
| 气温影响空气密度（三席并发） | 3/3 | supported / standard / valid / include | accept |

空气用例三个模型均将限定信息检查设为 unknown，并保留 MOD-001 unresolved 发现，替代了此前“pass 与 unresolved 并存”的无效回复。原候选的 `can` 未被程序改成 `asserted`。这一用例的映射已被识别，但来源与表达轴仍有模态待确认，因此没有证明该空气候选的完整通过路径。并发组单独展示了研究 accept 路径，不代表生产准入。

`report.json.core_three_case_gate_passed=true` 表示前三组约定的映射工程验收、三席一致性、动态权重校验、流转和串行调度检查通过；其中正例断言针对映射识别，不能解读为三个候选全部通过。原报告的 `positive_accept_path_demonstrated=false` 特指空气正例；本轮任一正例研究通过为 true，见只读验证摘要。并发组 Qwen/Gemma 请求重叠 45.57 秒，不能据此认定旧 504 的原因。

已知用量 267663 输入 + 14906 输出 = 282569 tokens；无价格材料，未估算费用。旧三个未知请求保持原状态，未重发，费用也未记为零。

32 项针对性检查与 132 项兼容检查全部通过，共 164 项。只读复核通过：冻结提案、原始回复与请求回执绑定一致，候选未修改，可靠性先验保持 0.5，权重按四轴冻结规则计算；历史源数据库、上一轮报告和生产本体 head 哈希未变，所有写入 outbox 和 jobs 均为零。运行代码哈希与随实验归档的代码一致。

- [本轮原始报告](D:/knowledge-huopao/outputs/committee_reply_fix_20261117/live/report.json)
- [最终校验与用量](D:/knowledge-huopao/outputs/committee_reply_fix_20261117/verification.json)
- [只读复核脚本](D:/knowledge-huopao/outputs/committee_reply_fix_20261117/verify.py)
- [针对性检查日志](D:/knowledge-huopao/outputs/committee_reply_fix_20261117/final_targeted_checks.log)
- [兼容检查日志](D:/knowledge-huopao/outputs/committee_reply_fix_20261117/compatibility_checks.log)

下一步应明确“可近似看成”中模态与近似关系的标注规则，并对该来源做人工语义复核；随后在新候选上试标。无需为了获得 accept 再请求同一组投票。
