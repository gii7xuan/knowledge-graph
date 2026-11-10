<script setup>
import ReviewAxes from './ReviewAxes.vue';
import ReviewOutcome from './ReviewOutcome.vue';
import ReviewQuality from './ReviewQuality.vue';
import ReviewTaskPolicy from './ReviewTaskPolicy.vue';
import MappingPreflight from './MappingPreflight.vue';
import FieldStandardization from './FieldStandardization.vue';
import RevisionLoop from './RevisionLoop.vue';
import DisputeReport from './DisputeReport.vue';
import { computed, onActivated, onDeactivated, onUnmounted, ref, watch } from 'vue';
import { RefreshCw } from 'lucide-vue-next';
import { api } from '../api.js';
import { filterCandidates, formatTime, label, outcome } from '../reviewMonitor.js';
import AgentReviewTimeline from './AgentReviewTimeline.vue';
import ModelVoteMatrix from './ModelVoteMatrix.vue';
import ReviewOpinionDetail from './ReviewOpinionDetail.vue';

const props = defineProps({ selection: { type: Object, default: () => ({}) } });
const sources = ref([]), batches = ref([]), sourceId = ref(''), batchId = ref('');
const summary = ref(null), candidateId = ref(''), audits = ref([]), auditChoice = ref(''), auditId = ref('');
const roundChoice = ref(''), snapshot = ref(null), cursor = ref(''), selectedCell = ref(null);
const opinion = ref(null), opinionLoading = ref(false), opinionError = ref('');
const error = ref(''), detailError = ref(''), loading = ref(false);
const lastBatchAt = ref(null), lastDetailAt = ref(null);
const filter = ref(''), search = ref('');
const filtered = computed(() => filterCandidates(summary.value?.candidates || [], filter.value, search.value));
const selectedEntry = computed(() => summary.value?.candidates.find(item => item.candidate_id === candidateId.value));
const selectedSource = computed(() => sources.value.find(item => item.id === sourceId.value));
const finalOutcome = computed(() => outcome(snapshot.value));
let active = false, generation = 0, batchTimer, detailTimer, opinionSequence = 0;
let pendingSelection = props.selection;
const controllers = new Set();

function alive(gen) { return active && !document.hidden && generation === gen; }
function stop() {
  generation += 1;
  clearTimeout(batchTimer); clearTimeout(detailTimer);
  controllers.forEach(controller => controller.abort()); controllers.clear();
  opinionSequence += 1; opinionLoading.value = false;
  return generation;
}
async function read(path, gen) {
  if (!alive(gen)) return null;
  const controller = new AbortController(); controllers.add(controller);
  try {
    const data = await api(path, { signal: controller.signal });
    return alive(gen) ? data : null;
  } finally { controllers.delete(controller); }
}
function params(extra = {}) {
  return new URLSearchParams({ source_id: sourceId.value, ...extra }).toString();
}
function savedSelection() {
  return { source_id: sourceId.value, batch_id: batchId.value, candidate_id: candidateId.value,
    audit_id: auditChoice.value, round_id: roundChoice.value };
}
function syncUrl() {
  if (!active || location.pathname !== '/review-monitor') return;
  const values = Object.fromEntries(Object.entries(savedSelection()).filter(([, value]) => value));
  history.replaceState(null, '', '/review-monitor?' + new URLSearchParams(values));
}
function clearDetail() {
  snapshot.value = null; cursor.value = ''; selectedCell.value = null;
  opinion.value = null; opinionError.value = ''; detailError.value = ''; lastDetailAt.value = null;
}
function scheduleBatch(gen) {
  if (alive(gen)) batchTimer = setTimeout(() => refreshBatch(gen), 5000);
}
function scheduleDetail(gen) {
  if (!alive(gen) || !auditId.value) return;
  const polling = snapshot.value?.status === 'running' || snapshot.value?.slots.some(slot => ['running', 'correcting'].includes(slot.state));
  detailTimer = setTimeout(() => refreshDetail(gen), polling ? 2000 : 10000);
}

async function loadOpinion(gen = generation) {
  if (!selectedCell.value || !snapshot.value) return;
  const sequence = ++opinionSequence, selected = { ...selectedCell.value };
  const id = auditId.value, round = snapshot.value.binding.round_id;
  opinionLoading.value = true; opinionError.value = '';
  try {
    const data = await read('/api/review-monitor/audits/' + encodeURIComponent(id) + '/opinion?' +
      params({ voter_id: selected.voter, ...(round != null ? { round_id: round } : {}) }), gen);
    if (data && sequence === opinionSequence) opinion.value = data;
  } catch (cause) {
    if (alive(gen) && sequence === opinionSequence && cause.name !== 'AbortError') opinionError.value = cause.message;
  } finally {
    if (alive(gen) && sequence === opinionSequence) opinionLoading.value = false;
  }
}
function chooseCell(value) {
  selectedCell.value = value; opinion.value = null; loadOpinion();
}
async function refreshDetail(gen) {
  if (!auditId.value || !alive(gen)) return;
  try {
    const extra = { ...(roundChoice.value ? { round_id: roundChoice.value } : {}),
      ...(cursor.value ? { after: cursor.value } : {}) };
    const data = await read('/api/review-monitor/audits/' + encodeURIComponent(auditId.value) + '/snapshot?' + params(extra), gen);
    if (!data) return;
    lastDetailAt.value = data.observed_at; detailError.value = '';
    if (!data.not_modified) {
      const oldBinding = snapshot.value?.binding;
      snapshot.value = data; cursor.value = data.cursor;
      if (oldBinding && oldBinding.proposal_digest !== data.binding.proposal_digest) {
        selectedCell.value = null; opinion.value = null; opinionSequence += 1;
      }
      if (selectedCell.value) loadOpinion(gen);
    }
  } catch (cause) {
    if (alive(gen) && cause.name !== 'AbortError') detailError.value = '连接中断，保留最后记录：' + cause.message;
  } finally { scheduleDetail(gen); }
}
async function loadCandidate(gen) {
  clearTimeout(detailTimer); clearDetail();
  const entry = selectedEntry.value;
  auditId.value = auditChoice.value || entry?.audit_id || '';
  if (!candidateId.value) return;
  try {
    const data = await read('/api/review-monitor/candidates/' + encodeURIComponent(candidateId.value) + '/audits?' + params(), gen);
    if (!data) return;
    audits.value = data.items;
    if (auditId.value && !audits.value.some(item => item.audit_id === auditId.value)) {
      auditId.value = ''; detailError.value = '指定审核不属于当前数据源或候选。'; return;
    }
    syncUrl();
    await refreshDetail(gen);
  } catch (cause) {
    if (alive(gen) && cause.name !== 'AbortError') detailError.value = cause.message;
  }
}
async function refreshBatch(gen, target = null) {
  if (!batchId.value || !alive(gen)) return;
  try {
    const data = await read('/api/review-monitor/batches/' + encodeURIComponent(batchId.value) + '/summary?' + params(), gen);
    if (!data) return;
    summary.value = data; lastBatchAt.value = data.observed_at; error.value = '';
    const selectedExists = data.candidates.some(item => item.candidate_id === candidateId.value);
    if (target || !selectedExists) {
      candidateId.value = target?.candidate_id && data.candidates.some(item => item.candidate_id === target.candidate_id)
        ? target.candidate_id : data.candidates.find(item => item.status === 'running')?.candidate_id || data.candidates[0]?.candidate_id || '';
      auditChoice.value = target?.audit_id || ''; roundChoice.value = target?.round_id || '';
      await loadCandidate(gen);
    } else if (!auditChoice.value && (selectedEntry.value?.audit_id || '') !== auditId.value) {
      roundChoice.value = ''; await loadCandidate(gen);
    }
  } catch (cause) {
    if (alive(gen) && cause.name !== 'AbortError') error.value = '连接中断，保留最后记录：' + cause.message;
  } finally {
    if (alive(gen)) loading.value = false;
    scheduleBatch(gen);
  }
}
async function bootstrap(target = savedSelection()) {
  const gen = stop();
  if (!alive(gen)) return;
  loading.value = true; error.value = '';
  try {
    const data = await read('/api/review-monitor/sources', gen);
    if (!data) return;
    sources.value = data.items;
    const desired = target.source_id || data.default_source_id;
    const source = data.items.find(item => item.id === desired && item.available);
    if (!source) { error.value = '指定数据源不可用，请选择已登记的数据源。'; loading.value = false; return; }
    sourceId.value = source.id;
    const list = await read('/api/review-monitor/batches?' + params(target.candidate_id ? { candidate_id: target.candidate_id } : {}), gen);
    if (!list) return;
    batches.value = list.items;
    batchId.value = list.items.some(item => item.id === target.batch_id) ? target.batch_id : list.items[0]?.id || '';
    if (!batchId.value) { loading.value = false; summary.value = null; clearDetail(); return; }
    await refreshBatch(gen, target);
  } catch (cause) {
    if (alive(gen) && cause.name !== 'AbortError') { error.value = cause.message; loading.value = false; }
  }
}
function chooseSource() {
  const target = { source_id: sourceId.value };
  batches.value = []; batchId.value = ''; summary.value = null; audits.value = []; candidateId.value = ''; clearDetail();
  bootstrap(target);
}
function chooseBatch() {
  summary.value = null; audits.value = []; candidateId.value = ''; clearDetail();
  bootstrap({ source_id: sourceId.value, batch_id: batchId.value });
}
function chooseCandidate(item) {
  const gen = stop();
  candidateId.value = item.candidate_id; auditChoice.value = ''; roundChoice.value = ''; audits.value = [];
  loadCandidate(gen); scheduleBatch(gen);
}
function chooseAudit() {
  const gen = stop(); roundChoice.value = '';
  loadCandidate(gen); scheduleBatch(gen);
}
function chooseRound() {
  const gen = stop(); clearDetail(); syncUrl();
  refreshDetail(gen); scheduleBatch(gen);
}
function visibilityChanged() {
  if (document.hidden) stop();
  else if (active) bootstrap(savedSelection());
}
watch(() => props.selection, value => {
  pendingSelection = value;
  if (active && !document.hidden) { pendingSelection = null; summary.value = null; clearDetail(); bootstrap(value); }
});
onActivated(() => {
  active = true; document.addEventListener('visibilitychange', visibilityChanged);
  const target = pendingSelection || savedSelection(); pendingSelection = null; bootstrap(target);
});
onDeactivated(() => { active = false; stop(); document.removeEventListener('visibilitychange', visibilityChanged); });
onUnmounted(() => { active = false; stop(); document.removeEventListener('visibilitychange', visibilityChanged); });
</script>

<template>
  <section class="monitor-page">
    <header class="page-toolbar">
      <div class="page-title"><h1>Agent 审核监控</h1><span class="subtle">已保存的执行记录 · 只读</span></div>
      <button class="icon-button" aria-label="刷新审核监控" @click="bootstrap(savedSelection())"><RefreshCw :size="17" /></button>
    </header>
    <div class="monitor-controls">
      <label>数据源<select v-model="sourceId" aria-label="数据源" @change="chooseSource"><option value="" disabled>选择数据源</option>
        <option v-for="source in sources" :key="source.id" :value="source.id" :disabled="!source.available">{{ source.label }}{{ source.available ? '' : '（不可用）' }}</option>
      </select></label>
      <label>审核批次<select v-model="batchId" aria-label="审核批次" :disabled="!batches.length" @change="chooseBatch">
        <option v-for="batch in batches" :key="batch.id" :value="batch.id">{{ batch.label }}</option>
      </select></label>
      <span class="monitor-tag">{{ selectedSource?.kind === 'experiment' ? '实验库 · 只读' : '生产记录 · 只读查看' }}</span>
      <span class="monitor-muted">概览更新：{{ lastBatchAt ? formatTime(lastBatchAt) : '等待记录' }}</span>
    </div>
    <div v-if="error" class="monitor-alert danger" role="alert">{{ error }}</div>
    <div v-if="summary?.paused" class="monitor-alert warning">
      <strong>批次已暂停</strong> · {{ summary.pause_reason.reason_code === 'INSUFFICIENT_BALANCE' ? '模型服务余额不足' : summary.pause_reason.reason_code || '等待处理' }}
      <span v-if="summary.pause_reason.model_id"> · {{ summary.pause_reason.model_id }}</span>
    </div>
    <div v-if="summary && !summary.population_complete" class="monitor-alert">此历史批次未登记完整的候选清单；当前统计仅覆盖已有审核记录。</div>
    <div v-if="summary" class="monitor-stats">
      <div><span>候选总数</span><strong>{{ summary.counts.total }}</strong></div>
      <div><span>已开始</span><strong>{{ summary.counts.started }}</strong></div>
      <div><span>处理结束</span><strong>{{ summary.counts.processing_closed }}</strong></div>
      <div><span>三席审核齐全</span><strong>{{ summary.counts.three_seats_ready }}</strong></div>
      <div><span>运行中</span><strong>{{ summary.counts.running }}</strong></div>
      <div><span>已有意见分歧</span><strong>{{ summary.counts.disagreements }}</strong></div>
    </div>
    <ReviewQuality :report="summary?.quality_report" />
    <div v-if="!summary && loading" class="empty-state">正在读取审核记录…</div>
    <div v-if="!summary && !loading && !error" class="empty-state">此数据源暂无审核批次。</div>
    <div v-if="summary" class="monitor-layout">
      <aside class="monitor-candidates">
        <label class="monitor-search">候选检索<input v-model="search" placeholder="样本ID、实体或关系" /></label>
        <label>状态筛选<select v-model="filter" aria-label="状态筛选"><option value="">全部候选</option><option value="running">运行中</option>
          <option value="incomplete">审核未完成</option><option value="disagreement">已有分歧</option><option value="semantic">需语义修订</option><option value="legacy">旧版待区分修订</option>
          <option value="normalization">待无损规范化</option><option value="evidence">证据不足</option><option value="mapping">等待映射</option><option value="scope">范围排除或待确认</option></select></label>
        <p class="monitor-muted">{{ filtered.length }} / {{ summary.counts.total }} 条</p>
        <button v-for="item in filtered" :key="item.candidate_id" type="button" class="monitor-candidate"
          :aria-pressed="candidateId === item.candidate_id" @click="chooseCandidate(item)">
          <strong>{{ item.sample_id || item.candidate_id }}</strong>
          <span>{{ item.head }} — {{ item.relation }} — {{ item.tail }}</span>
          <small>{{ label(item.status) }} · 有效席位 {{ item.accepted_seats || 0 }}/{{ item.required_seats || '—' }}</small>
        </button>
        <p v-if="!filtered.length" class="monitor-muted">没有匹配的候选。</p>
      </aside>
      <main class="monitor-detail">
        <section v-if="selectedEntry" class="monitor-selection">
          <div class="monitor-section-title"><strong>{{ selectedEntry.sample_id || selectedEntry.candidate_id }}</strong><span>候选版本 {{ selectedEntry.revision }}</span></div>
          <p>{{ selectedEntry.head }} — {{ selectedEntry.relation }} — {{ selectedEntry.tail }}</p>
          <div class="monitor-controls compact">
            <label>审核记录<select v-model="auditChoice" aria-label="审核记录" @change="chooseAudit"><option value="">本批次最新审核</option>
              <option v-for="item in audits" :key="item.audit_id" :value="item.audit_id">版本{{ item.revision }} · {{ label(item.status) }} · {{ formatTime(item.created_at) }}</option>
            </select></label>
            <label v-if="snapshot?.rounds.length">审核轮次<select v-model="roundChoice" aria-label="审核轮次" @change="chooseRound"><option value="">最新轮次</option>
              <option v-for="round in snapshot.rounds" :key="round.round_id" :value="String(round.round_id)">第{{ round.round_id }}轮</option>
            </select></label>
          </div>
        </section>
        <p v-if="detailError" class="monitor-alert danger" role="alert">{{ detailError }}</p>
        <p v-if="snapshot?.historical" class="monitor-alert warning">这是旧候选版本的历史审核，不参与当前版本准入。</p>
        <div v-if="!snapshot && !detailError" class="empty-state">{{ auditId ? '正在读取 Agent 和模型记录…' : '本批次尚未开始审核这条候选。' }}</div>
        <template v-if="snapshot">
          <div class="monitor-columns">
            <AgentReviewTimeline :snapshot="snapshot" />
            <div>
              <ModelVoteMatrix :snapshot="snapshot" :selected="selectedCell || {}" @select="chooseCell" />
              <ReviewOpinionDetail v-if="selectedCell" :snapshot="snapshot" :selected="selectedCell" :detail="opinion"
                :loading="opinionLoading" :error="opinionError" />
            </div>
          </div>
          <section class="monitor-outcome">
            <div class="monitor-section-title"><h2>程序汇总</h2><strong>{{ finalOutcome.title }}</strong></div>
            <ReviewAxes :axes="snapshot.review_axes || {}" :pending-actions="snapshot.decision?.pending_actions || []" />
            <ReviewOutcome :result="snapshot.review_outcome" />
            <ReviewTaskPolicy :policy="snapshot.scope_policy" :failure="snapshot.failure_handling" :clarifications="snapshot.semantic_clarifications || []" />
            <MappingPreflight :report="snapshot.mapping_preflight" :evidence="snapshot.mapping_source_evidence || []" />
            <FieldStandardization :spec="snapshot.field_spec" :report="snapshot.field_standardization" />
            <RevisionLoop :loop="snapshot.revision_loop" />
            <DisputeReport :report="snapshot.dispute_report" :reviews="snapshot.focused_reviews || []" />
            <p>{{ finalOutcome.detail }}</p>
            <p class="monitor-muted">三席审核齐全与允许准入分别记录。查看页面不会发起审核、恢复或模型请求。</p>
            <details><summary>审核版本与绑定</summary>
              <p>审核：{{ snapshot.audit_id }} · 版本 {{ snapshot.revision }} · 轮次 {{ snapshot.binding.round_id ?? '尚无' }}</p>
              <p>契约：{{ snapshot.versions.response_contract_version }} · 本体：{{ snapshot.binding.ontology_release_id }}</p>
              <p>提案：{{ snapshot.binding.proposal_digest }} · 证据包：{{ snapshot.binding.evidence_hash }}</p>
            </details>
            <p class="monitor-muted">详情更新：{{ formatTime(lastDetailAt) }}</p>
          </section>
        </template>
      </main>
    </div>
  </section>
</template>

<style src="../reviewMonitor.css"></style>
