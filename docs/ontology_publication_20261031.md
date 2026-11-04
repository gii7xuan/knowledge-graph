# 本体扩展审核与发布处理（2026-10-31）

截至北京时间 2026-10-31T13:32:31.233618+08:00。用户已说明暂时无法调整网关或提供服务端查询，按要求先推进其余工作。**本轮尚未发布新本体。**

完整盘点 102 个任务，当前有 78 份草案；2 份当前草案已收齐有效三席意见，0 份达到审核预览条件。草案准备不代表独立审核通过。

## 已完成的候选复用

5 条候选改用已有正式属性：修订均获原三模型一致批准，新版本也完成三席来源审核，schema guard 均为 pass。端点类型、限定及表面关系未被推断或改写，候选仍待人工处理。

| 候选 | 当前版本 | 复用的正式属性 |
|---|---:|---|
| cand_006761af7b46d4cb89f6 | 2 | R_36719caf973ba4631cbb |
| cand_1733237c3026d874c587 | 3 | R_0e6aeaed149fdb07cfac |
| cand_4d903306be730770f49b | 2 | R_5d7591372f1859dc86e0 |
| cand_e6ace1981cf9b8b16200 | 2 | R_84ec7faad76ddd289126 |
| cand_cc7f508e0ee31b09d27d | 2 | R_1654fcf38913d94cb712 |

## 本体草案与真实分歧

- 气压机制草案：Gemma、DeepSeek 批准，Qwen 的有效 revise 要求说明既有关系能否复用及独立建模必要性。该真实反对意见保留；触发候选仍缺 head_type，未从关系 domain 反填。
- 横风机制按有效复用意见修订为既有属性 R_1654fcf38913d94cb712 的一条定义注释。delta revision 2 仅增加 1 条 skos:definition，RDF 882→883；未增加类/属性，未删除三元组，未修改 domain/range/层级。旧草案及其原票全部保留。
- 横风定义草案当前 Gemma、DeepSeek 批准，Qwen 有效 revise，但七项检查均 pass，且没有具体修订项。该票仍按 revise 处理；只能在能力问题真实通过后，向原模型进行独立、留痕的中性澄清，不能由服务端转换为 approve。当前尚未调用澄清。
- 别名草案、“飞行于”及降弧比较草案仍有未知请求；没有通过重建提案、换模型、降低三席门槛或自动重发规避。

## 自然语言能力问题的实际执行

已单独执行 chapter1_source_coverage，提交经校验的完整章节（55,505 字符）、完整 RDF 基线及哈希核验后的精确补丁。该检查与本体建模票分开记录，不能用结构通过代替。

| 草案 | 版本 | 席位 | 响应 | 覆盖 | 问题结果 |
|---|---:|---|---|---|---|
| ext_4272d42b83144e8c8da86d3530f76019 | 1（历史） | judge_a | invalid_contract |  |  |
| ext_4272d42b83144e8c8da86d3530f76019 | 1（历史） | judge_c | ok | complete | chapter1_source_coverage:pass |
| ext_4272d42b83144e8c8da86d3530f76019 | 1（历史） | judge_b | ok | complete | chapter1_source_coverage:pass |
| ext_f9b004e2df2c4613aa4d060302238889 | 2（当前） | judge_a | api_error |  |  |
| ext_f9b004e2df2c4613aa4d060302238889 | 2（当前） | judge_c | ok | partial | chapter1_source_coverage:fail |
| ext_f9b004e2df2c4613aa4d060302238889 | 2（当前） | judge_b | ok | complete | chapter1_source_coverage:pass |
| ext_4272d42b83144e8c8da86d3530f76019 | 2（当前） | judge_a | api_error |  |  |
| ext_4272d42b83144e8c8da86d3530f76019 | 2（当前） | judge_b | ok | complete | chapter1_source_coverage:pass |
| ext_4272d42b83144e8c8da86d3530f76019 | 2（当前） | judge_c | ok | partial | chapter1_source_coverage:fail |

气压机制及横风定义当前均有 Qwen 网关 504 未知、Gemma pass、DeepSeek 有效 fail/partial，未达到原三模型一致通过。已修正已确认的 JSON/引文契约失败；所有有效负面结果和未知请求均保留，不再抽票重试。
本地逐术语核验：270 个正式术语均有 RDF 标签；249 个可在原文中匹配声明名称，263 个有原文或端点上下文召回。21 个名称无精确词面匹配，已列出供语义复核。该索引反驳“RDF普遍缺标签”的结构性判断，但不能自动推翻覆盖不完整的语义 fail，也不能将词面召回视为完整覆盖。

## 其余任务准备

准备模式执行真实完整本体检索、证据调查及最小草案构建，保存结构、影响和能力预检；明确禁止独立投票与发布。修正了准备模式错误预留三席调用预算的问题，并保留所有历史用量。
当前准备统计：{"status": "completed", "investigated": 97, "preparation_runs": 116, "distinct_tasks": 97, "deltas_generated": 73, "result_categories": {"draft_with_all_local_checks": 71, "draft_missing_local_checks_in_original_audit": 2, "no_current_delta": 24}, "historical_preparation_errors": 1}。缺少可靠类端点、预算用尽或模型动作失败的任务按实际原因留待处理，未虚构草案或批准。
两份早期草案在原规划审计中未跑齐预检，本次已在一致性快照上补做结构、影响和能力检查，结果写入 current_task_dossiers.json；原审计保持原样。
详细执行轨迹见 remaining_preparation_status.json；所有 102 项的现行来源、草案、确定性预检和发布阻塞见 current_task_dossiers.json。

另已导出 78 份未批准 OWL 草案包（ontology.ttl、精确 patch、项目视图与 UNAPPROVED 标记），逐份复核源码、图及补丁哈希。目录为 unapproved_owl_drafts；清单见 unapproved_owl_draft_manifest.json。这些文件没有写入正式发布目录或发布目录库。各草案独立基于同一发布头；发布一份后，其余需重基并重审。

## 代码与验证

- 提供无损完整基线 RDF 加精确补丁交付，发送前重建图并核对哈希；匿名节点差异保留双完整图。冻结审核输入与请求语义身份不随传输压缩变化。
- 补齐独立能力问题执行、引文精确校验、已确认失败恢复、原模型中性澄清及发布校验链。原有效票不可覆盖；未知结果不可自动重发；三模型三家族门槛保持。
- 完整回归曾通过 89 项；语义/澄清/恢复定向回归 28 项通过；准备模式及共享运行时 44 项通过；澄清元数据回归 7 项通过；准备模式最终 3 项回归通过。各组有重叠，不相加。原始运行记录及实际状态见 summary.json 的 test_evidence。测试使用隔离数据库与模拟模型，不将测试通过当作生产审核通过。
完整历史核对：{"validator_votes": 1128, "revision_votes": 259, "ontology_votes": 5, "candidate_revisions": 388, "proposals": 413, "ontology_proposals": 3}；原有效票、候选旧版及冻结提案均未改变。历史未知请求状态变化 0 条。

本轮可对账的审核请求尝试 57 次，新增规划调用 665 次；合计已知输入 11773378、输出 138270 tokens；5 次缺少完整用量。规划日志与持久化审核请求分开统计，缺失用量不补估。

发布头保持 release_c722d176f283335f3f398e3559da5ead968973a870efdea663359669db07b6bb。自动准入、自动发布继续关闭，未新增人工标签，未导入 Neo4j。

## 后续条件

发布仍需：对账未知请求、处理有效建模反对意见及能力覆盖失败、完成其余草案的原三模型独立审核，再逐份进行发布绑定和新版本重审。用户目前不能提供网关或日志；待服务端条件变化后从已保存请求继续，不能把“准备完毕”报告成“发布完毕”。

服务端对账清单中的 request_key 为本地请求身份，未必是服务商可查询的请求 ID。原始 provider_request_id 若可得，保存在 new_request_costs.json 中。

[完整任务与阻塞](D:/knowledge-huopao/runs/ontology_publication_20261031/current_task_dossiers.json) · [能力问题实际结果](D:/knowledge-huopao/runs/ontology_publication_20261031/semantic_competency_results.json) · [服务端对账清单](D:/knowledge-huopao/runs/ontology_publication_20261031/ontology_request_reconciliation.json) · [统计与历史核验](D:/knowledge-huopao/runs/ontology_publication_20261031/summary.json)
