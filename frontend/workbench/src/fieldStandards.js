export const meaningEffectLabels = { preserved: '原意保留', changed: '存在语义改变', unresolved: '语义仍需核验' };
export const fieldNames = { applicability: '适用范围', quantifier: '数量限定', frequency: '频率', condition: '条件', modality: '模态', surface_relation: '关系表达' };
export function ruleTitle(spec, id) { return spec?.rules?.find(rule => rule.id === id)?.title || id || '旧版未标注规则'; }
export function meaningEffectLabel(value) { return meaningEffectLabels[value] || '旧版未区分语义影响'; }
export function normalizationLabel(plan) {
  if (plan.operation === 'move_frequency') return `${fieldNames[plan.source_field] || plan.source_field} → 频率，原样保留“${plan.value}”`;
  if (plan.operation === 'remove_duplicate_frequency') return `清除关系边界重复的“${plan.value}”，独立频率仍保留`;
  return '需要核对的规范化操作';
}
