<script setup>
import { actionLabels, formatTime, label } from '../reviewMonitor.js';
defineProps({ snapshot: { type: Object, required: true } });
</script>

<template>
  <section class="monitor-timeline">
    <h2>Agent 调查过程</h2>
    <p class="monitor-muted">规划角色：{{ snapshot.versions.planner_model_id || '历史未记录' }} Agent</p>
    <p v-if="!snapshot.events.length" class="monitor-muted">尚无已保存的规划或工具记录。</p>
    <ol>
      <li v-for="event in snapshot.events" :key="event.id">
        <div class="monitor-event-role">{{ event.kind === 'tool' ? '工具执行' : event.role === 'system' ? '系统动作' : '模型建议' }} · 第 {{ event.step_no }} 步</div>
        <strong>{{ actionLabels[event.action] || event.action || '未产生有效动作' }}</strong>
        <span class="monitor-tag">{{ label(event.status) }}</span>
        <p v-if="event.summary">{{ event.summary }}</p>
        <p v-if="event.error" class="monitor-danger">{{ event.error }}</p>
        <p v-if="event.new_evidence_count != null">新增证据 {{ event.new_evidence_count }} 条</p>
        <time class="monitor-muted">记录时间：{{ formatTime(event.at) }}</time>
      </li>
    </ol>
    <div class="monitor-blocker">
      <strong>{{ label(snapshot.status) }}</strong>
      <p>当前阶段：{{ label(snapshot.phase) }}</p>
      <p v-if="snapshot.pending_action">当前动作：{{ actionLabels[snapshot.pending_action] || snapshot.pending_action }}</p>
      <p v-if="snapshot.stop_reason">停止原因：{{ snapshot.stop_reason }}</p>
    </div>
    <details v-if="snapshot.budget?.usage"><summary>本轮预算记录</summary>
      <p>模型调用 {{ snapshot.budget.usage.llm_calls ?? '未记录' }} · 工具调用 {{ snapshot.budget.usage.tool_calls ?? '未记录' }}</p>
      <p>输入 {{ snapshot.budget.usage.input_tokens ?? '未记录' }} / 输出 {{ snapshot.budget.usage.output_tokens ?? '未记录' }} tokens</p>
      <p>字段补正上限：每席 {{ snapshot.correction_limits.max_corrections_per_seat ?? '历史未记录' }} 次</p>
    </details>
  </section>
</template>
