# 按 40 条 AI 预标推进的后续工作（2026-11-18）

已核对用户提供的《人工试标_AI预标_40条.xlsx》，在独立目录保存 AI 预标记录、问题队列、原书补证、本体材料和待复核修订草案。输入表格、160 条冻结候选、原试标包与生产本体均未修改。本轮新增模型调用为 0。

## 预标内容与使用边界

40 条样本编号、候选内容、版本哈希、证据文字及引用均与冻结试标包一致，六项标签均已填写。审核人栏统一注明“ChatGPT（AI预标，待人工复核）”；实际人工用时为空。

以下是 **AI 预标分布**，不是准确率或人工结论：

| 项目 | 标记分布 |
|---|---|
| 来源支持 | supported 35，insufficient 3，unresolved 2 |
| 表达质量 | faithful 24，needs_revision 14，unresolved 2 |
| 已有映射 | unassigned 40 |
| 本体覆盖 | not_covered 38，unresolved 2 |
| 操作偏好 | include 18，revise 18，unresolved 3，exclude 1 |

记录明确设置 `label_source=ai_preannotation`、人工金标准数 0，禁止作为本轮可靠性训练、校准或论文评分标签。原始标记与理由完整保留，没有将 `include` 转成研究 accept：这 18 条仍未满足正式映射等准入条件。原表“已核对完整材料”是 AI 的声明，不能作为人类审核记录；人工时间也没有填成零。此前生成预标的用量和费用未知，不因本轮零调用就记成零成本。

## 已推进的输入修复

### 1. 原书补证

为 40 条建立了 168 项原书段落补充材料，每项保留原书版本、字符范围、逐字文本和独立补证 ID。使用同一来源小节的有界上下文，并显式保留省略及超范围过滤，未把省略后的段落当作连续论证。

- **PILOT-020**：从原书连续位置补到式 **2.129** 及紧随其后的 “This equation…”、右端第二项和第三项的解释。该定位来自原书，而不是依据预标中提到的公式号猜测。
- **PILOT-004**：补到式 **3.9**、局部适用条件及实际流体能量损失的上下文。
- **PILOT-011、036**：Markdown 中的 `k_{circ}`、`δ''` 保持原样。没有本地原页可证明 OCR 符号含义，仍需原页核查。

补证是新材料，没有覆盖原证据包或旧票；要用于后续审核，须建立绑定这些材料的新输入/轮次。当前预标不能因此追溯变成“已经基于补证作出的判断”。

### 2. 本体约束材料

整理了完整 48 个关系、189 个类，以及预标明确引用的正式 ID、定义、域和值域类释义。

PILOT-015、034 引用的 `R_5d7591372f1859dc86e0`，名称为“引起”，文字定义较泛，但正式域 `C_a902e72f8e9d12ecd166` 是 **横风**，值域 `C_09cdf04291934cb9c598` 是 **侧偏**。它不能仅凭“因果”这一表面语义直接绑定到分子碰撞和压力命题。材料中保留了这个约束冲突，也没有将它升级为“其他所有关系均已证明不覆盖”。

40 条本体覆盖工作单已准备好，要求分别核查正式关系可复用、定义/类型材料不足、真正缺少属性等情况。如需共享扩充，应基于来源起草定义与类型约束，并对所有实验组使用同一冻结版本。当前未赋任何正式 ID，也未发布本体。

### 3. 占位符技术修订

新增 `explicit_not_stated_wire_v1`：只有**原始抽取值精确为字符串 `not_stated` 且同一字段的原始状态也为 `not_stated`** 时，才把值投影为 JSON null；原始状态、引用与其他判断保持不变。保留原始/投影哈希、政策哈希和逐字段路径。

此策略显式选择后才启用，历史默认处理没有改变。它不补肯定极性，不把 can 改为 asserted，不解释 would，不把 unknown 自动改成缺省值，也不执行 AI 给出的自由文本修改建议。

从 **全部 160 条开发候选**对应的原始成功抽取回执中，找到 18 条满足该严格条件的候选，生成 18 份修订草案；无匹配或建草案失败。草案存于隔离数据库，均为 `proposed`，要求独立修订复核，尚未批准或应用。原候选仍为 revision 1。现有修订流程会对新草案重置未确认映射状态，未替候选增加映射。

18 份草案在本次 40 条试标中对应 **PILOT-003、018、039**，其余 15 条属于同一开发池的未试标候选。它们与 AI 标记的 18 条 revise **不是同一集合**，也不表示 18 条语义修复已经成功。can 等有效模态与主谓宾均保留；其余语义问题继续待复核。

## 下一轮的具体工作

已生成七项优先争议核查单：PILOT-001、002、011、017、020、027、036，分别涉及解释口吻与证据张力、施事/篇章范围、OCR、缺失公式身份、推导条件及规范性语气。其余问题也在完整队列中保存。

核查单显示 AI 标记和新增材料，属于辅助复核，不可声称为盲标。原来的空白独立试标包保留；若需要独立金标准，应让未看过预标的审核人使用原包，并分别记录后续新材料和人工裁决。对已经阅读预标的审核，记录 `saw_ai_preannotation=true` 及实际时间。

接下来先处理这七项裁决和共享本体准备，再复核技术修订草案；取得合适的人工标签与可比较输入后运行五组方法及权重对照。当前不计算论文效果、训练模型可靠性或将本轮处理结果当作已入库事实。

## 验证和材料

16 项针对性回归通过，覆盖严格占位符条件、原文/模态保持、篡改拒绝、历史默认行为、AI 纳入偏好不转准入，以及已有标注/抽样逻辑。另逐项核验：18 份草案对应原始回执、全部补证逐字定位正确、160 条当前候选未改、人工反馈/审核票/新增 API 请求/应用后的版本/jobs/outbox 均为零。输入文件和源数据库、生产本体 head 哈希未变。

- [优先争议核查单](D:/knowledge-huopao/outputs/preannotation_followup_20261118/priority_review_questions.md)
- [待填写答复](D:/knowledge-huopao/outputs/preannotation_followup_20261118/priority_answers_blank.json)
- [AI 预标原始记录](D:/knowledge-huopao/outputs/preannotation_followup_20261118/ai_preannotations.json)
- [完整问题队列](D:/knowledge-huopao/outputs/preannotation_followup_20261118/review_queue.json)
- [原书补证](D:/knowledge-huopao/outputs/preannotation_followup_20261118/source_supplements.json)
- [本体与类约束材料](D:/knowledge-huopao/outputs/preannotation_followup_20261118/mapping_materials.json)
- [本体覆盖工作单](D:/knowledge-huopao/outputs/preannotation_followup_20261118/ontology_coverage_worklist.json)
- [修订草案索引](D:/knowledge-huopao/outputs/preannotation_followup_20261118/draft_index.json)
- [修订证明](D:/knowledge-huopao/outputs/preannotation_followup_20261118/wire_repair_plans.json)
- [最终核验](D:/knowledge-huopao/outputs/preannotation_followup_20261118/verification.json)
