<script setup>
import { ruleTitle, normalizationLabel, fieldNames } from '../fieldStandards.js';
defineProps({ spec: Object, report: Object });
const shownFields = ['frequency', 'quantifier', 'applicability', 'condition', 'modality'];
</script>

<template>
  <section v-if="spec && report" class="field-standardization" aria-label="统一字段规范">
    <div class="field-heading"><h3>统一字段规范与规范化结果</h3><span>{{ spec.version }}</span></div>
    <p v-if="report.post_run_diagnostic" class="field-note">本轮结束后的独立字段诊断；没有重新发起模型审核，历史结论保持原样。</p>
    <p class="field-note">确定性结果另存于派生表达，保留原始候选。限定附着和完整语义仍需审核，不能据此直接准入。</p>
    <div class="field-expression-grid">
      <article v-for="(item, index) in [report.original_expression, report.normalized_expression]" :key="index">
        <strong>{{ index === 0 ? '原始候选表达' : '规范化表达 · 待语义核验' }}</strong>
        <p>{{ item?.head }} — {{ item?.surface_relation }} — {{ item?.tail }}</p>
        <dl><template v-for="field in shownFields" :key="field"><dt>{{ fieldNames[field] }}</dt><dd>{{ item?.qualifiers?.[field] ?? '未填写' }}</dd></template></dl>
      </article>
    </div>
    <div v-for="plan in report.plans" :key="plan.plan_id" class="field-rule-result">
      <strong>{{ plan.rule_id }} · {{ ruleTitle(spec, plan.rule_id) }}</strong>
      <p>{{ normalizationLabel(plan) }}</p><small>原文锚点：{{ plan.evidence_ids.join('、') }}</small>
    </div>
    <p v-if="!report.plans?.length">未执行可确定性完成的字段迁移或去重。</p>
    <p v-for="(review, index) in report.semantic_review" :key="index"><strong>{{ review.rule_id }} · {{ ruleTitle(spec, review.rule_id) }}</strong>：{{ review.reason }}</p>
    <details><summary>查看本版本的统一判断规则</summary>
      <p v-for="rule in spec.rules" :key="rule.id"><strong>{{ rule.id }} · {{ rule.title }}</strong>：{{ rule.rule }}</p>
    </details>
    <p class="field-note">对象配对、复合实体拆分、模态和关系修改需要逐条举证；规则名称与规范化预览均不能替代原文证据。</p>
  </section>
</template>

<style scoped>
.field-standardization { margin: 14px 0; padding: 14px; border: 1px solid #dce3eb; border-radius: 8px; font-size: 13px; }
.field-heading { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
h3 { margin: 0; font-size: 14px; }
.field-heading span, .field-note, small { color: #657084; }
.field-expression-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 14px; margin: 14px 0; }
article { padding: 12px; background: #f6f8fa; border-radius: 6px; overflow-wrap: anywhere; }
dl { display: grid; grid-template-columns: 70px minmax(0,1fr); gap: 6px; }
dt { color: #657084; } dd { margin: 0; }
p { line-height: 1.7; }
.field-rule-result { padding: 10px 0; border-bottom: 1px solid #e7ebef; }
details { margin-top: 12px; }
@media (max-width: 700px) { .field-expression-grid { grid-template-columns: 1fr; } }
</style>
