# DEV-008 映射路径核对

候选 `cand_cc7f508e0ee31b09d27d`，版本2；正式关系 `R_1654fcf38913d94cb712`。原始关系短语保留，未替换为另一个含义。

|阶段|现有证据|结论|
|---|---|---|
|原文及两端身份|冻结证据包、文档内身份ID、类型定义|可核对|
|检索|当前子集8个属性；扩大到绑定发布版189类、81属性及别名|已执行，词法召回不等于语义确认|
|正式关系身份|R_1654fcf38913d94cb712 已存在|不是必须新增关系|
|约束|头尾类型分别满足既有domain/range|pass|
|已发布定义|绑定发布版description/definitions为空|guard=unknown，RELATION_DEFINITION_UNAVAILABLE|
|现有定义草案|原文有出处，delta第2版；仅增加定义注解|未发布|
|独立本体审核|三席到齐：revise / approve / approve|未满足批准门槛|
|语义能力检查历史记录|B有效；A提供方结果未知；C契约失败|unknown，需先对账，不能盲重试|
|内存元数据对照|给同一关系加载现有草案定义，类型约束不变|guard=pass；不代表批准|
|发布→重绑定→新绑定语义审核|均未执行|正式映射仍未完成|

本体审核ID：`ontology_audit_c2825120297e414b9d8e64f942c14d3c`；选定提案：`4ba6cd402b25d3013256b4e6daf9b949519181fdb06ca010a1176ecf974d7648`；delta_hash：`330e04dcda9a0c9f1ca6bac7d8f32c8621b2c766f6ca68fa2d8f3e0c228263ca`。
发布清单明确阻塞：`independent ontology approval quorum not satisfied`。
Qwen的revise票分项为pass或不适用，suggested_changes和requested_context均为空。这是应定向询问的总决定/理由一致性问题；不能凭其余两票自动把revise改成approve。

本轮结论：完整走查到“既有草案的独立批准及发布”这个直接阻塞点，未声称成功入库。先对账能力检查的未知请求，再按现有预算仅澄清未解决的本体义务。批准发布后，须重新绑定候选并审核关系语义与约束；旧候选票不能直接转给新绑定。
内存对照使用裸三元组检查元数据，没有生成合法的新release绑定，所以不构成版本迁移或正式映射验证。

完整冻结关系、实体、检索轨迹、票摘要、能力检查记录见 [结构化证据](mapping_pilot_DEV008.json)。
