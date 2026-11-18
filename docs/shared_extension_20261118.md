# 表达修订、类身份和统一扩充候选版（2026-11-18）

已将20条表达/范围先决项落实为具体修订，并为PILOT-011补齐字面量表示，形成21份隔离草案。类身份的两项设计决策及统一扩充候选文件也已生成。当前为**待独立语义审核的候选版**，没有把工程判断、AI预标或未完成模型回复冒充批准。

## 表达修订

主要修订包括：

- 001保留covolume主体、原否定关系与尾项，仅修局部解释范围；不继续首版this passage改写。
- 002把概念与篇章位置分开，017修正选择控制体的施事；008明确we与these changes的来源指代。
- 012将压力比与激波强度处理为局部表征，020明确式2.129第二项及其完整解释命题。
- 015保留整段比较命题及其共同范围；024保留一入口且一出口的假设与数量角色；027保留定常且（正压或不可压缩）的逻辑范围。
- 021、030、031区分事件与方式、空间尺度、倾向；032保留确定过程的两个输入角色。
- 023的simply、036的because理由不再放入错误限定字段；指令、倾向、规范语气保留原词，编码不足时显式unknown。
- 038区分密度与其时空约束；011保持原尾项，补value和xsd:string，并附原页核证待审标记。

21份草案均为proposed，原候选仍为revision 1。新证据上下文包含原引用、精确原书补证及完整当前片段；所有材料作为保护证据保留。旧票不复用，PDF视觉核证没有冒充已注册的文本审核证据。

原未知极性和无法完整编码的模态仍明确保留。处理具体表达不意味着这些未知状态、正式类型、映射及准入义务已经通过审核。

## 类身份

**状态方程：**旧 `C_eaa767de9c851cd73be7` 和 `C_b944bb2feba8240d5c86` 保留原有气象应用描述，不合并、不重定向、不擅自认定类等价。候选版另建“一般物质状态方程”，赋独立ID和 `equation of state` 别名，与旧节点仅保留参照关系。来源中的函数关系实例和概念类仍分开。

**连续方程：**按本轮原文“质量守恒方程/连续方程”的同称建立一个“连续方程（质量守恒）”概念，保存中英文别名。不将一般微分方程、任意守恒方程或所有具体方程形式视为同一。

这是候选版中已落实的身份设计，尚未经独立建模审核，不是人工裁决。

## 统一扩充候选版

版本为 `shared_extension_candidate_20261118_v2`，候选ID：

`ontology_candidate_3182272c1ff152b9517bd4bdf5052d85a2c7d32faef8530ccad59d7e3800b8f2`

| 项目 | 原冻结版 | 扩充候选版 |
|---|---:|---:|
| 类 | 189 | 231 |
| 对象关系 | 48 | 74 |
| 数据属性 | 0 | 2 |

新增42类中40类用于来源限定的命题、事件和角色表示，2类为上述通用概念；没有把这些表示类包装成新的物理规律。新增26个对象关系与2个字符串数据属性，逐ID记录定义、端点、来源样本及精确引用。旧189类、48个关系和全部旧RDF三元组保留，未放宽任何旧关系的域和值域。

40条均准备了语义问项和拟议表示路线，**不能据此声称语义覆盖率已达100%**。还需核查各改写的完整含义、对象类型及来源限定；能保存一段命题文本不能代替正确的物理概念映射。

## 验证与交接

结构与类型引用校验、Turtle/JSON视图往返、完整性和旧版保留检查通过。9项针对性检查通过，包含旧定义保留、类身份不误合并、条件/比较不被拆分、限定附着修正、未知模态保留，以及篡改和伪批准拒绝。

候选包的 `approval.json` 为pending，`eligible_for_formal_experiment=false`，不能作为已发布版本被加载。本轮未运行外部OWL推理器或独立语义审核，未重新认证全部历史类层级。生产本体、原实验冻结版、160条候选、人工空白包及旧请求库哈希未变；新增API请求、审核票、正式映射、应用修订和发布数均为0。Gemma未知请求仍保留，没有重发。

准备过程中一次过严的状态断言和首版候选已分别留档；当前交接以本页列出的v2哈希为准，未借准备失败重发任何请求。

- [候选版说明与交接](D:/knowledge-huopao/outputs/shared_extension_20261118/README.md)
- [逐条表达对照](D:/knowledge-huopao/outputs/shared_extension_20261118/expression_changes.md)
- [本体Turtle](D:/knowledge-huopao/outputs/shared_extension_20261118/ontology_candidate/ontology.ttl)
- [本体JSON视图](D:/knowledge-huopao/outputs/shared_extension_20261118/ontology_candidate/ontology_snapshot.json)
- [候选清单](D:/knowledge-huopao/outputs/shared_extension_20261118/ontology_candidate/candidate_manifest.json)
- [逐ID来源证明](D:/knowledge-huopao/outputs/shared_extension_20261118/ontology_candidate/model_source_proofs.json)
- [本体候选包](D:/knowledge-huopao/outputs/shared_extension_20261118/shared_ontology_candidate.zip)
- [验证记录](D:/knowledge-huopao/outputs/shared_extension_20261118/verification.json)

下一步审核这份固定候选及对应修订，取得有效结论后再应用和冻结可用发布版；采用时所有实验方法必须使用同一发布哈希和重新绑定的输入，不能单独扩充某个方法。
