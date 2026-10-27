<script setup>
import { computed, onActivated, onDeactivated, onUnmounted, reactive, ref, watch } from 'vue';
import { Check, ChevronLeft, ChevronRight, Clock3, Pencil, Play, RefreshCw, X } from 'lucide-vue-next';
import MarkdownIt from 'markdown-it';
import { api, post } from '../api';
import ModelSelector from './ModelSelector.vue';
import { agentTimeline } from '../reviewAgentTimeline';

const props = defineProps({
  markdownFiles: { type: Array, default: () => [] },
  modelConfigs: { type: Array, default: () => [] },
  selectedModelId: { type: String, default: 'default' }
});
const emit = defineEmits(['update:selectedModelId', 'model-created']);
const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true });
const openEvidence = reactive(new Set());
const renderedEvidence = reactive({});
let markdownReady;
const files = ref([]);
const runs = ref([]);
const file = ref('');
const relation = ref('');
const minConfidence = ref('');
const maxConfidence = ref('');
const includeDone = ref(false);
const workflow = ref('');
const extensionDetails = reactive({});
const workflowLabels = { ready_for_review: '待审核', manual_review: '待人工审核', revision_required: '需修改候选', schema_unresolved: '本体问题待处理', awaiting_ontology_review: '等待本体审核', awaiting_ontology_release: '等待本体发布', reaudit_required: '需按新版本重审', review_completed: '审核完成', human_reviewed: '人工已处理' };
async function showExtension(id) {
  try { extensionDetails[id] = await api('/api/ontology/extensions/' + encodeURIComponent(id)); }
  catch (cause) { error.value = cause.message; }
}
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
const isV3 = computed(() => file.value.startsWith('v3:'));
const runId = computed(() => isV3.value ? file.value.slice(3) : '');
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
  const params = isV3.value
    ? new URLSearchParams({ run_id: runId.value })
    : new URLSearchParams({ file: file.value, include_done: includeDone.value ? '1' : '0' });
  if (relation.value) params.set(isV3.value ? 'relation_id' : 'relation', relation.value);
  if (!isV3.value && minConfidence.value !== '') params.set('min_confidence', minConfidence.value);
  if (!isV3.value && maxConfidence.value !== '') params.set('max_confidence', maxConfidence.value);
  if (isV3.value && !includeDone.value) params.set('status', 'pending');
  if (isV3.value && workflow.value) params.set('workflow_state', workflow.value);
  if (includePage) {
    params.set('page', String(page.value));
    params.set('page_size', String(pageSize));
  }
  return params.toString();
}

async function loadFiles() {
  try {
    const [legacyFiles, runPage] = await Promise.all([
      api('/api/triplets/files'), api('/api/triplets/runs?page_size=100')
    ]);
    runs.value = runPage.items || [];
    files.value = [...runs.value.map(item => `v3:${item.run_id}`), ...legacyFiles];
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
      const id = idFor(item);
      delete renderedEvidence[id];
      const triple = tripleFor(item);
      drafts[id] = {
        head: triple.head || '',
        head_type: triple.head_type || '',
        relation: triple.relation_label || triple.surface_relation || triple.relation_id || '',
        relation_id: triple.relation_id || '',
        original_relation: triple.relation_label || triple.surface_relation || triple.relation_id || '',
        original_relation_id: triple.relation_id || '',
        tail: triple.tail || '',
        tail_type: triple.tail_type || '',
        tail_kind: triple.tail_kind || 'entity', value: triple.value ?? '', datatype: triple.datatype || '', unit: triple.unit || null, language: triple.language || null,
        qualifiers: { ...triple.qualifiers }, qualifier_status: { ...triple.qualifier_status },
        evidence_mode: item.audit_summary?.status === 'completed' ? 'proposal' : 'source',
        selections: Object.fromEntries((item.audit_summary?.evidence_bundle?.items || []).map(ev => [ev.evidence_id, { selected: false, role: 'context', note: '', resolutions: '' }])),
        spans: [{ quote: '', occurrence: 1, role: 'support', note: '' }],
        triple_correct: null,
        evidence_support: 'insufficient',
        relation_mapping_correct: null,
        reason_code: '',
        comment: '',
        activeStartedAt: Date.now()
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
  const id = idFor(item);
  const draft = drafts[id];
  if (!draft) return;
  busyId.value = id;
  error.value = '';
  try {
    const corrected = decision === 'modify'
      ? (isV3.value ? {
          head: draft.head,
          head_type: draft.head_type || null,
          surface_relation: draft.relation,
          relation_label: draft.relation,
          relation_id: (
            draft.relation !== draft.original_relation && draft.relation_id === draft.original_relation_id
              ? null
              : (draft.relation_id || null)
          ),
          tail: draft.tail,
          tail_type: draft.tail_type || null,
          ...(item.contract_version === 'extraction_candidate_v4' ? {
            tail_kind: draft.tail_kind, value: draft.value, datatype: draft.datatype || null, unit: draft.unit || null, language: draft.language || null,
            qualifiers: Object.fromEntries(Object.entries(draft.qualifiers).map(([key, value]) => [key, value === '' ? null : value])),
            qualifier_status: draft.qualifier_status
          } : {})
        } : {
          head: draft.head,
          relation: draft.relation,
          relation_id: draft.relation_id || null,
          tail: draft.tail
        })
      : null;
    const reasonCode = draft.reason_code || ({
      accept: 'HUMAN_CONFIRMED', reject: 'FACT_INCORRECT',
      modify: 'REVISION_REQUIRED', defer: 'EVIDENCE_INSUFFICIENT'
    }[decision]);
    const factualReject = ['FACT_INCORRECT', 'NEGATION_IGNORED', 'DIRECTION_MISMATCH', 'CONDITION_MISSING', 'RELATION_MISMATCH', 'ENTITY_MISMATCH', 'SOURCE_CONTRADICTS'].includes(reasonCode);
    const labels = decision === 'accept'
      ? { valid_for_kg: 1, source_support: 'supported', relation_mapping_correct: draft.relation_mapping_correct }
      : decision === 'reject'
        ? { valid_for_kg: factualReject ? 0 : null, source_support: draft.evidence_support, relation_mapping_correct: draft.relation_mapping_correct }
        : decision === 'modify'
          ? { valid_for_kg: null, source_support: null, relation_mapping_correct: null }
          : { valid_for_kg: null, source_support: null, relation_mapping_correct: null };
    const base = isV3.value ? {
      run_id: runId.value,
      candidate_id: item.candidate_id,
      revision: item.revision,
      expected_row_version: item.row_version,
      audit_id: item.audit_summary?.status === 'completed' ? item.audit_summary.audit_id : null
    } : {
      file: file.value,
      review_id: item.review_id
    };
    if (isV3.value && decision === 'accept') {
      const selected = Object.entries(draft.selections).filter(([, s]) => s.selected);
      const bundleRequest = draft.evidence_mode === 'proposal' ? {
        audit_id: item.audit_summary?.audit_id,
        selected_round_id: item.audit_summary?.selected_round_id,
        selected_proposal_digest: item.audit_summary?.selected_proposal_digest,
        selected_evidence_hash: item.audit_summary?.selected_evidence_hash,
        evidence_ids: selected.map(([eid]) => eid),
        evidence_assessments: selected.map(([eid, s]) => ({ evidence_id: eid, role: s.role, note: s.note, resolution_evidence_ids: s.resolutions.split(/[,，\s]+/).filter(Boolean) }))
      } : { spans: draft.spans };
      const bundle = await post(`/api/triplets/candidates/${encodeURIComponent(item.candidate_id)}/human-bundles`, {
        revision: item.revision, expected_row_version: item.row_version, ...bundleRequest
      });
      base.human_bundle_id = bundle.bundle_id;
      base.audit_id = draft.evidence_mode === 'proposal' ? item.audit_summary?.audit_id : null;
    }
    await post('/api/triplets/decision', {
      ...base,
      decision,
      idempotency_key: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`,
      reason_code: reasonCode,
      comment: draft.comment,
      modifications: corrected,
      active_review_seconds: Math.max(0, Math.round((Date.now() - draft.activeStartedAt) / 1000)),
      annotation: labels
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
      output_stem: outputStem.value,
      model_id: props.selectedModelId
    });
    if (!job.value.running) await loadFiles();
  } catch (cause) {
    error.value = cause.message;
  } finally {
    extractionBusy.value = false;
  }
}

function updateSelectedModel(value) {
  emit('update:selectedModelId', value);
}

function onModelCreated(model) {
  emit('model-created', model);
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
  const id = idFor(item);
  if (!event.target.open) {
    openEvidence.delete(id);
    return;
  }
  openEvidence.add(id);
  if (renderedEvidence[id]) return;
  try {
    await loadMathRenderer();
    if (openEvidence.has(id)) {
      if (isV3.value && item.audit_summary?.evidence_bundle) {
        const audit = item.audit_summary;
        const contexts = await Promise.all((audit.evidence_bundle.items || []).map(ev => api(
          '/api/triplets/evidence/' + encodeURIComponent(ev.evidence_id) + '/context?' +
          new URLSearchParams({ audit_id: audit.audit_id, round_id: audit.final_round_id })
        )));
        renderedEvidence[id] = markdown.render(contexts.map(result =>
          '锚点状态：' + result.status + '\n\n' + result.context.map(s => s.text).join('\n')
        ).join('\n\n---\n\n') || '本轮没有可核验的原文证据。');
      } else {
        renderedEvidence[id] = markdown.render(item.evidence_chunk || item.extraction_quote || '未找到对应 source_id 的原文');
      }
    }
  } catch (cause) {
    error.value = `证据排版失败：${cause.message}`;
    renderedEvidence[id] = isV3.value ? '冻结原文上下文加载失败：' + cause.message
      : markdown.render(item.evidence_chunk || item.extraction_quote || '未找到对应 source_id 的原文');
  }
}

function rawConfidence(item) {
  const value = item.confidence_detail?.raw_confidence || item.confidence_detail || {};
  return typeof value === 'object' && value !== null ? value : {};
}

function bestEvidence(item) {
  if (isV3.value) return item.audit_summary?.evidence_bundle?.items?.[0] || {};
  return item.confidence_detail?.evidence_result?.best_evidence || {};
}

function validation(item) {
  return item.type_validation || item.confidence_detail?.type_validation || {};
}

function calibratedLabel(item) {
  const value = item.audit_summary?.calibrated_probability ?? item.calibrated_probability;
  return value === null || value === undefined ? '尚未校准' : Number(value).toFixed(4);
}

function votes(item) {
  return item.audit_summary?.votes || item.validator_votes || [];
}

function counterVoters(item, evidenceId) {
  return votes(item).filter(v => v.response_status === 'ok' && (v.counter_evidence_ids || []).includes(evidenceId)).map(v => v.voter_id).join('、');
}

function idFor(item) {
  return item.review_id || item.candidate_id;
}

function tripleFor(item) {
  return item.triple || item;
}

function sourceLabel(value) {
  if (!value.startsWith('v3:')) return value;
  const id = value.slice(3);
  const run = runs.value.find(item => item.run_id === id);
  return `v3 · ${run?.document_id || run?.source_path || id}`;
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
          <option v-for="name in files" :key="name" :value="name">{{ sourceLabel(name) }}</option>
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
        <ModelSelector
          :model-configs="modelConfigs"
          :selected-model-id="selectedModelId"
          :disabled="job.running || extractionBusy"
          @update:selected-model-id="updateSelectedModel"
          @model-created="onModelCreated"
        />
        <button class="primary run-button" :disabled="!markdownPath || job.running || extractionBusy" @click="startExtraction"><Play :size="16" />开始抽取</button>
      </div>
      <div class="run-status">
        <span :class="['status-dot', job.running ? 'running' : job.exists && job.returncode === 0 ? 'done' : job.exists ? 'failed' : '']"></span>
        {{ jobLabel }}
        <span v-if="job.exists">已接收 {{ job.counts?.accepted || 0 }} · 待审核 {{ job.counts?.rejected || 0 }} · 进程 {{ job.pid || '-' }}</span>
      </div>
      <div v-if="job.exists" class="output-paths">
        <span v-if="job.model?.model">model: <code>{{ job.model.name }} · {{ job.model.model }}</code></span>
          <span v-if="job.ontology_release_id" :title="job.ontology_release_id">本体版本：<code>{{ job.ontology_release_id }}</code></span>
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
          <label v-if="isV3">审核流程<select v-model="workflow" @change="applyFilters"><option value="">全部流程</option><option v-for="(label, state) in workflowLabels" :key="state" :value="state">{{ label }}</option></select></label>
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
          <article v-for="item in items" :key="idFor(item)" class="review-card">
            <div class="triple-line">
              <div class="triple-node"><strong>{{ tripleFor(item).head }}</strong><small>{{ tripleFor(item).head_type || item.head_type || 'Unknown' }}</small></div>
              <div class="triple-relation">{{ tripleFor(item).relation_label || tripleFor(item).surface_relation || item.corrected_relation || tripleFor(item).relation_id || item.relation }}</div>
              <div class="triple-node"><strong>{{ tripleFor(item).tail }}</strong><small>{{ tripleFor(item).tail_type || item.tail_type || 'Unknown' }}</small></div>
            </div>
            <div class="meta-line">校准概率 <strong>{{ calibratedLabel(item) }}</strong> · 旧机制分数 {{ item.confidence_display }} · 版本 {{ item.revision || 1 }} · 状态 {{ item.review_status || item.human_status || 'pending' }} · 来源 {{ item.source_id || item.source_chunk_id || '-' }}</div>
            <div v-if="item.contract_version === 'extraction_candidate_v4'" class="quality-notes">
              <div>完整限定：{{ JSON.stringify(tripleFor(item).qualifiers) }}</div>
              <div>限定识别状态：{{ JSON.stringify(tripleFor(item).qualifier_status) }}</div>
              <div>关系映射：{{ tripleFor(item).mapping_status }} · 身份：{{ item.fact_key_state === 'provisional' ? '待确认' : '已计算候选事实键，尚需审核' }}</div>
              <div v-if="tripleFor(item).tail_kind === 'literal'">字面量：{{ tripleFor(item).value }} · {{ tripleFor(item).datatype }} · {{ tripleFor(item).unit || '无单位' }} · {{ tripleFor(item).language || '未声明语言' }}</div>
              <div v-if="item.contract_issues?.length">待核对字段：{{ item.contract_issues.join('；') }}</div>
            </div>
            <p v-if="item.corrected_relation" class="reason">原关系：{{ item.relation }}，建议关系：{{ item.corrected_relation }}</p>
            <p v-if="item.discard_reason || item.confidence_decision?.reason" class="reason">{{ item.discard_reason || item.confidence_decision?.reason }}</p>
            <details class="evidence-details" @toggle="onEvidenceToggle($event, item)">
              <summary>评分细节和证据</summary>
              <div v-if="rawConfidence(item).logic_info" class="detail-line">{{ rawConfidence(item).logic_info }}</div>
              <div v-if="rawConfidence(item).map_info" class="detail-line">{{ rawConfidence(item).map_info }}</div>
              <div v-if="rawConfidence(item).evidence_info" class="detail-line">{{ rawConfidence(item).evidence_info }}</div>
              <div v-if="isV3" class="detail-line">冻结本体检查：{{ item.audit_summary?.ontology_check?.status || '未审核' }} · {{ (item.audit_summary?.ontology_check?.reason_codes || []).join('；') }}</div>
              <div v-if="isV3" class="detail-line">流程：{{ workflowLabels[item.workflow_state] || item.workflow_state }} · 原文共识：{{ item.audit_summary?.source_support_consensus?.result || '未形成' }} · 准入共识：{{ item.audit_summary?.admission_consensus?.result || '未形成' }}</div>
              <div v-if="item.pending_extension_id" class="detail-line">
                <button @click="showExtension(item.pending_extension_id)">查看本体扩展请求 {{ item.pending_extension_id }}</button>
                <p>等待本体处理；当前原文审核票不能用于新本体版本的准入。</p>
                <div v-if="extensionDetails[item.pending_extension_id]">任务状态：{{ extensionDetails[item.pending_extension_id].status }} · 基线 release：{{ extensionDetails[item.pending_extension_id].base_release_id }} · {{ extensionDetails[item.pending_extension_id].ontology_review_executed ? '本体审核已有执行记录' : '尚未执行本体审核' }}</div>
              </div>
              <details v-if="item.audit_summary?.issue_history?.length"><summary>问题处理记录</summary><p v-for="issue in item.audit_summary.issue_history" :key="issue.issue_id">{{ issue.code }}：{{ issue.status === 'resolved' ? '已解决' : (issue.requires_revision ? '需新修订' : '未解决') }} · {{ issue.description }} <span v-if="issue.resolution_rule">· {{ issue.resolution_rule }}</span></p></details>
              <div v-else class="detail-line">domain/range：{{ validation(item).passed === false ? (validation(item).errors || []).join('；') : '通过' }}</div>
              <div v-if="item.evidence || bestEvidence(item).text" class="detail-line">证据片段：{{ item.evidence || bestEvidence(item).text }}</div>
              <div v-if="bestEvidence(item).level" class="detail-line">证据层级：{{ bestEvidence(item).level }} · 位置 {{ bestEvidence(item).start }}-{{ bestEvidence(item).end }}</div>
              <div class="detail-line">引用定位：{{ bestEvidence(item).semantic_status === 'unknown' ? '已定位，语义待审核' : (bestEvidence(item).semantic_status || '未知') }}</div>
              <div v-if="votes(item).length" class="detail-line">
                模型意见：
                <span v-for="vote in votes(item)" :key="`${vote.round_id}-${vote.voter_id}`">
                  {{ vote.voter_id }}={{ vote.response_status === 'ok' ? vote.verdict : vote.response_status }}
                  {{ vote.error || '' }}
                </span>
              </div>
              <div v-if="item.audit_summary" class="detail-line">
                本轮审核：{{ item.audit_summary.audit_id }} · 提示 {{ item.audit_summary.versions?.prompt_version || '历史版本' }}
                · 契约 {{ item.audit_summary.versions?.response_contract_version || '未记录' }}
                <div v-for="ev in item.audit_summary.evidence_bundle?.items || []" :key="ev.evidence_id">
                  {{ ev.evidence_id }}：{{ ev.anchor_valid ? '原文锚点已核验，语义由模型审核' : ev.anchor_status || '未核验' }}
                  · {{ ev.retrieval_level }} · {{ ev.quote }}
                </div>
                <div v-for="ev in item.audit_summary.evidence_bundle?.rejected_items || []" :key="ev.evidence_id + ev.anchor_status">
                  排除证据 {{ ev.evidence_id }}：{{ ev.anchor_status }}
                </div>
              </div>
              <details v-if="isV3 && item.audit_summary?.planner_steps?.length" class="agent-timeline">
                <summary>Agent 调查过程（已保存记录）</summary>
                <p>配置策略 {{ item.audit_summary.configured_strategy }} → 实际策略 {{ item.audit_summary.executed_strategy }}</p>
                <ol>
                  <li v-for="event in agentTimeline(item.audit_summary)" :key="event.step">
                    <div>{{ event.role }} · 第 {{ event.step }} 步 · {{ event.action }} · {{ event.status }}</div>
                    <div>{{ event.purpose }}</div>
                    <div v-if="event.observation">
                      工具观察：{{ event.observation.status }}
                      <span v-if="event.observation.new_evidence_count != null"> · 新证据 {{ event.observation.new_evidence_count }} 条</span>
                      <span v-if="event.observation.error"> · {{ event.observation.error }}</span>
                      <span v-if="event.observation.validation"> · 提案 {{ event.observation.validation.proposal_digest }} · {{ event.observation.validation.consensus?.result }}</span>
                    </div>
                    <div v-else>无已确认的工具结果</div>
                  </li>
                </ol>
                <div v-for="issue in item.audit_summary.unresolved_issues || []" :key="`${issue.code}:${issue.description}`">待解决：{{ issue.description }}</div>
                <div v-for="(draft, n) in item.audit_summary.revision_drafts || []" :key="`draft-${n}`">模型修订建议（待人工确认，不覆盖候选）：{{ JSON.stringify(draft.changes) }}</div>
                <div>系统最终决定：{{ item.latest_decision?.decision || '尚未提交' }} · 停止原因 {{ item.audit_summary.stop_reason || '调查中' }}</div>
                <div v-if="item.audit_summary.agent_budget">实际模型调用 {{ item.audit_summary.agent_budget.usage?.llm_calls }} · 工具调用 {{ item.audit_summary.agent_budget.usage?.tool_calls }}</div>
              </details>
              <div v-if="openEvidence.has(idFor(item))" class="markdown-evidence" v-html="renderedEvidence[idFor(item)] || '正在排版证据...' "></div>
            </details>
            <template v-if="drafts[idFor(item)]">
              <div class="edit-grid">
                <label>head<input v-model="drafts[idFor(item)].head" /></label>
                <label v-if="isV3">head_type<input v-model="drafts[idFor(item)].head_type" /></label>
                <label>relation<input v-model="drafts[idFor(item)].relation" /></label>
                <label v-if="isV3">relation_id<input v-model="drafts[idFor(item)].relation_id" /></label>
                <label>tail<input v-model="drafts[idFor(item)].tail" /></label>
                <label v-if="isV3">tail_type<input v-model="drafts[idFor(item)].tail_type" /></label>
              </div>
              <div class="edit-grid">
                <label>三元组正确性<select v-model="drafts[idFor(item)].triple_correct"><option :value="null">未判断</option><option :value="1">正确</option><option :value="0">错误</option></select></label>
                <label>证据支持<select v-model="drafts[idFor(item)].evidence_support"><option value="insufficient">证据不足</option><option value="supported">完整支持</option><option value="contradicted">明确反驳</option></select></label>
                <label>关系映射<select v-model="drafts[idFor(item)].relation_mapping_correct"><option :value="null">未判断</option><option :value="1">正确</option><option :value="0">错误</option></select></label>
              </div>
              <details v-if="item.contract_version === 'extraction_candidate_v4'">
                <summary>修改完整限定与字面量（保存为新修订后重新审核）</summary>
                <div class="edit-grid">
                  <label v-for="key in ['polarity','condition','time_scope','applicability','modality','quantifier']" :key="key">{{ key }}
                    <input v-model="drafts[idFor(item)].qualifiers[key]" />
                    <select v-model="drafts[idFor(item)].qualifier_status[key]"><option value="extracted">原文明示</option><option value="not_stated">原文未述</option><option value="unknown">待确认</option></select>
                  </label>
                  <label>对象类型<select v-model="drafts[idFor(item)].tail_kind"><option value="entity">实体</option><option value="literal">字面量</option></select></label>
                  <template v-if="drafts[idFor(item)].tail_kind === 'literal'">
                    <label>值<input v-model="drafts[idFor(item)].value" /></label><label>数据类型<input v-model="drafts[idFor(item)].datatype" /></label>
                    <label>单位<input v-model="drafts[idFor(item)].unit" /></label><label>语言<input v-model="drafts[idFor(item)].language" /></label>
                  </template>
                </div>
              </details>
              <details v-if="isV3">
                <summary>接纳所依据的原文证据与人工判断</summary>
                <p>选择支持证据并填写判断依据。模型提出的反证也需保留并填写解决依据；表达不完整时请先修改。</p>
                <select v-model="drafts[idFor(item)].evidence_mode"><option v-if="item.audit_summary?.status === 'completed'" value="proposal">当前审核提案的证据</option><option value="source">从规范原文选择引文</option></select>
                <template v-if="drafts[idFor(item)].evidence_mode === 'proposal'">
                  <div v-for="ev in item.audit_summary?.evidence_bundle?.items || []" :key="ev.evidence_id">
                    <label><input v-model="drafts[idFor(item)].selections[ev.evidence_id].selected" type="checkbox" />{{ ev.evidence_id }}：{{ ev.quote }}</label>
                    <strong v-if="counterVoters(item, ev.evidence_id)">反证引用：{{ counterVoters(item, ev.evidence_id) }}；需保留并说明处置依据。</strong>
                    <template v-if="drafts[idFor(item)].selections[ev.evidence_id].selected">
                      <select v-model="drafts[idFor(item)].selections[ev.evidence_id].role"><option value="support">支持</option><option value="counter">反驳</option><option value="context">上下文</option><option value="irrelevant">无关</option></select>
                      <input v-model="drafts[idFor(item)].selections[ev.evidence_id].note" placeholder="原文如何支持或限定当前事实" />
                      <input v-model="drafts[idFor(item)].selections[ev.evidence_id].resolutions" placeholder="解决反证所引用的支持证据 ID，逗号分隔" />
                    </template>
                  </div>
                </template>
                <template v-else>
                  <div v-for="(span, n) in drafts[idFor(item)].spans" :key="n">
                    <textarea v-model="span.quote" placeholder="粘贴规范原文中的完整引文，服务器将核对位置" />
                    <label>第几次出现<input v-model.number="span.occurrence" type="number" min="1" /></label>
                    <select v-model="span.role"><option value="support">支持</option><option value="counter">反驳</option><option value="context">上下文</option><option value="irrelevant">无关</option></select>
                    <input v-model="span.note" placeholder="依据与适用范围说明" />
                    <button @click="drafts[idFor(item)].spans.splice(n, 1)">移除</button>
                  </div>
                  <button @click="drafts[idFor(item)].spans.push({quote:'',occurrence:1,role:'context',note:''})">添加引文</button>
                </template>
              </details>
              <label>原因
                <select v-model="drafts[idFor(item)].reason_code">
                  <option value="">按动作使用默认原因</option>
                  <option value="NEGATION_IGNORED">忽略否定</option>
                  <option value="CONDITION_MISSING">遗漏条件</option>
                  <option value="DIRECTION_MISMATCH">方向错误</option>
                  <option value="RELATION_MISMATCH">关系错误</option>
                  <option value="DUPLICATE">重复事实</option>
                  <option value="OUT_OF_SCOPE">超出范围</option>
                  <option value="EVIDENCE_INSUFFICIENT">证据不足</option>
                  <option value="SCHEMA_GAP">本体缺口（不表示原文事实错误）</option>
                  <option value="CONSTRAINT_VIOLATION">违反冻结本体约束</option>
                </select>
              </label>
              <textarea v-model="drafts[idFor(item)].comment" placeholder="审核备注" aria-label="审核备注"></textarea>
              <div class="decision-actions">
                <button class="success" :disabled="!!busyId" @click="decide(item, 'accept')"><Check :size="16" />通过</button>
                <button class="primary" :disabled="!!busyId" @click="decide(item, 'modify')"><Pencil :size="16" />保存修改并复审</button>
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
