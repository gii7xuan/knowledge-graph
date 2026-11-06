// Validate explicit human input; never infer evidence roles or approval.
const roles = new Set(['support', 'counter', 'context', 'irrelevant']);
const qualifierKeys = ['polarity', 'condition', 'time_scope', 'applicability', 'modality', 'quantifier'];
const normalized = value => value == null || value === '' ? null : value;
const splitIds = value => [...new Set(String(value || '').split(/[,，\s]+/).filter(Boolean))];
const tripleFor = item => item.triple || item;
export const requiresScopeReview = item => Object.keys(tripleFor(item).semantic_context || {}).length > 0;

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])]));
  return value;
}

function hasUnsavedChanges(item, draft) {
  const triple = tripleFor(item);
  for (const key of ['head', 'tail', 'head_type', 'tail_type']) {
    if (key in draft && normalized(draft[key]) !== normalized(triple[key])) return true;
  }
  if ('relation' in draft && draft.relation !== (draft.original_relation ?? triple.relation_label ?? triple.surface_relation ?? triple.relation_id ?? '')) return true;
  if ('relation_id' in draft && normalized(draft.relation_id) !== normalized(triple.relation_id)) return true;
  if (item.contract_version !== 'extraction_candidate_v4') return false;
  for (const key of ['tail_kind', 'value', 'datatype', 'unit', 'language']) {
    if (key in draft && normalized(draft[key]) !== normalized(triple[key])) return true;
  }
  for (const key of qualifierKeys) {
    if (draft.qualifiers && normalized(draft.qualifiers[key]) !== normalized(triple.qualifiers?.[key])) return true;
    if (draft.qualifier_status && normalized(draft.qualifier_status[key]) !== normalized(triple.qualifier_status?.[key])) return true;
  }
  if (draft.semantic_context_json) {
    try {
      if (JSON.stringify(stable(JSON.parse(draft.semantic_context_json))) !== JSON.stringify(stable(triple.semantic_context))) return true;
    } catch { return true; }
  }
  return Boolean(triple.semantic_context && !draft.semantic_context_json && 'semantic_context_json' in draft);
}

export function prepareAcceptance(item, draft) {
  const issues = [];
  const add = (field, message) => issues.push({ field, message });
  const audit = item.audit_summary;
  const materialCounters = new Set((audit?.votes || []).filter(vote => vote.response_status === 'ok')
    .flatMap(vote => vote.counter_evidence_ids || []));
  let bundleRequest;
  if (hasUnsavedChanges(item, draft)) add('candidate', '您修改了候选内容，请先“保存修改并复审”，再通过新版本。');
  if (audit?.issue_history?.some(issue => issue.requires_revision && issue.status !== 'resolved')) {
    add('candidate', '当前候选仍有必须修订的问题，请先“保存修改并复审”。');
  }
  if (draft.evidence_mode === 'proposal') {
    if (audit?.status !== 'completed' || !audit.audit_id || !audit.selected_round_id ||
        !audit.selected_proposal_digest || !audit.selected_evidence_hash) {
      add('mode', '当前审核提案或版本信息不完整，请刷新后重新核对，或从规范原文选择引文。');
    }
    const catalog = new Set((audit?.evidence_bundle?.items || []).map(item => item.evidence_id));
    const selected = Object.entries(draft.selections || {}).filter(([, value]) => value.selected);
    const ids = new Set(selected.map(([id]) => id));
    const supports = new Set(selected.filter(([, value]) => value.role === 'support').map(([id]) => id));
    if (!selected.length) add('evidence', '请至少勾选一条当前审核提案的原文证据，设为“支持”，并填写判断依据。');
    else if (!supports.size) add('role:' + selected[0][0], '已勾选的证据中，至少需要一条明确标为“支持”。');
    for (const [id, selection] of selected) {
      if (!catalog.has(id)) add('mode', '所选证据不属于当前审核提案，请刷新后重新核对：' + id);
      if (!roles.has(selection.role)) add('role:' + id, '请选择证据的判断角色：' + id);
      if (!String(selection.note || '').trim()) add('note:' + id, '请填写这条证据如何支持或限定候选：' + id);
      const refs = splitIds(selection.resolutions);
      if (refs.some(ref => !ids.has(ref))) add('resolution:' + id, '反证处置引用的 ID 必须属于已勾选的本轮证据：' + id);
      if (selection.role === 'counter') add('role:' + id, '仍有标为“反驳”的证据，请先解决分歧、修订或暂缓，不能直接通过。');
      if (materialCounters.has(id) && (selection.role === 'counter' || !refs.some(ref => supports.has(ref)))) {
        add('resolution:' + id, '这条模型反证需要说明处置依据，并引用已勾选的支持证据 ID：' + id);
      }
    }
    for (const id of materialCounters) if (!ids.has(id)) add('evidence', '请保留并勾选模型提出的反证，说明其处置依据：' + id);
    bundleRequest = {
      audit_id: audit?.audit_id, selected_round_id: audit?.selected_round_id,
      selected_proposal_digest: audit?.selected_proposal_digest, selected_evidence_hash: audit?.selected_evidence_hash,
      evidence_ids: selected.map(([id]) => id),
      evidence_assessments: selected.map(([id, selection]) => ({
        evidence_id: id, role: selection.role, note: String(selection.note || '').trim(),
        resolution_evidence_ids: splitIds(selection.resolutions)
      }))
    };
  } else {
    const spans = draft.spans || [];
    if (!spans.length || spans.length > 24) add('spans', '请提供1至24段规范原文引文。');
    if (materialCounters.size) add('mode', '当前审核包含模型反证，请使用原审核提案的证据保留并处理反证；审核未结束时请先完成原审核或修订。');
    spans.forEach((span, index) => {
      if (!String(span.quote || '').trim()) add('quote:' + index, '请填写第' + (index + 1) + '段完整原文引文，或移除空白条目。');
      if (!Number.isInteger(span.occurrence) || span.occurrence < 1) add('occurrence:' + index, '引文出现次数必须是从1开始的整数。');
      if (!roles.has(span.role)) add('span-role:' + index, '请选择第' + (index + 1) + '段引文的判断角色。');
      if (span.role === 'counter') add('span-role:' + index, '存在未解决的反驳引文，请先处理分歧、修订或暂缓。');
      if (!String(span.note || '').trim()) add('span-note:' + index, '请填写第' + (index + 1) + '段引文的判断依据。');
    });
    if (spans.length && !spans.some(span => span.role === 'support')) add('spans', '至少需要一段明确标为“支持”的原文引文。');
    bundleRequest = { spans: spans.map(span => ({ ...span, note: String(span.note || '').trim() })) };
  }
  if (requiresScopeReview(item)) {
    if (!draft.annotation?.knowledge_scope || draft.annotation.knowledge_scope === 'unresolved') add('scope', '请确认这条知识的收录范围。');
    if (draft.annotation?.expression_status !== 'faithful') add('expression', '通过前需确认表述“完整忠实”；需要修订时请先保存修改并复审。');
    if (!String(draft.annotation_note || '').trim()) add('scope-note', '请填写知识范围与表述完整性的原文判断依据。');
    bundleRequest.scope_review = { knowledge_scope: draft.annotation?.knowledge_scope,
      expression_status: draft.annotation?.expression_status, note: String(draft.annotation_note || '').trim() };
  }
  return { ok: !issues.length, issues, bundleRequest };
}

export function acceptanceErrorMessage(error) {
  const text = error?.message || '提交失败，请稍后重试。';
  if (error?.status === 409) return '候选或审核版本已更新，请刷新并重新核对证据后再提交。';
  const messages = [
    ['select nonempty evidence IDs', '请至少勾选一条当前审核提案的证据，标为“支持”并填写依据。'],
    ['material counterevidence must remain', '模型提出的反证不能省略，请勾选并填写处置依据。'],
    ['candidate expression issues require', '候选表述仍需修订，请先“保存修改并复审”。'],
    ['manual assessment requires', '每条已选择的证据都需要明确角色和非空判断依据。'],
    ['counterevidence requires', '请说明反证的范围或适用条件，并引用本轮已选择的支持证据 ID。'],
    ['counter resolution references', '反证处置引用的 ID 必须属于本轮已选择的证据。'],
    ['quote is absent/ambiguous', '引文未在规范原文中找到，或出现多次。请核对原文并指定第几次出现。'],
    ['quote must be an exact nonempty', '请填写规范原文中的完整引文。'],
    ['provide 1..24 explicit source spans', '请提供1至24段规范原文引文。'],
    ['manual selection requires a completed', '当前审核尚未结束，请等待原审核完成或从规范原文选择引文。'],
    ['use the exact audited proposal', '存在原审核的反证，请使用对应审核提案的证据进行处置。'],
    ['scope review requires', '请补齐知识范围、表述完整性和原文判断依据。'],
    ['SOURCE_SEMANTIC_SUPPORT_MISSING', '缺少人工确认的支持证据，请至少选择一条支持证据并填写依据。'],
    ['FAITHFUL_EXPRESSION_CONFIRMATION_REQUIRED', '请确认候选表述完整忠实；不完整时请先修订。'],
    ['KNOWLEDGE_SCOPE_CONFIRMATION_REQUIRED', '请确认知识范围并填写判断依据。'],
    ['EXCLUDED_BY_SCOPE', '该知识范围不在当前收录范围内，请核对收录意见。']
  ];
  return messages.find(([key]) => text.includes(key))?.[1] || text;
}
