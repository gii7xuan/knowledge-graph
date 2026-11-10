<script setup>
import { computed } from 'vue';
import { axisLabels, axisStatusLabel, tone, label } from '../reviewMonitor.js';
import { meaningEffectLabel } from '../fieldStandards.js';
const props = defineProps({ axes: { type: Object, default: () => ({}) }, pendingActions: { type: Array, default: () => [] } });
const explanations = {
  source: '原文是否支持实体、关系及方向、条件、频率和模态。',
  expression: '字段归类、重复限定与实体表达是否规范。',
  mapping: '实体身份、正式关系定义与类型约束是否匹配。',
  scope: '这类知识是否属于本次建图目标。'
};
const findings = computed(() => props.axes.expression?.findings || []);
</script>

<template>
  <section class="review-axes" aria-label="四维审核结论">
    <p v-if="axes.legacy" class="review-axes-note">历史结论按原记录展示；旧版“需要修订”尚未区分规范化与语义修订。</p>
    <div class="review-axes-grid">
      <article v-for="(title, key) in axisLabels" :key="key" class="review-axis">
        <div class="review-axis-title"><strong>{{ title }}</strong>
          <span :class="['monitor-tag', tone(axes[key]?.status)]">{{ axisStatusLabel(key, axes[key]?.status) }}</span>
        </div>
        <p>{{ explanations[key] }}</p>
        <p v-if="key === 'expression' && axes[key]?.status === 'lossless_normalization'">限定含义已核验保留；待规范化处理，尚未自动修改候选。</p>
        <p v-if="key === 'mapping' && axes[key]?.status === 'context_incomplete'">需要补齐身份、定义或约束上下文，暂不能确认正式映射。</p>
        <p v-if="key === 'scope' && axes[key]?.reason">{{ axes[key].category_label }} · {{ axes[key].reason }}</p>
      </article>
    </div>
    <details v-if="findings.length" class="review-axes-findings">
      <summary>表达问题与保留依据（{{ findings.length }}项）</summary>
      <div v-for="(finding, index) in findings" :key="index">
        <p><strong v-if="finding.voter_id">{{ finding.voter_id }} · </strong>{{ finding.note }}</p>
        <p v-if="finding.rule_id"><strong>{{ finding.rule_id }}</strong> · {{ meaningEffectLabel(finding.meaning_effect) }}；建议：{{ finding.suggested_change }}</p>
        <p v-if="finding.normalization?.plan_id">对应确定性规范化计划；原文依据：{{ finding.evidence_ids.join('、') }}</p>
        <p v-else-if="finding.normalization">保留限定：“{{ finding.normalization.value }}”；原文依据：{{ finding.evidence_ids.join('、') }}</p>
      </div>
    </details>
    <p class="review-axes-note">四项结论独立记录，不合并为单一通过率。收录范围排除不代表事实错误。</p>
    <p v-if="pendingActions.length" class="review-axes-note">待办：{{ pendingActions.map(label).join('；') }}</p>
  </section>
</template>

<style scoped>
.review-axes { margin: 12px 0; }
.review-axes-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.review-axis { padding: 12px 14px; border: 1px solid #dce3eb; border-radius: 8px; background: #fff; }
.review-axis-title { display: flex; justify-content: space-between; gap: 10px; align-items: center; flex-wrap: wrap; }
.review-axis p, .review-axes-note { color: #657084; font-size: 12px; line-height: 1.65; margin: 8px 0 0; }
.review-axes-findings { margin-top: 10px; font-size: 13px; }
.review-axes-findings div { border-bottom: 1px solid #edf0f4; padding: 4px 0; }
@media (max-width: 700px) { .review-axes-grid { grid-template-columns: 1fr; } }
</style>
