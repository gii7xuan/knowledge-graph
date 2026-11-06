<script setup>
import { computed } from 'vue';
import { dimensionLabels, formatTime, label, requestLabels } from '../reviewMonitor.js';
const props = defineProps({ detail: Object, selected: Object, snapshot: Object, loading: Boolean, error: String });
const slot = computed(() => props.snapshot?.slots.find(item => item.id === props.selected?.voter));
const dimension = computed(() => props.detail?.dimensions?.[props.selected?.dimension] || slot.value?.dimensions?.[props.selected?.dimension]);
const ids = computed(() => dimension.value?.evidence_ids || []);
const evidence = computed(() => (props.detail?.evidence || []).filter(item => ids.value.includes(item.evidence_id)));
const pretty = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
const expectedType = target => Array.isArray(target.schema?.type) ? target.schema.type.join(' / ') : target.schema?.type || '见原契约';
</script>

<template>
  <section class="monitor-opinion">
    <h3>{{ slot?.model_id || '模型' }} · {{ dimensionLabels[selected?.dimension] || '意见详情' }}</h3>
    <p v-if="loading" class="monitor-muted">正在读取原记录…</p>
    <p v-if="error" class="monitor-danger" role="alert">{{ error }}</p>
    <template v-if="slot">
      <p>{{ requestLabels[slot.state] || slot.state }} <strong v-if="dimension">· {{ label(dimension.status) }}</strong></p>
      <p v-if="!slot.recorded_valid" class="monitor-muted">此响应尚未成为本轮已记录的有效审核票。</p>
      <p v-if="dimension?.note">{{ dimension.note }}</p>
      <p v-else-if="!dimension" class="monitor-muted">此维度尚无可展示的意见。</p>
      <p v-if="dimension?.relation_id">正式关系：{{ dimension.relation_id }}</p>
      <p v-if="slot.error" class="monitor-danger">{{ slot.error }}</p>
      <p v-if="slot.correction_stop_reason">补正停止原因：{{ slot.correction_stop_reason }}</p>
    </template>
    <template v-if="detail">
      <blockquote v-for="item in evidence" :key="item.evidence_id">
        <strong>{{ item.evidence_id }}</strong><p>{{ item.quote }}</p>
        <small>{{ item.anchor_valid ? '原文锚点已核验' : item.anchor_status || '锚点未核验' }}</small>
      </blockquote>
      <p v-if="dimension && !ids.length" class="monitor-muted">该项未引用证据 ID。</p>
      <p v-for="id in ids.filter(id => !evidence.some(item => item.evidence_id === id))" :key="id" class="monitor-danger">未在本轮冻结证据包中找到 {{ id }}</p>
      <details v-if="detail.correction?.targets?.length" class="monitor-repairs" open>
        <summary>字段补正对照</summary>
        <p v-for="target in detail.correction.targets" :key="target.path"><code>{{ target.path }}</code> · {{ target.kind }} · 原类型 {{ target.actual_type }} · 预期 {{ expectedType(target) }}</p>
        <p v-if="detail.correction.protected_judgments_unchanged">已有维度判断、证据角色和有效引用保持不变。</p>
        <div class="monitor-response-grid">
          <details><summary>原始响应</summary><pre>{{ pretty(detail.raw_response) }}</pre></details>
          <details><summary>补丁响应</summary><pre>{{ pretty(detail.raw_correction_response) }}</pre></details>
          <details><summary>程序合并结果</summary><pre>{{ pretty(detail.corrected_response) }}</pre></details>
        </div>
      </details>
      <details v-if="detail.attempts?.length">
        <summary>请求执行与用量</summary>
        <p v-for="attempt in detail.attempts" :key="attempt.attempt_id">
          {{ attempt.phase === 'field_patch' ? '字段补正' : '初始请求' }} · {{ attempt.state }}
          · {{ formatTime(attempt.started_at) }} · 输入 {{ attempt.input_tokens ?? '待核实' }} / 输出 {{ attempt.output_tokens ?? '待核实' }} tokens
        </p>
      </details>
      <details v-if="detail.raw_response != null && !detail.correction?.targets?.length"><summary>模型响应原文</summary><pre>{{ pretty(detail.raw_response) }}</pre></details>
    </template>
  </section>
</template>
