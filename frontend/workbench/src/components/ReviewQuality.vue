<script setup>
import { qualityMetricValue } from '../reviewMonitor.js';
defineProps({ report: Object });
</script>
<template>
  <section v-if="report" class="review-quality" aria-label="错放与错修质量评估">
    <div class="quality-title"><strong>错放与错修质量评估</strong><span>{{ report.partition === 'development' ? '开发回归集' : '独立验证集' }} · 四维人工重标 {{ report.human_resolved }} / {{ report.population }}</span></div>
    <div class="quality-grid"><article v-for="metric in report.metrics" :key="metric.id" :title="metric.reason || metric.denominator_note">
      <span>{{ metric.title }}</span><strong>{{ qualityMetricValue(metric.value) }}</strong>
      <small>分子 / 分母 {{ metric.numerator }} / {{ metric.denominator }} · 未确认 {{ metric.unknown_count }}</small>
    </article></div>
    <p>{{ report.quality_claim }}。意见收齐不等于判断正确；没有自动准入时精确率不写成0%或100%。</p>
    <p v-if="report.auto_admission_policy_enabled === false">本轮关闭自动准入，实际自动准入 {{ report.auto_admitted }} 条；全部转人工不能作为质量提高的证据。</p>
    <template v-if="report.recommendation_metrics">
      <strong>影子建议评价 · 仅适用于有效标签子集</strong>
      <p>未标注 {{ report.recommendation_metrics.unlabeled }} · 人工不可判断 {{ report.recommendation_metrics.human_admission_unknown }}</p>
      <div class="quality-grid"><article v-for="metric in report.recommendation_metrics.metrics" :key="metric.id" :title="metric.reason || metric.scope">
        <span>{{ metric.title }}</span><strong>{{ qualityMetricValue(metric.value) }}</strong>
        <small>分子 / 分母 {{ metric.numerator }} / {{ metric.denominator }}</small><small>{{ metric.reason }}</small>
      </article></div>
    </template>
    <p v-if="report.technical_note" class="technical-note">{{ report.technical_note }}</p>
  </section>
</template>
<style scoped>
.review-quality { padding: 12px 20px; border-bottom: 1px solid #dce3eb; font-size: 12px; }
.quality-title { display: flex; flex-wrap: wrap; gap: 15px; justify-content: space-between; color: #526074; }
.quality-title strong { color: #25344a; } .quality-grid { display: grid; grid-template-columns: repeat(6,minmax(0,1fr)); gap: 10px; margin: 10px 0; }
article { padding: 10px; background: #f6f8fa; border-radius: 6px; display: grid; gap: 6px; }
article strong { font-size: 17px; color: #263a4b; } article small,p { color: #647184; } p { margin: 4px 0; line-height: 1.6; }
.technical-note { padding: 7px 10px; background: #fff5df; border-radius: 5px; color: #745420; }
@media(max-width:1000px) { .quality-grid { grid-template-columns: repeat(3,minmax(0,1fr)); } }
@media(max-width:600px) { .quality-grid { grid-template-columns: repeat(2,minmax(0,1fr)); } }
</style>
