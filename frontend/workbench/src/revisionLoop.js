export const changeKinds = { semantic_change: '含义或指称修改，须完整复核', mapping_change: '映射修改，须重新核验', lossless_duplicate_cleanup: '精确重复清理，核验保留依据' };
export const facetNames = { relation: '关系', entities: '实体与值', negation: '否定', frequency: '频率与量化', condition: '条件与时间', modality: '模态', meaning_scope: '语义附着与范围', formal_mapping: '本体映射' };
export const routeNames = { apply_field_spec: '依据统一字段规范处理', supplement_mapping_information: '补齐关系定义与实体身份', supplement_source_context: '补充原文后定向复核', human_scope_review: '人工确认收录范围' };
export function displayValue(value) { return value == null ? '未填写' : typeof value === 'object' ? JSON.stringify(value) : String(value); }
export function focusStatus(value) { return ({ open: '待补充材料', human_review: '转人工处理', review_incomplete: '定向复核未完成', resolved_supports: '该争议已核实支持', resolved_opposes: '该争议已核实不支持', normalization_pending: '待采用规范化结果', started: '定向复核中' })[value] || value; }
export function observationLabel(value) { return ({ found: '已找到证据', core_only: '仅核心关系有证据', unresolved: '证据与结论待核验', not_recorded: '未记录支持证据', reviewed_no_material_counter_recorded: '已检查当前材料，未记录实质反证', incomplete: '检查未完成' })[value] || value; }
export function effectLabel(value) { return ({ changed: '存在语义改变', preserved: '未报告语义改变', unresolved: '语义仍待核验', legacy_unclassified: '旧记录未独立说明', not_source_judgment: '不单独判断来源事实' })[value] || value; }
