<script setup>
import { computed } from 'vue';
import { mappingInformationLabel } from '../reviewMonitor.js';
const props = defineProps({ report: Object, evidence: { type: Array, default: () => [] } });
const original = computed(() => props.report?.original_expression || {});
const formal = computed(() => props.report?.formal_mapping || {});
const sourceIds = computed(() => new Set(Object.values(props.report?.entities || {}).flatMap(e => e.source_evidence_ids || [])));
const source = computed(() => props.evidence.filter(e => sourceIds.value.has(e.evidence_id)));
</script>

<template>
  <section v-if="report" class="mapping-preflight" aria-label="映射信息预检">
    <div class="mapping-preflight-title"><h3>映射信息预检与补充检索</h3>
      <strong>{{ report.judgment_ready ? '信息齐全，待语义核验' : '仍需补充映射信息' }}</strong>
    </div>
    <p v-if="report.post_run_diagnostic" class="mapping-note">这是本轮结束后的独立诊断，使用原候选、原证据和绑定本体；历史审核结论保持原样。</p>
    <div class="mapping-expression-grid">
      <div><strong>原始抽取表达 · 版本 {{ original.revision }}</strong>
        <p>{{ original.head }} — {{ original.surface_relation }} — {{ original.tail }}</p>
        <small>保留原谓词和限定，不用近义正式关系替换原意。</small>
      </div>
      <div><strong>正式映射记录</strong>
        <p>已填写关系 ID：{{ formal.assigned_relation_id || '未分配' }}</p>
        <small>{{ formal.semantic_approval ? '以正式审核结论为准' : '候选 ID 和名称命中不代表映射已获确认。' }}</small>
      </div>
    </div>
    <ul v-if="report.blocking_information?.length">
      <li v-for="(item, index) in report.blocking_information" :key="index">{{ item.id || '关系候选' }}：{{ item.fields.map(mappingInformationLabel).join('；') }}</li>
    </ul>
    <details>
      <summary>检索过程与关系候选（{{ report.relation_candidates?.length || 0 }}）</summary>
      <p v-for="(step, index) in report.search_trace" :key="index">
        {{ step.stage === 'review_subset' ? '先检查本轮本体片段' : step.stage === 'full_bound_release_and_aliases' ? '补检绑定版本的完整本体及别名索引' : '补检预算检查' }}
        <span v-if="step.properties_scanned != null"> · 已扫描 {{ step.properties_scanned }} 个关系、{{ step.classes_scanned }} 个类</span>
        <span v-if="step.complete === false"> · 未完整纳入，请补充上下文</span>
      </p>
      <div v-for="item in report.relation_candidates" :key="item.id" class="mapping-relation">
        <strong>{{ item.label || item.id }}</strong> · {{ item.id }}
        <p>{{ item.definition_available ? '定义存在' : '绑定发布版本缺少定义正文' }} · {{ item.retrieved ? '已进入审核片段' : '尚未进入审核片段' }}</p>
        <small>形式方向：头实体 → {{ item.property_kind === 'data' ? '字面量' : '尾实体' }}；domain：{{ item.domain?.join?.('、') || '未声明限制' }}；range：{{ item.range?.join?.('、') || item.datatype || '未声明限制' }}</small>
      </div>
      <p v-for="group in report.confusable_relations" :key="group.query">同名或别名竞争关系：{{ group.candidate_ids.join('、') }}。需比较定义、方向和端点约束，不按名称自动选定。</p>
    </details>
    <details>
      <summary>实体身份、类型与来源语境</summary>
      <div v-for="(item, side) in report.entities" :key="side">
        <p><strong>{{ side === 'head' ? '头实体' : '尾实体' }}：{{ item.surface }}</strong> · {{ item.candidate_id || '候选身份未核验' }}</p>
        <p>已填类型：{{ item.assigned_type || '未分配' }}；类型候选：{{ item.type_candidate_ids.join('、') || '未召回' }}；别名线索：{{ item.aliases.join('、') || '无已提供线索' }}</p>
        <small>来源：{{ item.document_id }} · {{ item.document_version }}；同名对象不自动合并。</small>
      </div>
      <blockquote v-for="item in source" :key="item.evidence_id"><small>{{ item.evidence_id }}</small><p>{{ item.quote }}</p></blockquote>
    </details>
    <p class="mapping-note">已扫描目录不代表已证明本体缺口。信息不完整时保持待映射，原文支持意见独立保留。</p>
  </section>
</template>

<style scoped>
.mapping-preflight { margin: 14px 0; border: 1px solid #dce3eb; border-radius: 8px; padding: 14px; font-size: 13px; }
.mapping-preflight-title { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
h3 { margin: 0; font-size: 14px; }
.mapping-preflight-title > strong { color: #8a611d; font-size: 12px; }
.mapping-expression-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin: 14px 0; }
.mapping-expression-grid > div { padding: 12px; background: #f6f8fa; border-radius: 6px; overflow-wrap: anywhere; }
p, li { line-height: 1.7; }
details { margin: 12px 0; }
.mapping-relation { border-bottom: 1px solid #e7ebef; padding: 10px 0; }
.mapping-note, small { color: #657084; line-height: 1.65; }
blockquote { margin: 12px 0; padding-left: 12px; border-left: 3px solid #b8d7d7; }
@media (max-width: 700px) { .mapping-expression-grid { grid-template-columns: 1fr; } }
</style>
