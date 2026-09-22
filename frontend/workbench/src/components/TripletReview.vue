<script setup>
import { computed, onActivated, onDeactivated, onUnmounted, reactive, ref, watch } from 'vue';
import { Check, ChevronLeft, ChevronRight, Clock3, Pencil, Play, RefreshCw, X } from 'lucide-vue-next';
import MarkdownIt from 'markdown-it';
import { api, post } from '../api';

const props = defineProps({ markdownFiles: { type: Array, default: () => [] } });
const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true });
const openEvidence = reactive(new Set());
const renderedEvidence = reactive({});
let markdownReady;
const files = ref([]);
const file = ref('');
const relation = ref('');
const minConfidence = ref('');
const maxConfidence = ref('');
const includeDone = ref(false);
const summary = ref({});
const items = ref([]);
const total = ref(0);
const page = ref(1);
const pageSize = 20;
const drafts = reactive({});
const loading = ref(false);
const busyId = ref('');
const error = ref('');
const extractionOpen = ref(false);
const markdownPath = ref('');
const outputStem = ref('');
const extractionBusy = ref(false);
const job = ref({ exists: false, running: false });
let pollTimer;
let requestId = 0;

watch(() => props.markdownFiles, value => {
  if (!markdownPath.value && value.length) selectMarkdown(value[0]);
}, { immediate: true });

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)));
const jobLabel = computed(() => {
  if (job.value.running) return '抽取中';
  if (!job.value.exists) return '空闲';
  return job.value.returncode === 0 ? '抽取完成' : `抽取失败 (${job.value.returncode})`;
});

function selectMarkdown(value) {
  markdownPath.value = value;
  let stem = value.split('/').pop().replace(/\.md$/i, '');
  if (stem.startsWith('MinerU_markdown_')) stem = stem.slice('MinerU_markdown_'.length);
  outputStem.value = stem || 'triplets';
}

function query(includePage = false) {
  const params = new URLSearchParams({ file: file.value, include_done: includeDone.value ? '1' : '0' });
  if (relation.value) params.set('relation', relation.value);
  if (minConfidence.value !== '') params.set('min_confidence', minConfidence.value);
  if (maxConfidence.value !== '') params.set('max_confidence', maxConfidence.value);
  if (includePage) {
    params.set('page', String(page.value));
    params.set('page_size', String(pageSize));
  }
  return params.toString();
}

async function loadFiles() {
  try {
    files.value = await api('/api/triplets/files');
    if (!files.value.includes(file.value)) file.value = files.value[0] || '';
    if (file.value) await loadAll();
  } catch (cause) {
    error.value = cause.message;
  }
}

async function loadAll() {
  if (!file.value) {
    items.value = [];
    summary.value = {};
    total.value = 0;
    return;
  }
  const current = ++requestId;
  loading.value = true;
  error.value = '';
  try {
    const [nextSummary, list] = await Promise.all([
      api('/api/triplets/summary?' + query()),
      api('/api/triplets/items?' + query(true))
    ]);
    if (current !== requestId) return;
    summary.value = nextSummary;
    items.value = list.items || [];
    total.value = list.total || 0;
    if (page.value > 1 && !items.value.length && total.value > 0) {
      page.value = pageCount.value;
      await loadAll();
      return;
    }
    for (const item of items.value) {
      drafts[item.review_id] = {
        head: item.head || '',
        relation: item.corrected_relation || item.relation || '',
        tail: item.tail || '',
        triple_correct: 1,
        evidence_support: 'full',
        relation_mapping_correct: 1,
        comment: ''
      };
    }
  } catch (cause) {
    if (current === requestId) error.value = cause.message;
  } finally {
    if (current === requestId) loading.value = false;
  }
}

function applyFilters() {
  page.value = 1;
  loadAll();
}

function selectFile() {
  relation.value = '';
  applyFilters();
}

function selectRelation(value) {
  relation.value = value;
  applyFilters();
}

function changePage(next) {
  page.value = Math.max(1, Math.min(pageCount.value, next));
  loadAll();
}

async function decide(item, decision) {
  const draft = drafts[item.review_id];
  if (!draft) return;
  busyId.value = item.review_id;
  error.value = '';
  try {
    const corrected = decision === 'modify' ? {
      head: draft.head,
      relation: draft.relation,
      tail: draft.tail
    } : null;
    await post('/api/triplets/decision', {
      file: file.value,
      review_id: item.review_id,
      decision,
      comment: draft.comment,
      modifications: corrected,
      annotation: {
        triple_correct: Number(draft.triple_correct),
        evidence_support: draft.evidence_support,
        relation_mapping_correct: Number(draft.relation_mapping_correct),
        corrected_triple: corrected
      }
    });
    await loadAll();
  } catch (cause) {
    error.value = cause.message;
  } finally {
    busyId.value = '';
  }
}

async function refreshJob() {
  try {
    const wasRunning = job.value.running;
    job.value = await api('/api/triplet/status');
    if (wasRunning && !job.value.running) await loadFiles();
  } catch (cause) {
    error.value = cause.message;
  }
}

async function startExtraction() {
  extractionBusy.value = true;
  error.value = '';
  try {
    job.value = await post('/api/triplet/start', {
      markdown_path: markdownPath.value,
      output_stem: outputStem.value
    });
    if (!job.value.running) await loadFiles();
  } catch (cause) {
    error.value = cause.message;
  } finally {
    extractionBusy.value = false;
  }
}

function loadMathRenderer() {
  if (!markdownReady) {
    markdownReady = Promise.all([
      import('markdown-it-texmath'),
      import('katex'),
      import('katex/dist/katex.min.css'),
      import('markdown-it-texmath/css/texmath.css')
    ]).then(([texmath, katex]) => markdown.use(texmath.default, {
      engine: katex.default,
      delimiters: ['dollars', 'brackets'],
      katexOptions: { throwOnError: false, strict: 'ignore' }
    })).catch(cause => {
      markdownReady = undefined;
      throw cause;
    });
  }
  return markdownReady;
}

async function onEvidenceToggle(event, item) {
  const id = item.review_id;
  if (!event.target.open) {
    openEvidence.delete(id);
    return;
  }
  openEvidence.add(id);
  if (renderedEvidence[id]) return;
  try {
    await loadMathRenderer();
    if (openEvidence.has(id)) {
      renderedEvidence[id] = markdown.render(item.evidence_chunk || '未找到对应 source_id 的原文');
    }
  } catch (cause) {
    error.value = `证据排版失败：${cause.message}`;
    renderedEvidence[id] = markdown.render(item.evidence_chunk || '未找到对应 source_id 的原文');
  }
}

function rawConfidence(item) {
  const value = item.confidence_detail?.raw_confidence || item.confidence_detail || {};
  return typeof value === 'object' && value !== null ? value : {};
}

function bestEvidence(item) {
  return item.confidence_detail?.evidence_result?.best_evidence || {};
}

function validation(item) {
  return item.type_validation || item.confidence_detail?.type_validation || {};
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = undefined;
}

onActivated(() => {
  loadFiles();
  refreshJob();
  stopPolling();
  pollTimer = setInterval(() => {
    if (job.value.running) refreshJob();
  }, 4000);
});
onDeactivated(stopPolling);
onUnmounted(stopPolling);
</script>

<template>
  <section class="review-page">
    <div class="page-toolbar">
      <div class="page-title"><h1>三元组抽取审核</h1><span class="subtle">{{ file || '暂无待审文件' }}</span></div>
      <div class="toolbar-actions">
        <select v-model="file" aria-label="待审核文件" @change="selectFile">
          <option v-if="!files.length" value="">暂无待审文件</option>
          <option v-for="name in files" :key="name" :value="name">{{ name }}</option>
        </select>
        <button class="icon-button" title="刷新审核列表" aria-label="刷新审核列表" @click="loadFiles"><RefreshCw :size="17" /></button>
        <button :class="{ selected: extractionOpen }" @click="extractionOpen = !extractionOpen"><Play :size="16" />三元组抽取</button>
      </div>
    </div>

    <section v-if="extractionOpen" class="run-panel" aria-label="三元组抽取">
      <div class="run-fields">
        <label>Markdown 文档
          <select :value="markdownPath" @change="selectMarkdown($event.target.value)">
            <option v-if="!markdownFiles.length" value="">暂无 Markdown 文档</option>
            <option v-for="path in markdownFiles" :key="path" :value="path">{{ path }}</option>
          </select>
        </label>
        <label>输出名称<input v-model="outputStem" /></label>
        <button class="primary run-button" :disabled="!markdownPath || job.running || extractionBusy" @click="startExtraction"><Play :size="16" />开始抽取</button>
      </div>
      <div class="run-status">
        <span :class="['status-dot', job.running ? 'running' : job.exists && job.returncode === 0 ? 'done' : job.exists ? 'failed' : '']"></span>
        {{ jobLabel }}
        <span v-if="job.exists">已接收 {{ job.counts?.accepted || 0 }} · 待审核 {{ job.counts?.rejected || 0 }} · 进程 {{ job.pid || '-' }}</span>
      </div>
      <div v-if="job.exists" class="output-paths">
        <span v-for="(path, name) in job.outputs" :key="name">{{ name }}: <code>{{ path }}</code></span>
      </div>
      <details v-if="job.exists"><summary>运行日志</summary><pre class="run-log">{{ job.log_tail || '等待输出' }}</pre></details>
    </section>

    <div v-if="error" class="notice error" role="alert">{{ error }}</div>
    <div class="page-body triplet-body">
      <aside class="sidebar triplet-sidebar">
        <div class="sidebar-heading">筛选条件</div>
        <div class="filter-grid">
          <label>最低置信度<input v-model="minConfidence" type="number" step="0.01" min="0" max="1" placeholder="0.00" @keyup.enter="applyFilters" /></label>
          <label>最高置信度<input v-model="maxConfidence" type="number" step="0.01" min="0" max="1" placeholder="1.00" @keyup.enter="applyFilters" /></label>
          <label>状态<select v-model="includeDone" @change="applyFilters"><option :value="false">待处理</option><option :value="true">全部</option></select></label>
          <button class="filter-button" @click="applyFilters">应用筛选</button>
        </div>
        <div class="sidebar-heading relations-heading">关系</div>
        <div class="relation-list">
          <button :class="{ active: relation === '' }" @click="selectRelation('')"><span>全部关系</span><span class="count">{{ summary.filtered || 0 }}</span></button>
          <button v-for="group in summary.groups || []" :key="group.relation" :class="{ active: relation === group.relation }" @click="selectRelation(group.relation)"><span>{{ group.relation }}</span><span class="count">{{ group.count }}</span></button>
        </div>
      </aside>

      <div class="review-content">
        <div class="stats-row">
          <div><span>总数</span><strong>{{ summary.total || 0 }}</strong></div>
          <div><span>待处理</span><strong>{{ summary.pending || 0 }}</strong></div>
          <div><span>已通过</span><strong>{{ summary.accepted || 0 }}</strong></div>
          <div><span>已拒绝</span><strong>{{ summary.rejected || 0 }}</strong></div>
          <div><span>暂缓</span><strong>{{ summary.deferred || 0 }}</strong></div>
          <div><span>筛选后</span><strong>{{ total }}</strong></div>
        </div>
        <div v-if="loading && !items.length" class="empty-state">正在读取审核数据...</div>
        <div v-else-if="!files.length" class="empty-state">没有找到待审文件</div>
        <div v-else-if="!items.length" class="empty-state">当前筛选条件下没有待审三元组</div>
        <div v-else class="review-list">
          <article v-for="item in items" :key="item.review_id" class="review-card">
            <div class="triple-line">
              <div class="triple-node"><strong>{{ item.head }}</strong><small>{{ item.head_type || 'Unknown' }}</small></div>
              <div class="triple-relation">{{ item.corrected_relation || item.relation }}</div>
              <div class="triple-node"><strong>{{ item.tail }}</strong><small>{{ item.tail_type || 'Unknown' }}</small></div>
            </div>
            <div class="meta-line">置信度 <strong>{{ item.confidence_display }}</strong> · 状态 {{ item.review_status || 'pending' }} · 来源 {{ item.source_id || '-' }}</div>
            <p v-if="item.corrected_relation" class="reason">原关系：{{ item.relation }}，建议关系：{{ item.corrected_relation }}</p>
            <p v-if="item.discard_reason || item.confidence_decision?.reason" class="reason">{{ item.discard_reason || item.confidence_decision?.reason }}</p>
            <details class="evidence-details" @toggle="onEvidenceToggle($event, item)">
              <summary>评分细节和证据</summary>
              <div v-if="rawConfidence(item).logic_info" class="detail-line">{{ rawConfidence(item).logic_info }}</div>
              <div v-if="rawConfidence(item).map_info" class="detail-line">{{ rawConfidence(item).map_info }}</div>
              <div v-if="rawConfidence(item).evidence_info" class="detail-line">{{ rawConfidence(item).evidence_info }}</div>
              <div class="detail-line">domain/range：{{ validation(item).passed === false ? (validation(item).errors || []).join('；') : '通过' }}</div>
              <div v-if="item.evidence || bestEvidence(item).text" class="detail-line">证据片段：{{ item.evidence || bestEvidence(item).text }}</div>
              <div v-if="bestEvidence(item).level" class="detail-line">证据层级：{{ bestEvidence(item).level }} · 位置 {{ bestEvidence(item).start }}-{{ bestEvidence(item).end }}</div>
              <div v-if="openEvidence.has(item.review_id)" class="markdown-evidence" v-html="renderedEvidence[item.review_id] || '正在排版证据...' "></div>
            </details>
            <template v-if="drafts[item.review_id]">
              <div class="edit-grid">
                <label>head<input v-model="drafts[item.review_id].head" /></label>
                <label>relation<input v-model="drafts[item.review_id].relation" /></label>
                <label>tail<input v-model="drafts[item.review_id].tail" /></label>
              </div>
              <div class="edit-grid">
                <label>三元组正确性<select v-model.number="drafts[item.review_id].triple_correct"><option :value="1">正确</option><option :value="0">错误</option></select></label>
                <label>证据支持<select v-model="drafts[item.review_id].evidence_support"><option value="full">完整支持</option><option value="partial">部分支持</option><option value="none">不支持</option></select></label>
                <label>关系映射<select v-model.number="drafts[item.review_id].relation_mapping_correct"><option :value="1">正确</option><option :value="0">错误</option></select></label>
              </div>
              <textarea v-model="drafts[item.review_id].comment" placeholder="审核备注" aria-label="审核备注"></textarea>
              <div class="decision-actions">
                <button class="success" :disabled="!!busyId" @click="decide(item, 'accept')"><Check :size="16" />通过</button>
                <button class="primary" :disabled="!!busyId" @click="decide(item, 'modify')"><Pencil :size="16" />修改后通过</button>
                <button class="danger" :disabled="!!busyId" @click="decide(item, 'reject')"><X :size="16" />拒绝</button>
                <button :disabled="!!busyId" @click="decide(item, 'defer')"><Clock3 :size="16" />暂缓</button>
              </div>
            </template>
          </article>
        </div>
        <div v-if="total > pageSize" class="pagination">
          <span>第 {{ page }} / {{ pageCount }} 页，共 {{ total }} 条</span>
          <button class="icon-button" title="上一页" aria-label="上一页" :disabled="page <= 1" @click="changePage(page - 1)"><ChevronLeft :size="17" /></button>
          <button class="icon-button" title="下一页" aria-label="下一页" :disabled="page >= pageCount" @click="changePage(page + 1)"><ChevronRight :size="17" /></button>
        </div>
      </div>
    </div>
  </section>
</template>
