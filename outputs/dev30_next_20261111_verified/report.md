# 下一步改进：离线盘点与定向修复

已导出 30 条四维阻塞、16 条等待映射轨迹、30 份当前绑定人工模板及 2 条修订预览。
历史建议：{'wait_mapping': 16, 'manual_review': 11, 'revise': 2, 'exclude': 1}。
正式有效映射指标来自选定审核的 mapping.status=valid，而非发布/登记数；本轮全部 context_incomplete。
等待映射原因（按候选计数，可重叠）：{'definition_missing': 16, 'identity_ambiguous': 8}。
映射里程碑：{'proposal_formed': {'yes': 16}, 'candidate_id_assigned': {'no': 9, 'yes': 7}, 'structural_constraints_passed': {'no': 9, 'yes': 7}, 'full_guard_passed': {'no': 16}, 'human_confirmed': {'unknown': 16}, 'formally_registered': {'unknown': 16}}。
已执行局部和全绑定本体检索，主要缺口须核对正式发布版定义和实体身份；缺定义不代表不存在等价关系，不补造定义或强行替换。

|反事实窗口/秒|完整提案|
|---|---|
|90|25/30|
|120|26/30|
|150|27/30|
|180|27/30|

计时按持久请求开始至结果入账；同时保留worker、首内容和队列时点。模拟不改写90秒历史结果，不预测真实吞吐。
用量分组：{'committee:initial': {'requests': 90, 'input_tokens': 1731797, 'output_tokens': 106160, 'usage_missing': 0, 'latency_ms_sum': 3686248, 'receipt_statuses': {'ok': 79, 'invalid_contract': 10, 'invalid_reference': 1}}, 'committee:field_patch': {'requests': 9, 'input_tokens': 38395, 'output_tokens': 6463, 'usage_missing': 0, 'latency_ms_sum': 214012, 'receipt_statuses': {'ok': 8, 'invalid_contract': 1}}, 'planner': {'requests': 91, 'input_tokens': 1525505, 'output_tokens': 17385, 'usage_missing': 20, 'latency_ms_sum': 2989357, 'receipt_statuses': {'ok': 70, 'api_error': 20, 'invalid_action': 1}}}。已报告token合计 3425705，含缺失用量，不能作为完整总量；无价格表不估金额。

标注顺序：先阅读 annotation_packets/ 中候选、原文位置、完整证据、本体、范围及字段规则并独立判断；随后再看 model_suggestions_after_annotation.json 和旧标签。
annotation_current.jsonl 每维可判定性、缺材料、阻塞类型及沿用依据均待人工填写。旧5条未改绑；当前有效人工标签0，事实/处置质量不可评估。
两条修订保存修改前后、字段差异、语义风险和预览哈希；未独立批准及新版本复核，状态为执行未完成，不计修订成功。

代码：新轮提示v4明确规则ID/计划ID、反证pass和nullable映射字段；补丁v2对大本体按无损列式编码，语义澄清保留完整证据并去掉候选重复包装；预算上限不变。历史v3提示及v1补丁仍按原哈希读取。
新提示和补丁尚未经过真实模型复验；本次新模型调用0、生产写入0。先完成人工核验和有界真实定向验证，再冻结配置全量复跑；这30条仍为开发集。

## 本次真实定向验证与修订准备

用户明确授权后，对DEV-019/022/024仅运行Gemma的新隔离轮次，共4次请求（3次初审、1次语义澄清），低于6次上限；所有请求已知终态。DEV-024首轮契约合格，正确保持unassigned/null/[]。DEV-019仍为unresolved finding与fail维度不一致；DEV-022仍有反证角色引用重叠，澄清返回空的可见内容，两条继续无效。不拼接旧票，不把单席合格视为完整审核或事实正确。细节见targeted_live_validation.json及独立原库。

真实澄清另暴露观测缺陷：结果沿用了初审传输元数据。后续代码已单列原始/澄清元数据并明确空响应与截断终态，47项补充回归通过；没有再次调用模型验证这项观测修复。此前两组87+81项回归均通过。

两条revise已在独立数据库副本通过既有RevisionService形成proposed草案，详见revision_validation.jsonl中的isolated_draft。DEV-017待核对关系语义；DEV-020仅清理重复频率词并保留原quantifier。尚无独立批准、新版本重审或人工核验，不报告修订成功。

完整30条回归、5–8例重复稳定性与新文档评估尚未启动：当前人工标签、正式定义和修订核验仍未完成。生产写入0；旧标签、候选和历史结果保护验证通过。

当前发布版的3个代表案例DEV-001/008/024已重新执行OntologyGuard（零模型调用），结果及绑定保存在mapping_representative_validation.json；仍因正式定义/映射材料不足而未形成有效映射，未制造成功数。
