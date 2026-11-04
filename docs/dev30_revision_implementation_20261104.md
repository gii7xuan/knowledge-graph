# 30条开发样本整改：代码实现与验收

日期：2026-11-04。

依据用户提供的 `kg_dev30_revision_plan_20261104.md` 实施通用代码改动。方案中的旧人工统计不覆盖已经确认的007、011标注更正、022主语修订、017定义复用和028/029范围取舍；生产逻辑没有按这些样本编号或实体写特例。

## 本轮实现

| 部分 | 实际行为 | 主要代码 |
|---|---|---|
| 证据单元 | 从冻结原文构建段落、完整公式、图注、表格、符号解释及链接等单元。纯边界、单独感叹号、纯链接和公式碎片保留在档案，不能独立充当支持。公式补全及必要上下文生成新的真实坐标引用，不替换旧ID文本。 | `evidence/units.py`、`document/hierarchy_index.py`、`evidence/verification.py` |
| 主证据与全包 | V5委员明确提名支持完整表述的主证据组合；工作台显示组合、反证、正文和冻结包哈希。展开时读取模型实际审核的包，不把临时扩展上下文混入旧包。旧票没有完整主证据提名时显示“待核验主证据”，并展示已有正文。 | `review_agent/evidence_view.py`、`service.py`、`TripletReview.vue`、`evaluation/review_samples.py` |
| 人工标注 | 保存raw_annotation及独立的来源、表达、映射、覆盖、范围、收录意见。绑定候选内容、revision、proposal_digest、evidence_hash、本体版本和profile。缺正式ID、缺覆盖证明、反驳缺依据、模型/人工冲突等形成待仲裁项。 | `annotation_contract.py`、`annotation_store.py`、`feedback.py`、`labels.py` |
| 可训练标签 | 已解决且有标注人、时间、是否看过模型结论、完整包核验记录的分项标签才可导出。缺证据、纯格式引用和未绑定的新修订稿不能用文字豁免进入金标。修改后表述与原候选标签分开。待仲裁反馈不进入校准行或审核记忆。 | `annotation_store.py`、`evaluation/annotations.py`、`repository.py`、`memory.py` |
| 候选表达 | semantic_context保留原始提及、规范实体、指称范围、关系等价说明、限定种类及attachment、局部知识范围。支持entity/literal/event/proposition分类；后两类保留为来源候选，进入建模修订，不直接进图。非实体尾部不继承实体类提示。 | `extract_triplets/semantic_context.py`、`candidate_contract.py`、`revision_patch.py` |
| 修订 | 新草案独立审核完整前后表述及未改字段，不能以泛化谓词丢失具体属性。局部语义补丁保留另一端实体和原限定。原始提及保留为来源记录，新版本重新审核、绑定身份和票据。旧草案仍使用冻结旧提示。 | `revision_service.py`、`revision_patch.py`、`contracts.py` |
| 本体映射 | ID/标签/别名只做召回。向委员提供完整属性目录、定义、属性种类、端点类型及继承路径。缺定义、缺ID或端点不明保留未确定；约束冲突展示requested类型、allowed范围及路径。 | `schema/relation_registry.py`、`ontology_guard.py`、`tools.py`、`validators.py` |
| 审核契约 | 九项独立检查：核心支持、实体指称、关系语义、方向、限定、证据充分性、映射、知识范围、反证。每项有issue_code、证据和解释。来源支持与表达须修订可同时成立，不能聚合成完整通过。缺引用及反证角色矛盾由原席位补正，保留历史意见。 | `response_contract_v5.py`、`consensus.py`、`planner_view.py`、`recovery_service.py` |
| 准入分流 | 来源、表达、映射及范围分别处理。经确认的局部符号约定、示例、推导可按项目政策排除，保留来源判断。自动、人工和图谱重新验证均检查绑定的范围/表达记录；已在等待本体任务的候选不会被映射分流覆盖等待状态。 | `scope_policy.py`、`orchestrator.py`、`admission.py`、`admission_repository.py`、`human_evidence.py` |
| 实验隔离 | 增加development分区和开发参考集入口。训练记忆与开发/测试之间检查同一候选修订、规范事实、近重复表述、重叠证据；执行和评价报告明确标注分区。补齐隔离实验同步完成队列任务时的精确租约绑定。 | `evaluation/split_integrity.py`、`experiment.py`、`evaluate_experiment.py`、`memory_scope.py` |

## 版本与恢复

- 新审核默认使用 `validator_response_v5`，提示版本为 `triple_validator_v5_semantic_units_v1`；固定三席、三票和三个模型族的门槛保持原配置。
- 新抽取使用 `extraction_v5.txt`，来源候选外层仍兼容 `extraction_candidate_v4`。含semantic_context的事实使用 `fact_identity_v3`；局部或未明范围不合并，确认的一般事实仍能共享规范身份而保留多个来源。
- 证据单元覆盖层使用 `evidence_bundle_v3`，新增包有新哈希。原文、旧索引、旧证据ID/引文和原包哈希保留。
- 旧v3/v4审核提示、契约、请求键及恢复分派保留。新增修订提示仅用于带 `full_expression_v2` 标记的新草案；旧草案保持旧提示和请求身份。
- 当前已确认失败、格式无效和请求结果未知仍是不同状态；没有放宽未知请求重投、真实反对意见、发布或准入门槛。

## 30条离线核对

回放结果在 [offline_replay.json](../outputs/dev30_code_revision_20261104/offline_replay.json)。全部30条保留，5条纯 `$$` 首页引文均改为正文/完整包可见的展示。

原90张返回没有V5的完整主证据组合提名，因此30条旧记录的“主证据”均显示待核验；这不是新增30条来源反驳，也不改变冻结来源分布20 supported / 10 unresolved。新增完整公式和上下文有新证据包哈希，不能复用旧票声称已经审核新包。

原填写Excel SHA-256仍为 `99fa28fb1efc37ccbe1e1193bd0f3fae01e2a473a054cdcb0e86bf250da63e9e`。30条生产候选内容与冻结绑定一致；离线检查未调用真实模型或写业务状态。

已读取实际冻结本体，确认189个类、81个对象属性、0个数据属性；81个关系在适配后的完整属性目录中均无定义。该现象不只是参考表漏列。代码会保留未确定映射，并支持提出 `revise_definition` 审核建议；没有自动猜写定义或改旧release。补定义、例子及新数据属性仍需真实本体审核和版本发布。

## 验收覆盖

通用回归位于 [test_dev_semantic_revision.py](../tests/test_dev_semantic_revision.py)，包含方案T01–T18及真实仓储、固定委员会、标注导入、统一准入的模拟端到端检查。测试不用开发样本实体或编号决定生产行为。

| 用例 | 覆盖内容 |
|---|---|
| T01–T03 | 纯标记/链接不能支持、完整公式保留、主证据组合与反证显示、扩展产生新包哈希、旧ID引文不变 |
| T04–T06 | 原标签冲突待仲裁、无正式ID不能确认映射、证据不足不转为事实反驳 |
| T07–T11 | 原始实体提及保留、谓词编辑不倒置端点、完整表述独立审核、分支限定保留、未知极性不默认肯定 |
| T12–T14 | 同名关系及缺定义处理、约束与继承路径展示、范围排除保留来源判断 |
| T15–T18 | 新版本绑定、待仲裁不进训练、生产无样本特例、版本记录及跨分区泄漏检查 |

最终测试、前端构建及复测摘要在 [verification_summary.json](../outputs/dev30_code_revision_20261104/verification_summary.json)，原始日志保存在 `runs/dev30_code_revision_20261104`。旧回归中依赖“词面相同即可直接取得规范事实身份”的断言已改为先保留未映射候选、确认一般范围及正式映射后再验证合并；没有通过恢复错误自动映射来满足旧断言。

最终64项复测全部通过、无跳过；此前134项受影响模块回归通过，其中22项按既有条件跳过。前端9项测试及最终生产构建通过，Python编译与差异空白检查通过。测试模型均为本地模拟，本轮没有真实模型调用。

## 使用入口

- 工作台：新的多维标注面板；API为 `POST /api/triplets/annotations`，单页服务对应 `POST /api/annotations`。该入口单独保存标注，不自动改变候选或准入。
- 导出某一维度的已仲裁标签：`python -m evaluation.annotations --database <db> --dimension source_support --output <new.json>`。其他维度分别导出，不强行拼成统一真假标签。
- 导出共享冻结包的开发样本：`python -m evaluation.review_samples --database <db> --candidate-ids <ids...> --split-role development --output <new_directory>`，生成JSON全包及CSV入口，包含全部属性种类和定义/别名字段。
- 独立实验：`evaluation_partition` 显式选择development或test；测试时通过 `--development-candidates` 提供已用于调试的候选参考，检查事实/版本/证据泄漏。

本轮验收范围是代码、本地模拟回归与30条离线回放。真实模型按V5契约的完成率、误收漏收和成本，以及独立测试集效果，需下一轮受控对照实验评价；不能把这些开发样本统计当作全章准确率或召回率。
