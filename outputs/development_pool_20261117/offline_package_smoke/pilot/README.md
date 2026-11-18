# 开发试标说明

本包 1 条，均为新来源模型抽取候选，尚无人工作答。先阅读 `cards.html` 与共享本体 `ontology_reference.json`；A、B 分别填写自己的 `reviewer_A_blank.json` / `reviewer_B_blank.json`，互不查看结果。标注对象是原候选，修改建议另存，不能用修改后的事实替代原标签。所有标签留空不是 unknown；unknown 必须由人明确选择。

六项原始标签：来源 source_support（supported/contradicted/insufficient/unresolved）；表达 expression_status（faithful/needs_revision/unresolved）；已有映射 mapping_status（correct/incorrect/unassigned/ambiguous/unresolved）；本体覆盖 schema_coverage（covered/not_covered/unresolved）；范围 knowledge_scope（general/conditional/local_definition/example/derivation/unresolved）；操作偏好 admission_preference（include/revise/exclude/unresolved）。操作偏好不能倒推其他标签。

有来源支持也可能表达有误；正文自相矛盾可保留 unresolved。否定、方向、条件、主体指代、局部定义及近似含义逐项核对。关系 ID 为空标 unassigned，不把映射缺失当作事实错误；covered/correct 须列正式 ID、定义、域值域依据。正文相邻段落可能经过省略，不能凭空连接指代；需要额外上下文时记录缺口，不猜答案。初始材料与后续方法输入相同，新增核证材料须独立归档。

模态试标规则 V1：区分认知可能性、能力/许可、假设、近似限定和定义性陈述。“可近似看成”中的近似意义不能丢失，也不因有“可”就机械改成能力；候选 modality 与谓词/附着关系一起判断。确认含义保持可标 faithful；确认错位标 needs_revision 并写依据；来源仍不足才标 unresolved。程序不自动将 can 改成 asserted。该规则是待试标检验的开发规范，不是本包预填答案。

为每条记录支持/反驳 evidence_id、理由、审核人、时间、实际 duration_seconds 和 full_bundle_reviewed；模型结论未展示，saw_model_conclusions 应如实填写。两人原标保留，分歧由人工在 adjudication 中填写 adjudicated_label、证据及最终 reviewer/annotated_at，并将 adjudicated_by 设 human；没有人类裁决不得设 resolved。金标准建设工时与今后 Agent 辅助审核工时分别记录。本轮不生成论文准确率或模型可靠性统计。

仅属开发池。两本书的切片不算独立文档，不能拆分同书片段冒充独立测试；英文候选保留英文原文，语言因素单列。当前 48 个关系来自此前开发材料，应如实报告本体对这些新概念的覆盖限制。
