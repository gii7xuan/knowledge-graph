<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue';
import { api, post } from '../api';

const tasks = ref([]);
const selected = ref(null);
const owlPatch = computed(() => selected.value?.publication_preview?.source_patch || selected.value?.delta?.owl_patch);
const busy = ref(false);
const error = ref('');
const reason = ref('');
const confirmImpact = ref(false);
const confirmProfile = ref(false);
const jobs = reactive({});
const currentRelease = ref('');
const enabled = ref(false);
const labels = { proposed: '待调查', in_review: '审核中', needs_human: '待人工处理', approved: '审核通过，待发布', publishing: '发布处理中', published: '已发布', rejected: '已拒绝', needs_rebase: '需更新基线并重审' };
let timer;

async function refresh() {
  try {
    const result = await api('/api/ontology/extensions');
    tasks.value = result.items || [];
    currentRelease.value = result.current_release_id;
    enabled.value = result.enabled;
    if (selected.value) selected.value = await api('/api/ontology/extensions/' + selected.value.extension_id);
  } catch (cause) { error.value = cause.message; }
}
async function inspect(task) {
  error.value = ''; reason.value = ''; confirmImpact.value = false; confirmProfile.value = false;
  try { selected.value = await api('/api/ontology/extensions/' + task.extension_id); }
  catch (cause) { error.value = cause.message; }
}
async function act(action) {
  const task = selected.value;
  if (!task) return;
  busy.value = true; error.value = '';
  try {
    const preview = task.publication_preview;
    const payload = { expected_row_version: task.row_version, reason: reason.value,
      expected_base_release_id: action === 'rebase' ? currentRelease.value : task.base_release_id,
      proposal_digest: preview?.proposal_digest, confirm_high_impact: confirmImpact.value, confirm_profile_differences: confirmProfile.value };
    const result = await post('/api/ontology/extensions/' + task.extension_id + '/' + action, payload);
    if (action === 'review') jobs[task.extension_id] = result;
    await refresh();
  } catch (cause) { error.value = cause.message; }
  finally { busy.value = false; }
}
async function decide(action) {
  busy.value = true; error.value = '';
  try {
    await post('/api/ontology/extensions/' + selected.value.extension_id + '/decision', { action, reason: reason.value, expected_row_version: selected.value.row_version });
    await refresh();
  } catch (cause) { error.value = cause.message; }
  finally { busy.value = false; }
}
onMounted(() => {
  refresh();
  timer = setInterval(async () => {
    const active = Object.entries(jobs).filter(([, job]) => ['queued', 'running'].includes(job.status));
    if (!active.length) return;
    for (const [id, job] of active) {
      try { jobs[id] = await api('/api/triplets/audit-jobs/' + job.job_id); }
      catch (cause) { error.value = cause.message; }
    }
    await refresh();
  }, 3000);
});
onUnmounted(() => clearInterval(timer));
</script>

<template>
  <section class="review-card" aria-label="本体扩展审核 Agent">
    <div class="candidate-title"><strong>本体扩展审核 Agent</strong><button :disabled="busy" @click="refresh">刷新扩展任务</button></div>
    <p>独立调查本体差异、补充定义证据并由固定委员会审核。默认仅记录审核建议，发布后创建候选新修订并重新审核。</p>
    <div v-if="error" class="notice error" role="alert">{{ error }}</div>
    <p v-if="!enabled" class="subtle">当前配置未启用本体扩展审核。</p>
    <p v-if="!tasks.length" class="subtle">暂无扩展请求。三元组 Agent 确认原文支持且存在本体缺口后会创建任务。</p>
    <div v-for="task in tasks" :key="task.extension_id" class="detail-line">
      <button @click="inspect(task)">{{ task.description || task.extension_id }}</button>
      · {{ labels[task.status] || task.status }} · 修订 {{ task.delta_revision }}
      <span v-if="jobs[task.extension_id]"> · 执行 {{ jobs[task.extension_id].status }}</span>
    </div>
    <div v-if="selected">
      <p class="run-path">{{ selected.extension_id }} · {{ labels[selected.status] || selected.status }} · base {{ selected.base_release_id }}</p>
      <p>触发来源 {{ selected.trigger_candidates?.length || 0 }} · {{ selected.ontology_review_executed ? '已有独立本体审核记录' : '尚未执行本体审核' }}</p>
      <button :disabled="busy || !enabled || ['publishing', 'published', 'needs_rebase'].includes(selected.status)" @click="act('review')">启动 / 继续本体调查</button>
      <button v-if="selected.status === 'needs_rebase'" :disabled="busy" @click="act('rebase')">更新到当前基线，旧批准失效</button>
      <details v-if="selected.delta"><summary>完整差异与生成后的本体</summary><pre class="evidence-text">{{ JSON.stringify(selected.delta, null, 2) }}</pre></details>
      <div v-if="owlPatch" class="notice">
        <p>OWL 来源扩展：新增 {{ owlPatch.added?.length || 0 }} 条、移除 {{ owlPatch.removed?.length || 0 }} 条 RDF 声明。发布完整 Turtle 新版本，旧版本保留。</p>
        <p v-if="owlPatch.profile_after?.auto_review_blocked">含未执行的 OWL 公理，当前应用范围阻止自动准入。结构检查通过不代表 OWL 一致性通过。</p>
        <details><summary>OWL 声明增删及逐项操作</summary><pre class="evidence-text">{{ JSON.stringify({ added: owlPatch.added, removed: owlPatch.removed, operations: owlPatch.operations }, null, 2) }}</pre></details>
        <details><summary>审核绑定的完整 OWL 来源：变更前</summary><pre class="evidence-text">{{ owlPatch.base_rdf }}</pre></details>
        <details><summary>审核绑定的完整 OWL 来源：变更后</summary><pre class="evidence-text">{{ owlPatch.merged_rdf }}</pre></details>
      </div>
      <details v-for="audit in selected.audits || []" :key="audit.audit_id">
        <summary>{{ audit.audit_id }} · {{ audit.status }} · 建议 {{ audit.result?.recommendation || '调查中' }}</summary>
        <p>采用轮次 {{ audit.result?.selected_round_id || '无' }} · 停止原因 {{ audit.result?.stop_reason || '执行中' }}</p>
        <div v-for="step in audit.steps" :key="`${step.step_no}-${step.role}`">步骤 {{ step.step_no }} · {{ step.role === 'planner' ? '规划动作' : '实际工具' }} · {{ step.action?.action || '' }} · {{ step.status }}</div>
        <details v-for="vote in audit.votes" :key="`${vote.round_id}-${vote.voter_id}`"><summary>轮次 {{ vote.round_id }} · {{ vote.model_id }} · {{ vote.response_status }} · {{ vote.decision || '无有效票' }}</summary><p v-if="vote.error">{{ vote.error }}</p><pre class="evidence-text">{{ JSON.stringify(vote.checks || vote.raw_response?.checks || [], null, 2) }}</pre></details>
        <details><summary>结构、影响、能力问题及推理器检查</summary><pre class="evidence-text">{{ JSON.stringify(audit.proposals?.at(-1)?.check_records || {}, null, 2) }}</pre></details>
        <details><summary>跨 Agent 用量账目</summary><pre class="evidence-text">{{ JSON.stringify(audit.result?.budget || audit.checkpoint?.budget || {}, null, 2) }}</pre></details>
      </details>
      <label>本体审核 / 发布理由<input v-model="reason" placeholder="说明建模依据、影响及处理理由" /></label>
      <template v-if="selected.publication_preview && selected.status !== 'published'">
        <details><summary>发布前后对照与影响清单</summary><pre class="evidence-text">{{ JSON.stringify(selected.publication_preview, null, 2) }}</pre></details>
        <label v-if="selected.publication_preview.risk_category === 'high_impact'"><input v-model="confirmImpact" type="checkbox" />已核对已有定义 / 约束变更及受影响候选</label>
        <label v-if="selected.publication_preview.requires_difference_confirmation"><input v-model="confirmProfile" type="checkbox" />已核对本次应用范围及转换差异</label>
        <button class="primary" :disabled="busy || !reason.trim() || selected.publication_preview.risk_category === 'high_impact' && !confirmImpact || selected.publication_preview.requires_difference_confirmation && !confirmProfile" @click="act('publish')">批准当前差异并发布新版本</button>
      </template>
      <template v-if="!['publishing', 'published'].includes(selected.status)">
        <button :disabled="busy || !reason.trim()" @click="decide('revise')">要求修订</button>
        <button :disabled="busy || !reason.trim()" @click="decide('reject')">拒绝扩展</button>
      </template>
      <p v-if="selected.published_release_id" class="run-path">已发布 {{ selected.published_release_id }}；订阅候选及影响清单中的历史候选按并发版本检查后排入重审，旧票不继承。</p>
    </div>
  </section>
</template>
