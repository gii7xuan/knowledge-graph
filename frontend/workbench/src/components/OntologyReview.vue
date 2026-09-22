<script setup>
import { computed, onActivated, onDeactivated, onUnmounted, reactive, ref, watch } from 'vue';
import { Check, ChevronLeft, ChevronRight, Clock3, Pencil, Play, RefreshCw, X } from 'lucide-vue-next';
import { api, post } from '../api';
import { candidateModifications, draftForCandidate } from '../ontologyDraft';

const props = defineProps({ markdownFiles: { type: Array, default: () => [] } });
const markdownPath = ref('');
const sourcePath = ref('ontologyBuild/ontology_candidates.json');
const reviewMode = ref('manual');
const reviewer = ref('human');
const result = ref({});
const items = ref([]);
const total = ref(0);
const statusFilter = ref('pending_review_candidates');
const keyword = ref('');
const page = ref(1);
const pageSize = 20;
const drafts = reactive({});
const decisionErrors = reactive({});
const busyId = ref('');
const busyAction = ref('');
const error = ref('');
const message = ref('');
const job = ref({ exists: false, running: false });
const sidebarWidth = ref(Number(localStorage.getItem('ontologyBuild.sidebarWidth')) || 360);
let pollTimer;
let dragging = false;
let requestId = 0;
let keywordTimer;

watch(() => props.markdownFiles, files => {
  if (!markdownPath.value && files.length) markdownPath.value = files[0];
}, { immediate: true });

watch(statusFilter, () => { page.value = 1; loadItems(); });
watch(keyword, () => {
  page.value = 1;
  if (keywordTimer) clearTimeout(keywordTimer);
  keywordTimer = setTimeout(loadItems, 250);
});
watch(page, loadItems);

const summary = computed(() => result.value.summary || {});
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)));
const visibleItems = computed(() => items.value);
const sidebarStyle = computed(() => ({ '--ontology-sidebar-width': `${sidebarWidth.value}px` }));

function setupDrafts() {
  for (const item of visibleItems.value) {
    if (!drafts[item.candidate_id]) {
      drafts[item.candidate_id] = draftForCandidate(item);
    }
  }
}

watch(visibleItems, setupDrafts, { immediate: true });

async function refresh() {
  error.value = '';
  try {
    result.value = await api('/api/ontology/overview');
    await loadItems();
  } catch (cause) {
    error.value = cause.message;
  }
}

async function loadItems() {
  const current = ++requestId;
  const params = new URLSearchParams({
    status: statusFilter.value,
    keyword: keyword.value,
    page: String(page.value),
    page_size: String(pageSize)
  });
  try {
    const data = await api('/api/ontology/items?' + params);
    if (current !== requestId) return;
    items.value = data.items || [];
    total.value = data.total || 0;
    if (page.value > pageCount.value) {
      page.value = pageCount.value;
      await loadItems();
    }
  } catch (cause) {
    if (current === requestId) error.value = cause.message;
  }
}

async function runReview() {
  busyAction.value = 'review';
  error.value = '';
  message.value = '';
  try {
    result.value = await post('/api/ontology/review-run', {
      source_path: sourcePath.value,
      review_mode: reviewMode.value,
      reviewer: reviewer.value
    });
    statusFilter.value = 'pending_review_candidates';
    page.value = 1;
    await loadItems();
    message.value = '审核队列已生成';
  } catch (cause) {
    error.value = cause.message;
  } finally {
    busyAction.value = '';
  }
}

async function startBuild() {
  busyAction.value = 'build';
  error.value = '';
  message.value = '';
  try {
    job.value = await post('/api/ontology/build-run', {
      markdown_path: markdownPath.value,
      review_mode: reviewMode.value,
      reviewer: reviewer.value
    });
  } catch (cause) {
    error.value = cause.message;
  } finally {
    busyAction.value = '';
  }
}

async function refreshBuildStatus() {
  try {
    const wasRunning = job.value.running;
    job.value = await api('/api/ontology/build-status');
    if (wasRunning && !job.value.running) {
      if (job.value.error) error.value = job.value.error;
      else {
        sourcePath.value = 'ontologyBuild/ontology_candidates.json';
        statusFilter.value = 'pending_review_candidates';
        message.value = '抽取完成，审核队列已更新';
        await refresh();
      }
    }
  } catch (cause) {
    error.value = cause.message;
  }
}

async function decide(item, decision) {
  const draft = drafts[item.candidate_id];
  if (!draft) return;
  busyId.value = item.candidate_id;
  error.value = '';
  delete decisionErrors[item.candidate_id];
  try {
    await post('/api/ontology/review-decision', {
      candidate_id: item.candidate_id,
      decision,
      reviewer: reviewer.value,
      comment: draft.comment,
      modifications: decision === 'modify' ? candidateModifications(item, draft) : null
    });
    delete drafts[item.candidate_id];
    await refresh();
  } catch (cause) {
    error.value = cause.message;
    decisionErrors[item.candidate_id] = cause.message;
  } finally {
    busyId.value = '';
  }
}

function nameLabel(item) {
  if (item.candidate_type === 'mapping') return '原文提及';
  if (item.candidate_type === 'object_property') return '关系名';
  if (item.candidate_type === 'datatype_property' || item.candidate_type === 'constraint') return '属性名';
  return '名称';
}

function rangeLabel(item, draft) {
  return item.candidate_type === 'datatype_property'
    || item.candidate_type === 'constraint' && draft.propertyType === 'datatype_property'
    ? '数据类型' : 'range';
}

function qualityMessages(item) {
  const quality = item.review_quality || {};
  return [...new Set([
    ...(quality.approval_errors || []),
    ...(quality.issues || []).map(issue => issue.message)
  ])];
}

function updateWidth(clientX) {
  sidebarWidth.value = Math.min(Math.max(280, clientX), Math.max(360, window.innerWidth - 480));
}

function onPointerMove(event) {
  if (dragging) updateWidth(event.clientX);
}

function onPointerUp() {
  if (!dragging) return;
  dragging = false;
  document.body.classList.remove('resizing-sidebar');
  localStorage.setItem('ontologyBuild.sidebarWidth', String(sidebarWidth.value));
  window.removeEventListener('pointermove', onPointerMove);
  window.removeEventListener('pointerup', onPointerUp);
}

function startResize(event) {
  dragging = true;
  document.body.classList.add('resizing-sidebar');
  updateWidth(event.clientX);
  window.addEventListener('pointermove', onPointerMove);
  window.addEventListener('pointerup', onPointerUp);
}

function resizeByKeyboard(event) {
  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
  event.preventDefault();
  updateWidth(sidebarWidth.value + (event.key === 'ArrowRight' ? 20 : -20));
  localStorage.setItem('ontologyBuild.sidebarWidth', String(sidebarWidth.value));
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = undefined;
}

onActivated(() => {
  updateWidth(sidebarWidth.value);
  refresh();
  refreshBuildStatus();
  stopPolling();
  pollTimer = setInterval(() => {
    if (job.value.running) refreshBuildStatus();
  }, 4000);
});
onDeactivated(stopPolling);
onUnmounted(() => {
  stopPolling();
  onPointerUp();
  if (keywordTimer) clearTimeout(keywordTimer);
});
</script>

<template>
  <section class="review-page ontology-page">
    <div class="page-toolbar">
      <div class="page-title"><h1>本体抽取审核</h1><span class="subtle">{{ result.source_file || '候选本体元素' }}</span></div>
      <button class="icon-button" title="刷新审核结果" aria-label="刷新审核结果" @click="refresh"><RefreshCw :size="17" /></button>
    </div>
    <div v-if="error" class="notice error" role="alert">{{ error }}</div>
    <div v-else-if="message" class="notice success-notice" role="status">{{ message }}</div>
    <div class="page-body ontology-body" :style="sidebarStyle">
      <aside class="sidebar ontology-sidebar">
        <div class="sidebar-heading">候选生成与审核</div>
        <div class="control-stack">
          <label>Markdown 文档
            <select v-model="markdownPath">
              <option v-if="!markdownFiles.length" value="">暂无 Markdown 文档</option>
              <option v-for="path in markdownFiles" :key="path" :value="path">{{ path }}</option>
            </select>
          </label>
          <label>候选本体 JSON<input v-model="sourcePath" /></label>
          <div class="two-fields">
            <label>审核模式<select v-model="reviewMode"><option value="manual">人工审核</option><option value="hybrid">混合审核</option><option value="auto">自动审核</option></select></label>
            <label>审核人<input v-model="reviewer" /></label>
          </div>
          <button class="primary" :disabled="!!busyAction || job.running" @click="runReview">生成审核队列</button>
          <button :disabled="!markdownPath || !!busyAction || job.running" @click="startBuild"><Play :size="16" />抽取并进入审核</button>
          <div class="run-status">
            <span :class="['status-dot', job.running ? 'running' : job.exists && !job.error ? 'done' : job.exists ? 'failed' : '']"></span>
            {{ job.running ? '本体抽取中' : job.exists ? job.error ? '抽取失败' : '最近一次抽取完成' : '空闲' }}
          </div>
        </div>
      </aside>
      <div class="sidebar-resizer" role="separator" tabindex="0" aria-label="调整左侧栏宽度" aria-orientation="vertical" :aria-valuenow="sidebarWidth" aria-valuemin="280" @pointerdown="startResize" @keydown="resizeByKeyboard"></div>
      <div class="review-content ontology-content">
        <div class="stats-row">
          <div><span>总数</span><strong>{{ summary.total || 0 }}</strong></div>
          <div><span>可审核</span><strong>{{ summary.actionable_pending || 0 }}</strong></div>
          <div><span>待清理</span><strong>{{ summary.cleanup_pending || 0 }}</strong></div>
          <div><span>通过</span><strong>{{ summary.accepted || 0 }}</strong></div>
          <div><span>拒绝</span><strong>{{ summary.rejected || 0 }}</strong></div>
          <div><span>暂缓</span><strong>{{ summary.deferred || 0 }}</strong></div>
        </div>
        <div class="list-toolbar">
          <select v-model="statusFilter" aria-label="审核状态">
            <option value="pending_review_candidates">待人工审核</option>
            <option value="cleanup_candidates">待清理</option>
            <option value="accepted_candidates">已通过</option>
            <option value="rejected_candidates">已拒绝</option>
            <option value="deferred_candidates">已暂缓</option>
          </select>
          <input v-model="keyword" type="search" placeholder="搜索名称、类型、证据" aria-label="搜索本体候选项" />
          <span class="subtle">{{ total }} 项</span>
        </div>
        <div v-if="!visibleItems.length" class="empty-state">当前没有需要展示的本体候选项</div>
        <div v-else class="review-list">
          <article v-for="item in visibleItems" :key="item.candidate_id" class="review-card">
            <div class="candidate-title"><strong>{{ item.display_name || item.name || item.candidate_id }}</strong><span class="type-tag">{{ item.candidate_type || 'candidate' }}</span></div>
            <div class="meta-line">置信度 <strong>{{ item.confidence_display || '0.0000' }}</strong> · 状态 {{ item.final_decision || '-' }} · 推荐 {{ item.system_recommendation || '-' }}</div>
            <div v-if="item.source_module_text" class="meta-line">子本体模块 {{ item.source_module_text }} · 目录路径 {{ item.directory_context_text }}</div>
            <p v-if="item.recommendation_reasons?.length || item.reason || item.why_uncertain" class="reason">{{ (item.recommendation_reasons || []).join('；') || item.reason || item.why_uncertain }}</p>
            <div v-if="qualityMessages(item).length" class="quality-notes">{{ qualityMessages(item).join('；') }}</div>
            <template v-if="drafts[item.candidate_id]">
              <div class="edit-grid">
                <label>{{ nameLabel(item) }}<input v-model="drafts[item.candidate_id].name" /></label>
                <label v-if="item.candidate_type === 'class'">上位类<input v-model="drafts[item.candidate_id].parent" /></label>
                <label v-else-if="item.candidate_type === 'mapping'">映射目标<input v-model="drafts[item.candidate_id].mappedTo" /></label>
                <label v-else>domain<input v-model="drafts[item.candidate_id].domain" /></label>
                <label v-if="['object_property', 'datatype_property', 'constraint'].includes(item.candidate_type)">{{ rangeLabel(item, drafts[item.candidate_id]) }}<input v-model="drafts[item.candidate_id].range" /></label>
                <label v-if="item.candidate_type === 'constraint'">属性类型<select v-model="drafts[item.candidate_id].propertyType"><option value="object_property">对象属性</option><option value="datatype_property">数据属性</option></select></label>
                <label v-if="item.candidate_type === 'mapping'">映射类型<input v-model="drafts[item.candidate_id].mappedType" /></label>
                <label v-if="['class', 'object_property', 'datatype_property'].includes(item.candidate_type)" class="wide-field">定义<textarea v-model="drafts[item.candidate_id].definition" /></label>
              </div>
              <textarea v-model="drafts[item.candidate_id].comment" placeholder="审核备注" aria-label="审核备注"></textarea>
              <details class="evidence-details"><summary>证据与完整 JSON</summary><pre class="evidence-text">{{ item.evidence_text || '没有证据文本' }}</pre><pre class="evidence-text">{{ JSON.stringify(item, null, 2) }}</pre></details>
              <div class="decision-actions">
                <button class="success" :disabled="!!busyId || !!item.review_quality?.approval_errors?.length" :title="item.review_quality?.approval_errors?.length ? '请修改并补齐字段后通过' : ''" @click="decide(item, 'accept')"><Check :size="16" />通过</button>
                <button class="primary" :disabled="!!busyId" @click="decide(item, 'modify')"><Pencil :size="16" />修改后通过</button>
                <button class="danger" :disabled="!!busyId" @click="decide(item, 'reject')"><X :size="16" />拒绝</button>
                <button :disabled="!!busyId" @click="decide(item, 'defer')"><Clock3 :size="16" />暂缓</button>
              </div>
              <div v-if="decisionErrors[item.candidate_id]" class="decision-error" role="alert">{{ decisionErrors[item.candidate_id] }}</div>
            </template>
          </article>
        </div>
        <div v-if="total > pageSize" class="pagination">
          <span>第 {{ page }} / {{ pageCount }} 页，共 {{ total }} 项</span>
          <button class="icon-button" title="上一页" aria-label="上一页" :disabled="page <= 1" @click="page--"><ChevronLeft :size="17" /></button>
          <button class="icon-button" title="下一页" aria-label="下一页" :disabled="page >= pageCount" @click="page++"><ChevronRight :size="17" /></button>
        </div>
      </div>
    </div>
  </section>
</template>
