# 统一扩充候选版本（2026-11-18）

当前版本：shared_extension_candidate_20261118_v2。

候选ID：`ontology_candidate_3182272c1ff152b9517bd4bdf5052d85a2c7d32faef8530ccad59d7e3800b8f2`。

已生成20条表达/范围修订和额外1条literal表示修订，共21份proposed隔离草案；已作出2项类身份设计决策。原候选仍为revision 1，没有应用修订或正式映射。

扩充候选包含231类、74个对象关系、2个数据属性。旧189类和48个关系的对象内容、IRI/ID、定义、别名、父类、方向、域和值域，以及全部原RDF三元组均保留。新增42类中40类用于来源限定的命题和角色表示，2类为一般物质状态方程、连续方程（质量守恒）通用概念；这些表示类不是42条新的物理规律。新增26个对象关系及2个xsd:string数据属性，保持各关系的角色和条件义务。

两个旧“状态方程”应用节点不合并、不重定向、不添加未经证明的equivalentClass/sameAs；新通用概念具有单独ID及英文别名。质量守恒方程/连续方程按本轮来源中的同称建立一个概念和别名。具体身份决策及原始引用都已保存。

40条均准备了建模问项和拟议表示路线。路线存在不等于语义覆盖审核通过。源命题层的新增类型不替代具体对象的类型证据；每个候选仍须保持完整内容、限定信息及来源，不能因本体可存文本就自动准入。

PILOT-015以完整比较命题保存，不再拟建分拆的直接因果边；PILOT-034仍保持结果到原因方向。状态方程、连续方程的别名路线不靠新增万能“是”关系解决。

目前未知限定字段：`{"PILOT-012": ["polarity"], "PILOT-021": ["polarity"], "PILOT-023": ["modality"], "PILOT-030": ["polarity"], "PILOT-031": ["polarity", "modality"], "PILOT-036": ["modality"]}`。023指令、031倾向、036规范性语气保留原词，现有枚举不能完整编码时继续unknown；012、021、030的原未知极性也没有靠本轮建模补成肯定。unknown继续阻止完整表达准入。

本包通过命名类/父类/域值域/字面量应用约束校验、Turtle与JSON视图往返校验、来源及原版本保留核验；9项针对性检查通过。外部OWL推理器与独立语义审核未运行，旧189类并未因此全部重新认证。approval.json为pending，candidate_manifest.json明确eligible_for_formal_experiment=false，本包不能被load_release当作已发布版本加载。

## 文件

- [逐条表达对照](expression_changes.md)
- [21份完整修订草案](expression_drafts.json)
- [2项类身份决策](class_identity_decisions.json)
- [本体Turtle](ontology_candidate/ontology.ttl)
- [本体JSON视图](ontology_candidate/ontology_snapshot.json)
- [候选清单及内容哈希](ontology_candidate/candidate_manifest.json)
- [新定义逐ID来源证明](ontology_candidate/model_source_proofs.json)
- [40条语义问项](ontology_candidate/competency_questions.json)
- [验证记录](verification.json)
- [针对性检查](checks.log)

这是一份供所有实验方法共同审核、采用的候选版本。本轮没有修改现有冻结版本或生产head，没有向单个方法私配关系，没有借AI标签更新可靠性或计算论文分数。新增API请求为0，既有Gemma未知请求未重发。

下一步对这份固定候选执行独立修订与建模审核；只有获得对应版本的有效结论后，才能应用修订、冻结可用发布版并重新绑定各方法输入。不能将该候选状态改写为已经通过审核。
