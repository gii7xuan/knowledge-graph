# 正式关系定义处理

入口：`runs/formal_definitions_20261111.py`；精确增补内容：`specs/formal_definition_candidates_20261111.json`。本轮以11月11日全30条封存材料和其原本体发布版本为基线，不使用AI标注作为独立语义批准。

## 实际完成

当前发布版本有189类、81个关系，关系定义正文覆盖0/81；30条候选实际分配了12个关系ID。已为其中8个既有ID起草并验证来源绑定定义，覆盖DEV-002、004、007、013、014、015、024、025。

新副本中仅增加8条`skos:definition`，RDF从882条变为890条，删除0条；ID、标签、domain/range、别名和类层级不变。已重新执行8个对应候选的显式映射/完整定义guard，均pass，并对全部30条保存前后影响。DEV-028的真实range冲突继续fail。局部guard的pass只证明元数据与具名约束可检查，不代表正式语义映射通过；旧候选仍绑定旧发布版本。

## 真实独立审核

用户明确允许将8项定义、相关证据、完整冻结章节和本体发送到现有Qwen/Gemma网关与DeepSeek，最多6次调用，并仅在两项审核通过后生成隔离版本。本轮实际5次调用：3次初审、2次已知字段错误恢复。

|席位|原回执|恢复后当前结果|
|---|---|---|
|Qwen|超过90秒窗口，实际回执晚到且两处可选evidence_scope非法|一次恢复请求后有效approve，晚到结果已离线对账|
|Gemma|HTTP504，原请求实际结果未知|保留未知请求围栏，未重发|
|DeepSeek|一个证据ID重复字符，引用不合法|一次恢复请求后有效approve|

原始票和请求轨迹保留。恢复使用现有`ontology_review.recovery.recover_frozen_ontology`；新响应不是由程序删除错误字段或猜ID生成的派生票。Qwen层级检查从not_applicable变为pass，其余两席检查状态/总判断/反证集合不变，差异另存。三席三家族门槛、来源校验和原`chapter1_source_coverage`能力问题均未放宽。

目前有2张有效建模批准票，仍缺Gemma确定结果。本批完整章节/本体的能力审核输入已冻结，但未执行，不能把准备、结构检查或2票批准当作正式定义发布完成。未生成新的已批准本体发布版本，生产发布头和旧数据均未变化。

## 另外4项

- DEV-008：复用现有横风机制定义草案`ext_4272d42b83144e8c8da86d3530f76019`，保留其原未知请求及分歧，不以新批次重投规避。
- DEV-019：补出冻结原文中的地炮/常常/顶点及落点诸元句；需澄清计算需求与顶点诸元的正式指称，未猜写或发布定义。
- DEV-022：投影输入与结果混用，需要明确候选/类定义/domain之间应修改的对象，单补注释不能消除建模疑问。
- DEV-028：现有“考虑”关系指向科氏加速度，当前尾项为地面曲率；已有正确科氏项原文，仍禁止通过加定义或追加range来掩盖冲突。

## 文件与复现

完整报告和原始回执在`outputs/formal_definitions_20261111_ready/`：

- `report.md`、`final_summary.json`：实际完成范围、审核结果及剩余阻塞。
- `prepared_proposal.json`、`prepared_competency_request.json`：精确冻结输入。
- `UNAPPROVED_ontology.ttl`、`owl_patch.json`：8项定义的未批准OWL制品及精确补丁。
- `guard_effect_all30.jsonl`、`offline_verification.json`：RDF重建哈希、约束保持和全部候选影响。
- `new_request_ledger.json`、`recovery_judgment_diff.json`：5次调用及恢复判断差异。
- `held_relation_dossiers.json`：另外4项的原文、约束与具体处理问题。
- `gateway_reconciliation_required.json`：Gemma原请求的时间、请求身份及对账要求；本地request_key不是服务商请求ID。

```powershell
# 只准备，不调用模型；必须使用全新目录
& 'D:\miniconda3\envs\knowledge\python.exe' -X utf8 -B -m runs.formal_definitions_20261111 --output outputs/<全新定义目录>

# 首次执行已审阅的冻结输入；已存在execution_started.json的目录会拒绝重启
& 'D:\miniconda3\envs\knowledge\python.exe' -X utf8 -B -m runs.formal_definitions_20261111 --output outputs/<已准备目录> --execute
```

本轮已经执行的目录不能重新启动。下一步先对账Gemma原请求：若形成有效批准，再补齐原三席整章能力检查；若存在有效反对或修订，应处理具体内容后创建实质改变的新版本。正式发布仍保持阻断，不能自动重发结果未知请求。
