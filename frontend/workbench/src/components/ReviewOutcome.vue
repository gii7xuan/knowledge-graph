<script setup>
defineProps({ result: { type: Object, default: null } });
const names = { source: '原文支持', expression: '表达规范', mapping: '本体映射', scope: '收录范围' };
const labels = { completed: '执行结束', partial_failure: '部分失败', timed_out: '执行超时', failed: '运行失败', interrupted: '执行中断', paused: '已暂停', running: '执行中', not_started: '尚未开始',
  supported: '支持', contradicted: '矛盾', insufficient: '证据不足', unresolved: '未解决', standard: '规范', lossless_normalization: '待无损规范化', semantic_revision: '需语义修订', valid: '有效', unassigned: '未分配', conflict: '冲突', context_incomplete: '上下文不完整', include: '收录', exclude: '排除', unclear: '边界不清',
  admit: '建议准入', revise: '建议修订后复核', wait_mapping: '等待映射', insufficient_evidence: '等待补证', manual_review: '转人工', none: '无写入动作', not_executed: '未执行', not_found: '未找到' };
const text = value => labels[value] || value;
const semanticLabels = { completed: '语义审核完成', waiting_mapping: '等待定义或映射', review_incomplete: '语义审核未完成', unresolved: '语义争议待处理' };
const humanLabels = { quality_sampling: '质量抽检', shared_definition_or_mapping_review: '集中审核定义或映射', technical_recovery: '技术恢复', domain_adjudication: '领域判断' };
const blockers = { required_committee_incomplete: '必需席位未齐', ontology_guard_not_passed: '本体约束尚未通过', candidate_identity_unresolved: '候选身份未确认', formal_mapping_not_bound_to_candidate: '正式映射尚未绑定当前候选', unresolved_dimension_disagreement: '维度意见仍有分歧', critical_issue_unresolved: '关键问题未解决', unresolved_claim_dispute: '具体争议未解决' };
const stages = { local_retrieval: '局部本体检索', global_retrieval: '全局本体与别名检索', relation_definitions: '关系定义获取', entity_identity: '实体身份解析', mapping_attempt: '映射判断', constraint_check: '约束检查' };
const blockerText = value => { const [name, state] = value.split(':'); return blockers[value] || (names[name] ? `${names[name]}：${text(state)}` : value); };
</script>

<template>
  <section v-if="result" class="review-outcome">
    <strong>{{ text(result.execution_status) }} · {{ text(result.recommendation) }} · {{ text(result.action_taken) }}</strong>
    <p>终态原因：{{ result.execution_reason }} · 完整审核：{{ result.review_completed ? '是' : '否' }}</p>
    <p v-if="result.semantic_status">{{ semanticLabels[result.semantic_status] || result.semantic_status }} · 技术记录{{ result.technical_status === 'settled' ? '已结清' : '待恢复或对账' }} · 所需处理：{{ humanLabels[result.required_human_action] || result.required_human_action }}</p>
    <small v-if="result.resolution_snapshot_id">当前结论包含经版本核验的定向复核或原回执；原始意见仍可查看。</small>
    <article v-for="card in result.human_problem_cards || []" :key="card.issue_id" class="problem-card">
      <b>待决定的问题：{{ card.question }}</b>
      <p>{{ card.candidate?.head }} — {{ card.candidate?.surface_relation }} — {{ card.candidate?.tail }}</p>
      <p v-if="card.unresolved_reason">尚未解决：{{ card.unresolved_reason }}</p>
      <small v-if="card.evidence_ids?.length">定位原文：{{ card.evidence_ids.join('、') }}</small>
      <blockquote v-for="source in card.source_material || []" :key="source.evidence_id">
        {{ source.role === 'counter' ? '反证' : source.role === 'support' ? '正面证据' : '上下文' }}：{{ source.quote }}
        <small>（{{ source.section_id || source.chunk_id }}，{{ source.start }}–{{ source.end }}）</small>
      </blockquote>
      <p v-for="definition in card.shared_definitions || []" :key="definition.task_id">共用定义 {{ definition.term_id }}：影响 {{ definition.affected_candidates }} 条候选，集中审核后重新核验。</p>
      <details><summary>已尝试步骤</summary><p v-for="(step, index) in card.attempted_steps" :key="index">{{ step.tool }}：{{ step.status }}</p></details>
    </article>
    <div class="outcome-grid">
      <article v-for="(axis, name) in result.dimension_results" :key="name">
        <b>{{ names[name] }}：{{ text(axis.status) }}</b>
        <span>{{ axis.executed ? '已取得意见' : '尚无可用意见' }} · 有效席位 {{ axis.valid_seats }}/{{ axis.required_seats }}</span>
        <span>{{ axis.judgment_complete ? '达到判定条件' : '尚未达到判定条件' }}</span>
        <small v-if="axis.missing_voter_ids?.length">缺失：{{ axis.missing_voter_ids.join('、') }}</small>
        <small v-if="axis.resolution_ids?.length">本维度已纳入独立的定向复核，原始意见保留在下方。</small>
        <details v-if="axis.observations?.length"><summary>查看各席意见和缺失原因</summary>
          <p v-for="seat in axis.observations" :key="seat.voter_id">{{ seat.voter_id }}：{{ seat.usable ? text(seat.opinion?.status) : seat.missing_reasons.join('；') }}</p>
        </details>
      </article>
    </div>
    <p v-if="result.blocking_reasons?.length">阻塞：{{ result.blocking_reasons.map(blockerText).join('；') }}</p>
    <details v-if="result.mapping_process"><summary>映射过程</summary>
      <p v-for="stage in result.mapping_process.stages" :key="stage.stage">{{ stages[stage.stage] || stage.stage }}：{{ text(stage.status) }}</p>
    </details>
    <details v-if="result.technical_recovery_tasks?.length"><summary>技术恢复与用量待办（{{ result.technical_recovery_tasks.length }} 项）</summary>
      <p v-for="task in result.technical_recovery_tasks" :key="task.attempt_id">{{ task.task_type === 'planner_action' ? '规划请求' : '审核请求' }}：{{ task.execution_unknown ? '执行结果未知，保留原请求' : '结果已知，用量待核对' }}</p>
    </details>
    <small>当前派生诊断 {{ result.version }}。部分维度意见用于定位和补审，完整审核仍按冻结的三席门槛判断。</small>
  </section>
</template>

<style scoped>
.review-outcome{padding:16px;margin:12px 0;background:#f5f8fb;border:1px solid #cfdae5;border-radius:8px;overflow-wrap:anywhere}
.review-outcome p{margin:8px 0}.outcome-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}.outcome-grid article{display:flex;flex-direction:column;gap:6px;padding:10px;background:white;border-radius:5px}.review-outcome small{color:#586779}summary{cursor:pointer}
</style>
