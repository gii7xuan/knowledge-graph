# 首批七项争议核查

本单展示了 AI 预标及补充材料，属于辅助复核材料，不能声称为盲标结果。保留原始命题及其绑定，不将 AI 的修改建议作为原命题真值。

## PILOT-001

原候选：covolume → does not have → physical meaning

核查问题：作者的 Think of it as 限定能否省略？同段对参数物理解释的张力，应怎样分别记录引述、局部解释和原命题支持？

AI 预标（待复核）：{"source_support": "unresolved", "expression_status": "needs_revision", "mapping_status": "unassigned", "schema_coverage": "not_covered", "knowledge_scope": "local_definition", "admission_preference": "unresolved"}

AI 理由：同一冻结段落先赋予 covolume 分子尺寸及分子间力的解释，后又要求 Think of it as not having physical meaning，两种表述有实质张力。原候选把作者的解释口吻压成无条件否定，不能直接作为普遍事实。48 个正式关系没有参数物理解释或否定该解释的关系；映射缺失独立标 unassigned。

原始引用：

> Think of it as not having physical meaning but as simply a number that allows for a better fit to observed experimental data.

补充原文及精确位置：

[2.2 Other Gas Laws] parent_d10102aa10ad526a5086，原书字符 [23125,23215)

$$
p \left(\mathrm {V} - m _ {\mathrm {g}} b\right) = m _ {\mathrm {g}} R T \tag {2.27}
$$

[2.2 Other Gas Laws] parent_3fd5c6271fcf57daa3e4，原书字符 [23217,23446)

where  $p$  is the pressure of the gas;  $V$  is the volume the gas occupies;  $m_{\mathrm{g}}$  is the mass of the gas;  $R$  is the specific gas constant;  $T$  is the absolute temperature; and  $b$  is the covolume of the gas.

[2.2 Other Gas Laws] parent_d95ba5459588f8d9c356，原书字符 [23448,23991)

The covolume of the gas has been described as a parameter that takes into account the physical size of the molecules and any intermolecular forces created by their proximity to one another. Think of it as not having physical meaning but as simply a number that allows for a better fit to observed experimental data. The units of the covolume are cubic length per mass unit. Usually, the gas covolume is provided in the literature, but an estimation tool has been provided by Corner [7], which will not be repeated here since actual data exist.

[2.2 Other Gas Laws] parent_0ecbd59cb2710bca63a9，原书字符 [24156,24266)

$$
p = \frac {\tilde {R} T}{\tilde {v} - b ^ {\prime}} - \frac {a ^ {\prime}}{\tilde {v} ^ {2}} \tag {2.28}
$$

人工答复：留空；引用支持判断的原始/补充证据 ID，并记录查看过 AI 预标及实际时间。


## PILOT-002

原候选：thermodynamics → introduced concepts of → work and energy

核查问题：原文介绍功和能的施事是否为教材前文，而不是 thermodynamics 这门学科？earlier sections 应放在什么范围字段？

AI 预标（待复核）：{"source_support": "insufficient", "expression_status": "needs_revision", "mapping_status": "unassigned", "schema_coverage": "not_covered", "knowledge_scope": "local_definition", "admission_preference": "revise"}

AI 理由：证据只支持本书前文介绍过热力学中的功和能，不能支持学科 thermodynamics 自身执行 introduced。earlier sections 是篇章位置，候选却放进 time_scope。原候选的主体及附着均需修正。共享本体没有概念介绍或篇章讲授关系，不能以外弹道的研究关系代替。所列证据支持的是局部内容，非完整原命题。

原始引用：

> Some of the concepts of thermodynamics were introduced in earlier sections, work and energy being the major ones.

补充原文及精确位置：

[2.4 Thermodynamics] parent_b668e93b59dc0d0655ec，原书字符 [44257,44474)

Some of the concepts of thermodynamics were introduced in earlier sections, work and energy being the major ones. Here we shall look in detail at two ways of describing thermodynamic systems to proceed with our study.

[2.4 Thermodynamics] parent_3418ec45cf983c331ebb，原书字符 [44476,44525)

We shall define energy for an arbitrary system as

[2.4 Thermodynamics] parent_e6044a309a2feb671e3b，原书字符 [44527,44583)

$$
E = U + \frac {1}{2} m V ^ {2} + m g z \tag {2.38}
$$

人工答复：留空；引用支持判断的原始/补充证据 ID，并记录查看过 AI 预标及实际时间。


## PILOT-011

原候选：哈密顿算子 → 是 → ∂/∂x i + ∂/∂y j + ∂/∂z k

核查问题：请对照原页确认 k_{circ} 的附记是否具有符号意义。仅有当前 Markdown 时不能用常见公式替换。

AI 预标（待复核）：{"source_support": "unresolved", "expression_status": "unresolved", "mapping_status": "unassigned", "schema_coverage": "not_covered", "knowledge_scope": "local_definition", "admission_preference": "unresolved"}

AI 理由：冻结文本给出了哈密顿算子的三项表达，但末项写作 k_{circ}，候选转写为普通 k。该附记是OCR标点还是有意义符号，缺原页不能确定，精确公式转写暂待核。另有 tail_kind=literal 而 value/datatype 均空的表示缺口。共享本体没有算子符号定义或公式数据关系。

原始引用：

> $\nabla$  ——哈密顿算子，  $\nabla = \frac{\partial}{\partial x}\pmb {i} + \frac{\partial}{\partial y}\pmb {j} + \frac{\partial}{\partial z}\pmb{k}_{\circ}$

补充原文及精确位置：

[3.2.2 连续方程] parent_df45401f48ad39a8d78f，原书字符 [154120,154139)

式中  $\rho$  ——流体密度；

[3.2.2 连续方程] parent_908cda23ca89ddd9d3b3，原书字符 [154141,154180)

$\nu$  ——流体速度，  $\nu = u i + v j + w k$

[3.2.2 连续方程] parent_e906dfdf1fbe7574149b，原书字符 [154182,154331)

$\nabla$  ——哈密顿算子，  $\nabla = \frac{\partial}{\partial x}\pmb {i} + \frac{\partial}{\partial y}\pmb {j} + \frac{\partial}{\partial z}\pmb{k}_{\circ}$

[3.2.2 连续方程] parent_fa17df2a0e4c21cd9e78，原书字符 [154333,154388)

对于定常流动，  $\frac{\partial\rho}{\partial t} = 0$  ，连续方程为：

[3.2.2 连续方程] parent_50d8137e261b313edb9a，原书字符 [154390,154442)

$$
\nabla \cdot (\rho \mathbf {v}) = 0 \tag {3.3}
$$

人工答复：留空；引用支持判断的原始/补充证据 ID，并记录查看过 AI 预标及实际时间。


## PILOT-017

原候选：Eulerian frame → chooses → a CV (real or imaginary)

核查问题：原文在 Eulerian frame 中选择控制体的施事是谁？参考系能否在该句中直接作 chooses 的主体？

AI 预标（待复核）：{"source_support": "insufficient", "expression_status": "needs_revision", "mapping_status": "unassigned", "schema_coverage": "not_covered", "knowledge_scope": "general", "admission_preference": "revise"}

AI 理由：证据说在 Eulerian frame 中我们选择控制体，不能直接支持参考系自身执行 chooses。场景被提升为施事主体，需改写；这不是显式反命题，因此不标 contradicted。共享本体中自然坐标跟随、描述等关系有特定定义，不能覆盖欧拉方法选定控制体。所列证据只支持方法背景。

原始引用：

> Recall that in the Eulerian frame, we chose a CV (real or imaginary) and observed how the energy within the volume changes based upon the energy carried into or out of it by any entering or exiting substance as well as any heat or work done at the system boundaries.

补充原文及精确位置：

[2.4 Thermodynamics] parent_050ab3fe51618060b27a，原书字符 [48471,48659)

$$
Q _ {1 - 2} + W _ {1 - 2} = m \left[ \left(h _ {2} - p v _ {2} + \frac {1}{2} V _ {2} ^ {2}\right) - \left(h _ {1} - p v _ {1} + \frac {1}{2} V _ {1} ^ {2}\right) \right] \tag {2.42}
$$

[2.4 Thermodynamics] parent_7721035bf58a180147a2，原书字符 [48661,48743)

Here we note that  $h$  is the specific enthalpy and  $v$  is the specific volume.

[2.4 Thermodynamics] parent_79d85b51ddacf116be62，原书字符 [48745,49263)

We shall now examine the first law of thermodynamics in the Eulerian frame of reference. Recall that in the Eulerian frame, we chose a CV (real or imaginary) and observed how the energy within the volume changes based upon the energy carried into or out of it by any entering or exiting substance as well as any heat or work done at the system boundaries. It is convenient for us to write the first law in terms of the time rate of change of energy, heat, and work. We start by writing Equation 2.40 as a rate equation

[2.4 Thermodynamics] parent_8e604a2a33b66bb32c9b，原书字符 [49265,49397)

$$
\frac {\mathrm {d} Q}{\mathrm {d} t} + \frac {\mathrm {d} W}{\mathrm {d} t} = \frac {\mathrm {d} E}{\mathrm {d} t} \tag {2.43}
$$

[2.4 Thermodynamics] parent_d5a9c5d1db89e7b5d098，原书字符 [49399,49401)

or

人工答复：留空；引用支持判断的原始/补充证据 ID，并记录查看过 AI 预标及实际时间。


## PILOT-020

原候选：second term on the RHS → tells us how much material has left → CV

核查问题：已补回同一原文段落及式 2.129。请确认右端第二项的身份、原文所指物理量和该候选应保留的局部范围。

AI 预标（待复核）：{"source_support": "supported", "expression_status": "unresolved", "mapping_status": "unassigned", "schema_coverage": "not_covered", "knowledge_scope": "local_definition", "admission_preference": "unresolved"}

AI 理由：原文确实说右端第二项表示离开控制体的量，所以局部来源支持成立。但该句没有给出所属方程；另段的2.129及后续方程之间标有可能省略，不能据相邻顺序确认同一公式。当前 second term 身份及其精确含义待核。共享本体没有公式项表示物理量的通用关系。

原始引用：

> The second term on the RHS tells us how much material has left the CV, while the third term tells us how much material has entered.

补充原文及精确位置：

[2.7 Fluid Mechanics] parent_11228ae23013752eab56，原书字符 [122597,122614)

then we can write

[2.7 Fluid Mechanics] parent_e89b3adef4e366cadd1a，原书字符 [122616,122908)

$$
\frac {\mathrm {d} N}{\mathrm {d} t} = \frac {\partial}{\partial t} \int_ {\text {C V}} \eta \rho \mathrm {d V} + \int_ {\text {o u t f l o w a r e a}} \eta \rho \mathbf {V} \cdot \mathrm {d A} + \int_ {\text {i n f l o w a r e a}} \eta \rho \mathbf {V} \cdot \mathrm {d A} \tag {2.129}
$$

[2.7 Fluid Mechanics] parent_2aab1de460e72f2c163d，原书字符 [122910,123424)

This equation defines how a property of interest is transported into and out of the CV. If we look at each of the terms, we see that this is an intuitively satisfying equation. The term on the LHS is the time rate of change (decrease) of any property of the CV over a time of interest. The first term on the RHS tells us how much of that property is stored in the CV over this time. The second term on the RHS tells us how much material has left the CV, while the third term tells us how much material has entered.

[2.7 Fluid Mechanics] parent_480072477bacbb47bd07，原书字符 [123426,124039)

Now wait a minute! If we look at the signs on the second and third terms, they seem to be incorrect—should not the stuff leaving have a negative sign and the stuff entering have a positive sign? The answer to this is yes, but Equation 2.129 is written correctly. The key to this seemingly inconsistent sign convention lies in the fact that the dot product in the second term is positive when we define the area as a vector that points outward and is normal to the surface. Similarly, the inflow term will always lead to a negative number since the velocity vector points inward and the area vector points outward.

人工答复：留空；引用支持判断的原始/补充证据 ID，并记录查看过 AI 预标及实际时间。


## PILOT-027

原候选：质量力 → 是 → 有势力

核查问题：有势质量力是何推导的前提？原候选残缺的如果流动满足是否足以约束主句？

AI 预标（待复核）：{"source_support": "insufficient", "expression_status": "needs_revision", "mapping_status": "unassigned", "schema_coverage": "not_covered", "knowledge_scope": "conditional", "admission_preference": "revise"}

AI 理由：有势质量力是沿流线积分推导伯努利方程的前提之一，而不是已证实的所有质量力性质。候选 condition=如果流动满足 没有完整 antecedent，不能约束主句，来源不足以支持当前断开的原命题。所列证据只支持条件项。共享本体没有推导前提或质量力有势性质关系。

原始引用：

> 如果流动满足：i 流动是定常的（各参量对时间的偏导数为零）；ii 质量力是有势力；iii 流体是正压性的（密度可以只表示为压力的函数）或不可压缩，则可以将欧拉运动微分方程沿着流线积分，得：

补充原文及精确位置：

[3.2.3 运动微分方程与伯努利方程] parent_5fb700dee8411bc833fc，原书字符 [155057,155073)

对理想流体，欧拉运动微分方程为：

[3.2.3 运动微分方程与伯努利方程] parent_93f110e83b1526ed3609，原书字符 [155075,155230)

$$
\frac {\partial \boldsymbol {v}}{\partial t} + (\boldsymbol {v} \cdot \nabla) \boldsymbol {v} = \boldsymbol {F} - \frac {1}{\rho} \nabla p \tag {3.7}
$$

[3.2.3 运动微分方程与伯努利方程] parent_9b852928b5a49f074a4d，原书字符 [155232,155326)

如果流动满足：i 流动是定常的（各参量对时间的偏导数为零）；ii 质量力是有势力；iii 流体是正压性的（密度可以只表示为压力的函数）或不可压缩，则可以将欧拉运动微分方程沿着流线积分，得：

[3.2.3 运动微分方程与伯努利方程] parent_d28d0cd18583f6e856b1，原书字符 [155328,155375)

$$
U + \frac {v ^ {2}}{2} + P = C \tag {3.8}
$$

[3.2.3 运动微分方程与伯努利方程] parent_3beca7e902dc328befa5，原书字符 [155377,155434)

式中  $P$  ——定义的压力函数， $P = \int \frac{\mathrm{d}p}{\rho}$ 。

人工答复：留空；引用支持判断的原始/补充证据 ID，并记录查看过 AI 预标及实际时间。


## PILOT-036

原候选：proper thermodynamics terminology → would require → use of δ'' instead of d in Equation 2.43

核查问题：请对照原页确认 δ 双撇的记号；如何保留 would require 的规范性语气及作者随后忽略该规范的说明？

AI 预标（待复核）：{"source_support": "supported", "expression_status": "faithful", "mapping_status": "unassigned", "schema_coverage": "not_covered", "knowledge_scope": "local_definition", "admission_preference": "revise"}

AI 理由：候选保留了 would require 的规范建议、路径依赖理由及式2.43局部范围。原文随后说本书忽略该要求，不等于否定规范，也不能将候选改成作者已实际使用该符号。would 的契约枚举问题与来源含义分开处理。共享本体没有记号规范/替代关系。

原始引用：

> Proper thermodynamics terminology would require us to use the  $\delta''$  instead of  $d$  in Equation 2.43 because of path dependency considerations, but for our purposes, we shall ignore this fact.

补充原文及精确位置：

[2.4 Thermodynamics] parent_d5a9c5d1db89e7b5d098，原书字符 [49399,49401)

or

[2.4 Thermodynamics] parent_30b7e46f2437d0d94242，原书字符 [49403,49451)

$$
\dot {Q} + \dot {W} = \dot {E} \tag {2.44}
$$

[2.4 Thermodynamics] parent_413277fe9565bb0deab4，原书字符 [49453,49849)

Here the dots over the heat and work terms indicate the time rate of change of the variable. Proper thermodynamics terminology would require us to use the  $\delta''$  instead of  $d$  in Equation 2.43 because of path dependency considerations, but for our purposes, we shall ignore this fact. The reader is advised to consult any thermodynamics text for a better understanding of the difference.

[2.4 Thermodynamics] parent_35c53a1bf35f118a8e31，原书字符 [49851,50661)

The substitutions that were performed to arrive at Equation 2.41 are not as straightforward in this case. Because we have material entering and leaving the CV, we can imagine that this material can enter or leave with a different pressure and density as it interacts with our fixed CV. Because of this, we must account for the energy used to make these changes. Alternatively, one can envision the material coming in at a higher pressure or density and wanting to push our imaginary CV outward, but since we fixed our CV, it cannot. The energy from this must go somewhere so it works on the fluid in and around our CV. Mathematically, this results in the energy term in Equation 2.44 having to include a  $pv$  term. This is sometimes known as flow work [10]. With this in mind, Equation 2.44 can be written as

[2.4 Thermodynamics] parent_5710139f6c2d0678c5a7，原书字符 [50663,50900)

$$
\dot {Q} + \dot {W} = \dot {m} _ {\text {o u t}} \left(e _ {\text {o u t}} + p _ {\text {o u t}} v _ {\text {o u t}}\right) - \dot {m} _ {\text {i n}} \left(e _ {\text {i n}} + p _ {\text {i n}} v _ {\text {i n}}\right) \tag {2.45}
$$

人工答复：留空；引用支持判断的原始/补充证据 ID，并记录查看过 AI 预标及实际时间。
