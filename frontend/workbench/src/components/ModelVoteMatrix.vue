<script setup>
import { dimensionLabels, label, requestLabels, tone, formatTime } from '../reviewMonitor.js';
defineProps({ snapshot: { type: Object, required: true }, selected: { type: Object, default: () => ({}) } });
defineEmits(['select']);
</script>

<template>
  <section class="monitor-votes">
    <div class="monitor-section-title"><h2>独立模型意见</h2><span>已返回 {{ snapshot.received_seats }}/{{ snapshot.required_seats }} · 已记录有效席位 {{ snapshot.accepted_seats }}/{{ snapshot.required_seats }}</span></div>
    <div v-if="!snapshot.required_seats" class="monitor-muted">历史记录未保存委员会名单。</div>
    <div class="monitor-models">
      <article v-for="slot in snapshot.slots" :key="slot.id">
        <strong>{{ slot.model_id || slot.id }}</strong>
        <span :class="['monitor-tag', tone(slot.state)]">{{ requestLabels[slot.state] || slot.state }}</span>
        <small v-if="slot.correction_attempted">字段补正 {{ slot.correction_attempted }} 次</small>
        <small v-if="slot.cached">复用已有有效结果</small>
        <small v-if="slot.started_at">开始：{{ formatTime(slot.started_at) }}</small>
        <small v-if="slot.response_received_at">返回：{{ formatTime(slot.response_received_at) }}</small>
        <small v-if="slot.error_code">{{ slot.error_code }}</small>
      </article>
    </div>
    <div class="monitor-table-wrap">
      <table class="monitor-matrix">
        <thead><tr><th scope="col">审核维度</th><th v-for="slot in snapshot.slots" :key="slot.id" scope="col">{{ slot.model_id || slot.id }}</th></tr></thead>
        <tbody><tr v-for="(title, key) in dimensionLabels" :key="key">
          <th scope="row">{{ title }}</th>
          <td v-for="slot in snapshot.slots" :key="slot.id">
            <button type="button" :class="['monitor-cell', tone(slot.dimensions[key]?.status)]"
              :aria-pressed="selected.voter === slot.id && selected.dimension === key"
              :aria-label="slot.model_id + '，' + title + '，查看意见'"
              @click="$emit('select', { voter: slot.id, dimension: key })">
              {{ slot.dimensions[key] ? label(slot.dimensions[key].status) : '—' }}
            </button>
          </td>
        </tr></tbody>
      </table>
    </div>
    <p class="monitor-muted">点击单项意见查看公开说明和证据。请求失败不计作反对；程序决定单独展示。</p>
  </section>
</template>
