export const dimensionLabels = {
  triple_support: '来源支持', entity_reference: '实体指称', relation_semantics: '关系语义',
  relation_direction: '关系方向', qualifiers: '限定完整性', evidence_sufficiency: '证据充分性',
  counterevidence: '反证处理', mapping: '正式映射', knowledge_scope: '知识范围'
};

export const requestLabels = {
  queued: '尚未请求', awaiting_proposal: '准备审核材料', running: '请求中', correcting: '字段补正中',
  outcome_unknown: '结果未知 · 待对账', validated: '已校验并记录',
  validated_pending_commit: '响应已校验 · 待原轮采纳', balance_paused: '余额不足 · 已暂停',
  correction_stopped: '补正已停止', invalid_response: '响应校验失败', request_failed: '请求失败',
  binding_mismatch: '版本绑定不匹配'
};

export const actionLabels = {
  inspect_context: '核对上下文', search_evidence: '检索证据', inspect_ontology: '检查本体',
  lookup_review_memory: '查阅审核案例', run_validation: '三模型独立审核', recover_validation: '恢复失败席位',
  propose_revision: '提出修订', review_revision: '审核修订', apply_reviewed_revision: '应用已审核修订',
  request_ontology_extension: '申请本体扩展', get_ontology_extension_status: '查询本体审核', finish_review: '结束本轮'
};

const labels = {
  supported: '支持', contradicted: '反驳', insufficient: '证据不足',
  pass: '通过', fail: '不通过', unknown: '待查证', not_applicable: '不适用',
  matched: '已映射', ambiguous: '有歧义', unassigned: '待分配', not_covered: '本体未覆盖',
  constraint_conflict: '约束冲突', candidate_mapping_error: '候选映射有误', suspected_schema_error: '疑似本体错误',
  general: '一般知识', conditional: '条件知识', local_definition: '局部定义', example: '示例',
  derivation: '推导步骤', unresolved: '未确定', faithful: '完整忠实', needs_revision: '需要修订',
  running: '运行中', not_started: '未开始', completed: '处理结束', interrupted: '已中断',
  ok: '已记录', empty: '未找到新证据', finished: '本轮已停止', finish_requested: '已记录结束请求',
  failed: '处理失败', timeout: '等待超时', paused: '已暂停', superseded: '已有新版本',
  tool_running: '工具执行中', planning: '规划中', ready: '准备中', validating: '独立审核中',
  manual_review: '待人工审核', revision_required: '需修订', schema_unresolved: '本体问题待处理',
  mapping_review: '待核验正式映射', scope_review: '待确认知识范围', excluded_by_scope: '按范围排除',
  review_completed: '审核完成', ready_for_review: '等待审核', auto_accept: '允许准入',
  auto_reject: '未获准入', manual: '待人工处理', accept: '允许准入', reject: '未获准入',
  awaiting_ontology_review: '等待本体审核', awaiting_ontology_release: '等待本体发布', reaudit_required: '需重新审核'
};

export function label(value) {
  if (value == null || value === '') return '尚无记录';
  return typeof value === 'string' ? labels[value] || value : '字段类型异常';
}
export function tone(value) {
  if (['fail', 'contradicted', 'constraint_conflict', 'candidate_mapping_error', 'failed', 'balance_paused', 'binding_mismatch'].includes(value)) return 'danger';
  if (['pass', 'supported', 'matched', 'validated'].includes(value)) return 'good';
  if (['unknown', 'unresolved', 'ambiguous', 'unassigned', 'outcome_unknown', 'invalid_response', 'correction_stopped', 'needs_revision'].includes(value)) return 'warning';
  return 'neutral';
}

export function formatTime(value) {
  if (value == null || value === '') return '未记录时间';
  const number = Number(value);
  const date = new Date(Number.isFinite(number) ? number * 1000 : value);
  return Number.isNaN(date.getTime()) ? '未记录时间' : date.toLocaleString('zh-CN', { hour12: false });
}

export function filterCandidates(items, filter, query) {
  const text = (query || '').trim().toLowerCase();
  return items.filter(item => {
    if (filter === 'running' && item.status !== 'running') return false;
    if (filter === 'incomplete' && item.independent_review_completed) return false;
    if (filter === 'disagreement' && !item.has_disagreement) return false;
    if (filter === 'revision' && !item.needs_revision) return false;
    return !text || [item.sample_id, item.candidate_id, item.head, item.relation, item.tail]
      .some(value => String(value || '').toLowerCase().includes(text));
  });
}

export function outcome(snapshot) {
  if (!snapshot) return { title: '尚未开始审核', detail: '选中一条候选查看已记录的执行过程。' };
  if (snapshot.historical) return { title: '历史审核记录', detail: '该记录属于旧候选版本，不作为当前版本的准入结论。' };
  const decision = snapshot.decision || {};
  if (Object.keys(decision).length) return {
    title: label(decision.workflow_state || decision.decision),
    detail: '来源支持：' + label(decision.source_support_result) + '；准入：' + label(decision.admission_consensus?.result)
  };
  if (snapshot.slots.some(slot => slot.state === 'outcome_unknown')) return {
    title: '审核未完成 · 等待对账', detail: '未知请求不计赞成或反对；已有有效意见保留。'
  };
  if (snapshot.slots.some(slot => slot.state === 'balance_paused')) return {
    title: '审核已暂停 · 余额不足', detail: '该席位未形成有效意见。'
  };
  return { title: '尚无本轮程序决定', detail: snapshot.three_seats_ready
    ? '三席已记录，等待原审核流程汇总。' : '仍有席位未形成有效记录；不提前形成准入结论。' };
}
