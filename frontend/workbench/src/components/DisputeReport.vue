<script setup>
import { routeNames, focusStatus, observationLabel, effectLabel } from '../revisionLoop.js';
defineProps({ report: Object, reviews: { type: Array, default: () => [] } });
</script>
<template>
  <section v-if="report" class="dispute-report" aria-label="具体争议与定向复核">
    <h3>具体争议与定向复核</h3>
    <p v-if="report.historical_projection">依据已保存单项意见整理的历史争议；尚未进行新的定向复核。</p>
    <p>按具体表达、原文位置和理由处理分歧。多数票不消除少数席位提出的语义问题。</p>
    <div class="evidence-observation" v-for="item in report.evidence_observations" :key="item.voter_id">
      <strong>{{ item.voter_id }}</strong> · 正面支持：{{ observationLabel(item.positive_support.status) }}；反证检查：{{ observationLabel(item.counterevidence_search.status) }}
      <p>正面证据：{{ item.positive_support.evidence_ids.join('、') || '未记录' }}；实质反证：{{ item.counterevidence_search.counter_evidence_ids.join('、') || '未记录' }}</p>
    </div>
    <p>未发现反证与找到支持证据分别记录。无效或缺失席位不计赞成、反对或争议解决。</p>
    <p v-for="seat in report.technical_seats" :key="seat.voter_id">{{ seat.voter_id }}：{{ seat.response_status }} · 技术或契约问题，需独立恢复。</p>
    <p v-if="!report.disputes?.length">当前有效意见未形成可比较的具体分歧；审核完整度和准入另行核验。</p>
    <details v-for="dispute in report.disputes" :key="dispute.dispute_id">
      <summary>{{ dispute.check }} · {{ routeNames[dispute.route] }} · {{ focusStatus(reviews.find(r => r.dispute_id === dispute.dispute_id)?.status || dispute.status) }}</summary>
      <p><strong>定向复核问题：</strong>{{ dispute.question }}</p>
      <p>所需材料：{{ dispute.required_material }}。</p>
      <article v-for="opinion in dispute.opinions" :key="opinion.voter_id">
        <strong>{{ opinion.model_id || opinion.voter_id }} · {{ opinion.status }}</strong>
        <p>具体表达：{{ opinion.claim.expression.head }} — {{ opinion.claim.expression.surface_relation }} — {{ opinion.claim.expression.tail }}</p>
        <p>理由：{{ opinion.reason }}</p><p>问题类型：{{ opinion.issue_type }}；事实含义影响：{{ effectLabel(opinion.meaning_effect) }}</p>
        <details><summary>对应原文位置与内容</summary><blockquote v-for="e in opinion.source_positions" :key="e.evidence_id"><small>{{ e.section_id }} · {{ e.chunk_id }} · {{ e.start }}–{{ e.end }} · {{ e.evidence_id }}</small><p>{{ e.quote }}</p></blockquote></details>
      </article>
    </details>
    <p>定向复核仅回答这项争议，不替代整条候选或新版本的准入审核；证据仍不足时转人工处理。</p>
  </section>
</template>
<style scoped>
.dispute-report { margin: 14px 0; padding: 14px; border: 1px solid #dce3eb; border-radius: 8px; font-size: 13px; }
h3 { margin: 0; font-size: 14px; } p { line-height: 1.7; overflow-wrap: anywhere; }
details,article { margin-top: 12px; } article,.evidence-observation { padding: 10px; background: #f6f8fa; margin-top: 8px; border-radius: 5px; }
blockquote { margin: 10px 0; padding-left: 10px; border-left: 3px solid #b8d7d7; } small { color: #657084; }
</style>
