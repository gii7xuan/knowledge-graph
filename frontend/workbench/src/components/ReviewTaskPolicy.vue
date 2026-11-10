<script setup>
defineProps({ policy: Object, failure: Object, clarifications: { type: Array, default: () => [] }, current: Boolean, failurePolicy: String });
</script>
<template>
  <section v-if="policy || failure || clarifications.length" class="task-policy" aria-label="收录规则与故障处理">
    <h3>{{ current ? '新任务收录规则' : '本次审核收录规则与故障处理' }}</h3>
    <p v-if="policy">{{ policy.task_goal || '历史任务仅记录排除类别，未提供完整边界说明。' }}</p>
    <p v-if="policy">策略：{{ policy.version }}。模型识别类别并引用原文，程序决定是否收录；类别不明或席位不完整时等待复核。范围排除不代表事实错误。</p>
    <details v-if="policy?.categories"><summary>查看类别边界与收录配置</summary>
      <div v-for="(category, key) in policy.categories" :key="key"><strong>{{ category.label }} · {{ key === 'unresolved' ? '等待复核' : policy.excluded_scopes.includes(key) ? '保留来源，不入主图' : '属于收录范围' }}</strong><p>{{ category.boundary }}</p></div>
    </details>
    <p v-if="failurePolicy === 'bounded_minimum_v1'">规划输出格式异常最多重试一次；仍失败则执行固定最小审核。关键义务未满足时标为“审核未完成”。</p>
    <div v-if="failure"><strong>{{ failure.historical ? '历史规划器故障记录' : '本次已执行故障兜底' }}</strong><p>{{ failure.reason }}</p>
      <p v-if="failure.historical">此历史任务未执行新的最小审核兜底；{{ failure.independent_review_completed ? '完整度按原记录核验。' : '独立审核未完成。' }}</p>
      <p v-else>最小审核已执行 {{ failure.minimum_steps || 0 }} 步。审核结论以独立席位、原文和映射状态为准。</p>
      <p v-if="!failure.historical && failure.minimum_review_status === 'incomplete'"><strong>审核未完成：</strong>仍有席位或原文、表达、本体映射、范围信息需要补齐，不能仅凭收齐模型票结束审核。</p>
    </div>
    <article v-for="note in clarifications" :key="note.voter_id">
      <strong>{{ note.voter_id }} · 内部矛盾待人工澄清</strong><p v-for="reason in note.conflicts" :key="reason">{{ reason }}</p>
      <p>{{ note.clarification?.answer?.explanation || '尚无有效澄清结果；原始判断已保留。' }}</p>
      <p>澄清单独存档，不改写原判断、不计作通过票。仍需人工处理或对新版本重新审核。</p>
    </article>
  </section>
</template>
<style scoped>
.task-policy { margin: 12px 0; padding: 14px; border: 1px solid #dce3eb; border-radius: 8px; background: #f8fafc; font-size: 13px; }
h3 { margin: 0; font-size: 14px; } p { margin: 8px 0; line-height: 1.7; overflow-wrap: anywhere; color: #526074; }
details div,article { padding-top: 12px; } summary { cursor: pointer; }
</style>
