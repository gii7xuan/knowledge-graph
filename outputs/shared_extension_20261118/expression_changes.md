# 表达和范围修订逐条对照（2026-11-18）

20条覆盖审查先决项已落实为具体修订，另为PILOT-011补齐字面量表示，共21份隔离草案。下列after为拟议表达，尚未独立批准或应用；原候选和旧证据没有改写。新证据上下文已单独冻结，不能复用旧票。

## PILOT-001

原表达：covolume → does not have → physical meaning

拟议表达：covolume → does not have → physical meaning

变更字段：semantic_context

修订依据：Keep covolume / does not have / physical meaning and negative polarity. The assertion is inside the author's 'Think of it as' interpretive instruction. The preceding 'has been described as' is an attributed account; do not manufacture a direct factual contradiction or a universal denial.

原始引用：

> Think of it as not having physical meaning but as simply a number that allows for a better fit to observed experimental data.

原始候选绑定：cand_0e7425e58d0e11ea564b / revision 1

新证据哈希：a814e2733300d20ee5321363de29ded0ce6c1439159b5f7b9f5953390c0d791c

状态：proposed；未知限定字段：无。

## PILOT-002

原表达：thermodynamics → introduced concepts of → work and energy

拟议表达：work and energy → were introduced in → earlier sections of this text

变更字段：head, surface_relation, tail, qualifiers.time_scope, qualifier_status.time_scope, semantic_context

修订依据：Concept group to a location in the text; thermodynamics is not an agent. Preserve that work and energy are the major concepts referred to in the source.

qualifiers：{"polarity": "positive", "condition": null, "time_scope": "earlier sections", "applicability": null, "modality": "asserted", "quantifier": null} → {"polarity": "positive", "condition": null, "time_scope": null, "applicability": null, "modality": "asserted", "quantifier": null}

qualifier_status：{"polarity": "extracted", "condition": "not_stated", "time_scope": "extracted", "applicability": "not_stated", "modality": "extracted", "quantifier": "not_stated"} → {"polarity": "extracted", "condition": "not_stated", "time_scope": "not_stated", "applicability": "not_stated", "modality": "extracted", "quantifier": "not_stated"}

原始引用：

> Some of the concepts of thermodynamics were introduced in earlier sections, work and energy being the major ones.

原始候选绑定：cand_1c49ce3e5f3cc639175f / revision 1

新证据哈希：504593fba99938ee305ceccc1caca7267b2aedd1bcf52ff7a8de8ebc29e2420e

状态：proposed；未知限定字段：无。

## PILOT-004

原表达：实际流体的流动 → 不符合 → 式(3.9)

拟议表达：实际流体的流动 → 不符合 → 式(3.9)

变更字段：qualifiers.applicability, qualifier_status.applicability, semantic_context

修订依据：补证定位式(3.9)及黏性机械能损失说明。否定仅指该局部方程形式；不否定所有伯努利方程，也不否定总能量守恒。

qualifiers：{"polarity": "negative", "condition": null, "time_scope": null, "applicability": null, "modality": "asserted", "quantifier": null} → {"polarity": "negative", "condition": null, "time_scope": null, "applicability": "本节式(3.9)的机械能守恒表述", "modality": "asserted", "quantifier": null}

qualifier_status：{"polarity": "extracted", "condition": "not_stated", "time_scope": "not_stated", "applicability": "not_stated", "modality": "extracted", "quantifier": "not_stated"} → {"polarity": "extracted", "condition": "not_stated", "time_scope": "not_stated", "applicability": "extracted", "modality": "extracted", "quantifier": "not_stated"}

原始引用：

> 也就是说，在实际流体的流动中有机械能的损失，不符合式(3.9)。

原始候选绑定：cand_29b12271b98f4caa3b39 / revision 1

新证据哈希：f2481ccf9a5a0b9181fcccbcda46add1dd02b07e61d2a47e78e9a6fc1efa2ee3

状态：proposed；未知限定字段：无。

## PILOT-008

原表达：we → must account for → the energy used to make these changes

拟议表达：the authors' control-volume analysis in this passage → must account for → the energy associated with the described pressure and density changes of entering or leaving material

变更字段：head, tail, qualifiers.condition, semantic_context

修订依据：Methodological accounting requirement. Resolve we and these changes through the contiguous control-volume discussion; retain must, without inventing a physical causal relation.

qualifiers：{"polarity": "positive", "condition": "Because of this", "time_scope": null, "applicability": null, "modality": "must", "quantifier": null} → {"polarity": "positive", "condition": "material entering or leaving the fixed control volume can have a different pressure and density", "time_scope": null, "applicability": null, "modality": "must", "quantifier": null}

原始引用：

> Because of this, we must account for the energy used to make these changes.

原始候选绑定：cand_c3dc22e0be2c9a9ef937 / revision 1

新证据哈希：41397b9f5bbf5a48a19b0bb392fc9dabfafee0be24b74a5c8880c561ebf143f4

状态：proposed；未知限定字段：无。

## PILOT-012

原表达：压力比 → 是 → 激波强度

拟议表达：the pressure ratio across the shock in this passage → is used in this passage to characterize → the strength of the shock

变更字段：head, surface_relation, tail, semantic_context

修订依据：The explicit parenthetical pressure ratio (i.e., the strength) supplies a local characterization. Ratio, pressure and shock intensity are distinct roles; this is not global identity or class equivalence. Original unknown polarity remains unknown pending review.

原始引用：

> given the pressure ratio (i.e., the strength) of the shock, we know the temperature behind the wave and the increase in density across the wave.

原始候选绑定：cand_5d2bcc4902e79c36b827 / revision 1

新证据哈希：ed340124bb265152b79760d492dc4c87b00e218fa1aab8201c7e5f9654b586e6

状态：proposed；未知限定字段：polarity。

## PILOT-015

原表达：more tightly packed molecules → cause → more collisions and higher pressure

拟议表达：the molecular packing, collision frequency, and gas pressure described in this passage → are related by the local comparative claim → The more tightly packed the molecules are, the more collisions occur—the higher the pressure is.

变更字段：head, surface_relation, tail, tail_kind, qualifiers.quantifier, qualifier_status.quantifier, semantic_context

修订依据：Keep the whole comparative proposition. Do not silently distribute it into separate unconditional causal edges; more marks comparative degree rather than a count of objects.

qualifiers：{"polarity": "positive", "condition": null, "time_scope": null, "applicability": null, "modality": "asserted", "quantifier": "more"} → {"polarity": "positive", "condition": null, "time_scope": null, "applicability": null, "modality": "asserted", "quantifier": null}

qualifier_status：{"polarity": "extracted", "condition": "not_stated", "time_scope": "not_stated", "applicability": "not_stated", "modality": "extracted", "quantifier": "extracted"} → {"polarity": "extracted", "condition": "not_stated", "time_scope": "not_stated", "applicability": "not_stated", "modality": "extracted", "quantifier": "not_stated"}

tail_kind："event" → "proposition"

原始引用：

> The more tightly packed the molecules are, the more collisions occur—the higher the pressure is.

原始候选绑定：cand_495514d901678b084b1f / revision 1

新证据哈希：bbe9b5e451446f262b55374369a48b2584e7b6f3981deaadbf1c22671d76dfef

状态：proposed；未知限定字段：无。

## PILOT-016

原表达：functional relationship → varies from substance to substance → substance

拟议表达：the functional relationship described here as an equation of state → varies with → the substance whose state it describes

变更字段：head, surface_relation, tail, semantic_context

修订依据：The object denotes the substance argument of the state relationship, not another function or a class-equivalence target.

原始引用：

> This functional relationship varies from substance to substance and is known as an equation of state.

原始候选绑定：cand_9cbc360b7df42c92e161 / revision 1

新证据哈希：d1ede743e648ba430566d710eec808cbc704bf7a3e2534095b8ee43e4416b7cb

状态：proposed；未知限定字段：无。

## PILOT-017

原表达：Eulerian frame → chooses → a CV (real or imaginary)

拟议表达：the authors' analysis in the Eulerian frame → uses → a chosen real or imaginary control volume

变更字段：head, surface_relation, tail, qualifiers.applicability, qualifier_status.applicability, semantic_context

修订依据：We chose the control volume in this analysis. The frame is the setting, not the choosing agent; retain real or imaginary without distribution.

qualifiers：{"polarity": "positive", "condition": null, "time_scope": null, "applicability": null, "modality": "asserted", "quantifier": null} → {"polarity": "positive", "condition": null, "time_scope": null, "applicability": "the Eulerian-frame discussion in this passage", "modality": "asserted", "quantifier": null}

qualifier_status：{"polarity": "extracted", "condition": "not_stated", "time_scope": "not_stated", "applicability": "not_stated", "modality": "extracted", "quantifier": "not_stated"} → {"polarity": "extracted", "condition": "not_stated", "time_scope": "not_stated", "applicability": "extracted", "modality": "extracted", "quantifier": "not_stated"}

原始引用：

> Recall that in the Eulerian frame, we chose a CV (real or imaginary) and observed how the energy within the volume changes based upon the energy carried into or out of it by any entering or exiting substance as well as any heat or work done at the system boundaries.

原始候选绑定：cand_e8245c9e46331262412b / revision 1

新证据哈希：9145ce442dba8991b3ca4df9d7d3ae7ef493c8c700316f3839caa473c2ad4f47

状态：proposed；未知限定字段：无。

## PILOT-020

原表达：second term on the RHS → tells us how much material has left → CV

拟议表达：the second term on the right-hand side of Equation 2.129 in this text → is described by the text as indicating → how much material has left the control volume

变更字段：head, surface_relation, tail, tail_kind, qualifiers.polarity, qualifiers.modality, qualifiers.applicability, qualifier_status.polarity, qualifier_status.modality, qualifier_status.applicability, semantic_context

修订依据：Contiguous original source identifies Equation 2.129 and explicitly says what its second term tells us. Preserve that affirmative textual statement; do not infer cumulative mass or replace material with another property.

qualifiers：{"polarity": "unknown", "condition": null, "time_scope": null, "applicability": null, "modality": "unknown", "quantifier": null} → {"polarity": "positive", "condition": null, "time_scope": null, "applicability": "the explanation of Equation 2.129 in this text", "modality": "asserted", "quantifier": null}

qualifier_status：{"polarity": "unknown", "condition": "not_stated", "time_scope": "not_stated", "applicability": "not_stated", "modality": "unknown", "quantifier": "not_stated"} → {"polarity": "extracted", "condition": "not_stated", "time_scope": "not_stated", "applicability": "extracted", "modality": "extracted", "quantifier": "not_stated"}

tail_kind："entity" → "proposition"

原始引用：

> The second term on the RHS tells us how much material has left the CV, while the third term tells us how much material has entered.

原始候选绑定：cand_062eec7ea3f1ef71472c / revision 1

新证据哈希：c5cb223b8fe992659771fff9c4443ffe0925aa4a96322000b1ed6d43d86ac3b5

状态：proposed；未知限定字段：无。

## PILOT-021

原表达：a diaphragm → can burst → very quickly

拟议表达：the diaphragm separating the gas regions in this passage → can undergo → a rupture described as very quick

变更字段：head, surface_relation, tail, semantic_context

修订依据：Rupture is the event; very quickly is its manner. Keep the original can and unknown polarity; do not create a physical object named very quickly or add device instructions.

原始引用：

> regions of gas. These regions are separated by a diaphragm that can burst very quickly and contain one gas at high pressure and another at lower pressure.

原始候选绑定：cand_b638de124cd3710aa1c3 / revision 1

新证据哈希：bf58d0d9e0d2490735f842637d4becec19770c3c5fe40804c7252ede2cd0d001

状态：proposed；未知限定字段：polarity。

## PILOT-023

原表达：covolume → is → simply a number that allows for a better fit to observed experimental data

拟议表达：covolume → is to be treated in this passage as → a number that allows for a better fit to observed experimental data

变更字段：surface_relation, tail, qualifiers.quantifier, qualifiers.modality, qualifier_status.quantifier, qualifier_status.modality, semantic_context

修订依据：Retain the interpretive directive and the contrast with the preceding negated characterization. Simply is discourse, not a numerical quantifier; directive force is preserved lexically rather than normalized to asserted.

qualifiers：{"polarity": "positive", "condition": null, "time_scope": null, "applicability": null, "modality": "asserted", "quantifier": "simply"} → {"polarity": "positive", "condition": null, "time_scope": null, "applicability": null, "modality": "unknown", "quantifier": null}

qualifier_status：{"polarity": "extracted", "condition": "not_stated", "time_scope": "not_stated", "applicability": "not_stated", "modality": "extracted", "quantifier": "extracted"} → {"polarity": "extracted", "condition": "not_stated", "time_scope": "not_stated", "applicability": "not_stated", "modality": "unknown", "quantifier": "not_stated"}

原始引用：

> Think of it as not having physical meaning but as simply a number that allows for a better fit to observed experimental data.

原始候选绑定：cand_8db241d825f254dd7bc5 / revision 1

新证据哈希：c280bc6a5c4b0e2bbc2b7e73d844ce3ccc0d5674ef87e06bf169e1d349664ee8

状态：proposed；未知限定字段：modality。

## PILOT-024

原表达：we → have arbitrarily assumed → one inlet and one outlet

拟议表达：the authors' analysis in this passage → assumes → one inlet and one outlet

变更字段：head, surface_relation, qualifiers.quantifier, semantic_context

修订依据：One attaches separately to inlet and outlet within a conjunctive local assumption. Preserve assumed; do not turn it into an asserted structure of every control volume.

qualifiers：{"polarity": "positive", "condition": null, "time_scope": null, "applicability": null, "modality": "assumed", "quantifier": "one"} → {"polarity": "positive", "condition": null, "time_scope": null, "applicability": null, "modality": "assumed", "quantifier": "one inlet and one outlet"}

原始引用：

> We have also arbitrarily assumed one inlet and one outlet.

原始候选绑定：cand_79ce6455722817228b9a / revision 1

新证据哈希：9e0857f57249bec927c985b14646cf684b2097bf05ffb3fffd76d9878beff9c2

状态：proposed；未知限定字段：无。

## PILOT-027

原表达：质量力 → 是 → 有势力

拟议表达：本段伯努利方程的沿流线积分推导 → 以……为前提之一 → 质量力为有势力

变更字段：head, surface_relation, tail, tail_kind, qualifiers.condition, qualifiers.applicability, qualifier_status.applicability, semantic_context

修订依据：有势质量力是前提之一；其余条件为定常 AND（正压 OR 不可压缩）。不是所有质量力的无条件分类。

qualifiers：{"polarity": "positive", "condition": "如果流动满足", "time_scope": null, "applicability": null, "modality": "asserted", "quantifier": null} → {"polarity": "positive", "condition": "流动定常（各参量对时间的偏导数为零），且（流体为正压性的，或流体不可压缩）", "time_scope": null, "applicability": "本段理想流体欧拉运动方程沿流线积分的推导", "modality": "asserted", "quantifier": null}

qualifier_status：{"polarity": "extracted", "condition": "extracted", "time_scope": "not_stated", "applicability": "not_stated", "modality": "extracted", "quantifier": "not_stated"} → {"polarity": "extracted", "condition": "extracted", "time_scope": "not_stated", "applicability": "extracted", "modality": "extracted", "quantifier": "not_stated"}

tail_kind："entity" → "proposition"

原始引用：

> 如果流动满足：i 流动是定常的（各参量对时间的偏导数为零）；ii 质量力是有势力；iii 流体是正压性的（密度可以只表示为压力的函数）或不可压缩，则可以将欧拉运动微分方程沿着流线积分，得：

原始候选绑定：cand_a8f30a593fbf5bf0ea24 / revision 1

新证据哈希：36c6e9b0f994b2520488ecca7ef089996eee81944de61766ab3d66c54aed0e39

状态：proposed；未知限定字段：无。

## PILOT-028

原表达：covolume of the gas → has been described as → a parameter that takes into account the physical size of the molecules and any intermolecular forces created by their proximity to one another

拟议表达：covolume of the gas → has been described as → a parameter that takes into account the physical size of the molecules and any intermolecular forces created by their proximity to one another

变更字段：semantic_context

修订依据：An attributed description of covolume, preserving its source voice and both molecular-size/intermolecular-force components. Not a universal identity or an automatic contradiction of the neighboring interpretive instruction.

原始引用：

> The covolume of the gas has been described as a parameter that takes into account the physical size of the molecules and any intermolecular forces created by their proximity to one another.

原始候选绑定：cand_f11bb79b506302a167e0 / revision 1

新证据哈希：a0a771d6419e6420ebe8e41e7e9adb7272276220881f46ed66d8dc4c427db458

状态：proposed；未知限定字段：无。

## PILOT-029

原表达：first law of thermodynamics → can be written for → a fixed mass of material undergoing some thermodynamic process

拟议表达：first law of thermodynamics → can be written for → a fixed mass of material undergoing some thermodynamic process

变更字段：tail_kind, semantic_context

修订依据：The tail denotes a fixed-mass system in the stated process context, not an independent event that has been asserted to happen. Retain the can and the full fixed-mass/process condition.

tail_kind："event" → "entity"

原始引用：

> For a fixed mass of material, undergoing some thermodynamic process, the first law of thermodynamics can be written as

原始候选绑定：cand_e8b57c7d4a6370da244d / revision 1

新证据哈希：ead2c7444cbd4e55d7063dc8a34ffe86e5114a602094f7fd8cbfc98a96b759e7

状态：proposed；未知限定字段：无。

## PILOT-030

原表达：激波 → 在很小距离内发展 → 发展

拟议表达：the shock described in this passage → develops through → a development event occurring over a very small distance

变更字段：head, surface_relation, tail, qualifiers.quantifier, qualifiers.applicability, qualifier_status.quantifier, qualifier_status.applicability, semantic_context

修订依据：Separate the development event from its qualitative spatial extent. The repeated tail development is not an independent object; do not add numerical scales or operational calculations.

qualifiers：{"polarity": "unknown", "condition": null, "time_scope": null, "applicability": null, "modality": "asserted", "quantifier": "very small distance"} → {"polarity": "unknown", "condition": null, "time_scope": null, "applicability": "the shock model discussed in this paragraph", "modality": "asserted", "quantifier": null}

qualifier_status：{"polarity": "unknown", "condition": "not_stated", "time_scope": "not_stated", "applicability": "not_stated", "modality": "extracted", "quantifier": "extracted"} → {"polarity": "unknown", "condition": "not_stated", "time_scope": "not_stated", "applicability": "extracted", "modality": "extracted", "quantifier": "not_stated"}

原始引用：

> This step discontinuity is the way we model the shock—there is actually a very small distance over which a shock will develop so that the pressure increase is rapid, but continuous.

原始候选绑定：cand_cf98bb985043e0b63ecc / revision 1

新证据哈希：cb460164b8342759277a632ce921e20cb5713cc60ce5b38a7737726dc38c71b1

状态：proposed；未知限定字段：polarity。

## PILOT-031

原表达：the characteristics → tend to bend → bending

拟议表达：the characteristics involved in the rarefaction-wave reflection described here → tend to undergo → bending during that reflection

变更字段：head, surface_relation, tail, qualifiers.modality, qualifiers.applicability, qualifier_status.modality, qualifier_status.applicability, semantic_context

修订依据：Resolve the characteristics through the source paragraph. Preserve lexical tendency; the existing enum does not establish equivalence with frequency usually, so the modality status stays explicitly unknown.

qualifiers：{"polarity": "unknown", "condition": null, "time_scope": null, "applicability": null, "modality": "usually", "quantifier": null} → {"polarity": "unknown", "condition": null, "time_scope": null, "applicability": "the rarefaction-wave reflection described in this passage", "modality": "unknown", "quantifier": null}

qualifier_status：{"polarity": "unknown", "condition": "not_stated", "time_scope": "not_stated", "applicability": "not_stated", "modality": "extracted", "quantifier": "not_stated"} → {"polarity": "unknown", "condition": "not_stated", "time_scope": "not_stated", "applicability": "extracted", "modality": "unknown", "quantifier": "not_stated"}

原始引用：

> The result is that the characteristics tend to bend, making the calculations somewhat more complex.

原始候选绑定：cand_e1682ea5cb4faa99e529 / revision 1

新证据哈希：dc87acd4c69af30478dfccfb0222590aef03c4082af1857a09749ae085e14fb0

状态：proposed；未知限定字段：polarity, modality。

## PILOT-032

原表达：specific gas constant → can be determined from → universal gas constant

拟议表达：specific gas constant → can be determined through → the source-described determination using the universal gas constant and the molar mass

变更字段：surface_relation, tail, tail_kind, semantic_context

修订依据：Represent the described determination with both input roles. Do not make the universal gas constant the sole input, reverse the operation or perform a calculation.

tail_kind："entity" → "event"

原始引用：

> The specific gas constant can be determined from the universal gas constant by dividing the latter by the molar mass:

原始候选绑定：cand_b616c903145361531b01 / revision 1

新证据哈希：6d20afa8f1ae61eadb3472269d70655edb4738ca9521ca093cd063659c6571df

状态：proposed；未知限定字段：无。

## PILOT-036

原表达：proper thermodynamics terminology → would require → use of δ'' instead of d in Equation 2.43

拟议表达：proper thermodynamics terminology → would require → use of δ'' instead of d in Equation 2.43

变更字段：qualifiers.modality, qualifiers.condition, qualifier_status.condition, semantic_context

修订依据：Keep would require in the predicate and modality attachment, and preserve the original δ double-stroke text verified in the PDF. Because gives a reason, not an antecedent. The authors ignore the convention here; they do not assert that they used it. Unencoded normative modality remains unknown, never asserted.

qualifiers：{"polarity": "positive", "condition": "because of path dependency considerations", "time_scope": null, "applicability": null, "modality": "would", "quantifier": null} → {"polarity": "positive", "condition": null, "time_scope": null, "applicability": null, "modality": "unknown", "quantifier": null}

qualifier_status：{"polarity": "extracted", "condition": "extracted", "time_scope": "not_stated", "applicability": "not_stated", "modality": "unknown", "quantifier": "not_stated"} → {"polarity": "extracted", "condition": "not_stated", "time_scope": "not_stated", "applicability": "not_stated", "modality": "unknown", "quantifier": "not_stated"}

原始引用：

> Proper thermodynamics terminology would require us to use the  $\delta''$  instead of  $d$  in Equation 2.43 because of path dependency considerations, but for our purposes, we shall ignore this fact.

原始候选绑定：cand_7f05c38a350ef3efcb6d / revision 1

新证据哈希：66dd2526303527bc489b8941b22eacd102b8bc0840b13d03ced70e9a9efd998c

状态：proposed；未知限定字段：modality。

另附原页视觉核证；该侧录尚不等同于文本证据包中的已审核证据。

## PILOT-038

原表达：不可压缩流体 → 各处密度均相等 → 密度

拟议表达：本段不可压缩流体的密度 → 满足时空约束 → 各处密度相等且不随时间变化

变更字段：head, surface_relation, tail, tail_kind, semantic_context

修订依据：将密度量与其约束命题分开，保留空间各处相等 AND 时间不变及不可压缩条件；与019同源，不生成独立证据票。

tail_kind："entity" → "proposition"

原始引用：

> 对于不可压缩流体，各处密度均相等，且不随时间变化，即  $\rho =$  常数，则连续方程为：

原始候选绑定：cand_087c428aa9cd2eba7919 / revision 1

新证据哈希：b5d8ed4afdcc9a5da92d6fbb2805e1b79b242bf85e6b6065a7154f79400a7a15

状态：proposed；未知限定字段：无。

## PILOT-011

原表达：哈密顿算子 → 是 → ∂/∂x i + ∂/∂y j + ∂/∂z k

拟议表达：哈密顿算子 → 是 → ∂/∂x i + ∂/∂y j + ∂/∂z k

变更字段：value, datatype

修订依据：原文以破折号引出哈密顿算子的定义式，属于局部符号定义，不是一般实体属性。

value：null → "∂/∂x i + ∂/∂y j + ∂/∂z k"

datatype：null → "http://www.w3.org/2001/XMLSchema#string"

原始引用：

> $\nabla$  ——哈密顿算子，  $\nabla = \frac{\partial}{\partial x}\pmb {i} + \frac{\partial}{\partial y}\pmb {j} + \frac{\partial}{\partial z}\pmb{k}_{\circ}$

原始候选绑定：cand_19870dd12808821a02e2 / revision 1

新证据哈希：1bfecc225bd3a70f89f1083c3c7dba98aa5cabfe9dd8fd500185d4b8ce9bddc1

状态：proposed；未知限定字段：无。

另附原页视觉核证；该侧录尚不等同于文本证据包中的已审核证据。
