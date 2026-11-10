<script setup>
import { changeKinds, facetNames, displayValue } from '../revisionLoop.js';
defineProps({ loop: Object });
</script>
<template>
  <section v-if="loop?.revisions?.length" class="revision-loop" aria-label="修订验证闭环">
    <h3>修订与新版本验证</h3>
    <p class="revision-flow">原始候选 → 修订提案 → 修改前后差异 → 新版本证据核验 → 新版本本体映射 → 最终决定</p>
    <p>修订草案获批与新版准入分别记录。旧版本审核票不作为新版本或拆分子候选的通过票。</p>
    <details v-for="row in loop.revisions" :key="row.revision" :open="row.is_current && !!row.lineage">
      <summary>版本 {{ row.revision }}{{ row.is_current ? ' · 当前版本' : ' · 历史版本' }} · {{ row.expression.head }} — {{ row.expression.surface_relation }} — {{ row.expression.tail }}</summary>
      <p v-if="row.lineage">来源：{{ row.lineage.parent_candidate_id }} · 版本 {{ row.lineage.parent_revision }}；{{ changeKinds[row.lineage.analysis.kind] }}</p>
      <p v-if="row.split_origin">拆分组 {{ row.split_origin.group_id }} · 当前子候选独立核验来源与映射。</p>
      <table v-if="row.lineage?.analysis?.changes?.length"><thead><tr><th>字段</th><th>修改前</th><th>修改后</th></tr></thead><tbody>
        <tr v-for="change in row.lineage.analysis.changes" :key="change.field"><td>{{ change.field }}</td><td>{{ displayValue(change.before) }}</td><td>{{ displayValue(change.after) }}</td></tr>
      </tbody></table>
      <p v-if="row.lineage">核验重点：{{ row.lineage.analysis.facets.map(f => `${facetNames[f.facet]}${f.changed ? '（已变更）' : '（检查保留）'}`).join('；') }}</p>
      <p>本版本证据核验：{{ row.decision.independent_review_completed ? (row.review_axes?.source?.status || row.decision.source_support_result || '见单项结论') : '等待完整的新版本审核' }}；本版本映射：{{ row.review_axes?.mapping?.status || '等待核验' }}。</p>
      <p>{{ row.decision.historical_inactive ? '历史决定（已失效）' : '最终决定' }}：{{ row.decision.decision || '尚无本版本决定' }} · {{ row.decision.workflow_state || '等待证据与映射闭环' }}</p>
    </details>
    <details v-if="loop.drafts?.length"><summary>修订提案与独立审核（{{ loop.drafts.length }}）</summary>
      <div v-for="draft in loop.drafts" :key="draft.draft_id" class="revision-draft"><strong>{{ draft.draft_id }} · {{ draft.status }}</strong>
        <p>{{ draft.before.head }} — {{ draft.before.surface_relation }} — {{ draft.before.tail }} → {{ draft.after.head }} — {{ draft.after.surface_relation }} — {{ draft.after.tail }}</p>
        <p>{{ changeKinds[draft.change_analysis?.kind] || '按原冻结契约复核' }}；有效审核席位 {{ draft.reviewed_seats }}，批准席位 {{ draft.approved_seats }}。草案获批后仍需新版审核。</p>
        <small>本提案来源：{{ draft.evidence_ids.join('、') }}</small>
      </div>
    </details>
    <p v-for="group in loop.split_groups" :key="group.group_id">拆分组 {{ group.group_id }} · {{ group.status }}：{{ group.child_candidate_ids.join('、') }}；每个子候选分别核验证据及本体映射。</p>
  </section>
</template>
<style scoped>
.revision-loop { margin: 14px 0; padding: 14px; border: 1px solid #dce3eb; border-radius: 8px; font-size: 13px; }
h3 { margin: 0; font-size: 14px; } p { line-height: 1.7; overflow-wrap: anywhere; }
.revision-flow, small { color: #657084; } details { margin-top: 12px; }
table { width: 100%; border-collapse: collapse; margin: 10px 0; table-layout: fixed; }
td,th { padding: 8px; border: 1px solid #e7ebef; text-align: left; overflow-wrap: anywhere; font-weight: normal; }
th { background: #f6f8fa; } .revision-draft { padding: 10px 0; border-bottom: 1px solid #e7ebef; }
</style>
