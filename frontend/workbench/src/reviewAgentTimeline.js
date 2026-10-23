// Render persisted events only; a model action never masquerades as a tool result.
export function agentTimeline(audit) {
  if (!audit) return [];
  const calls = new Map((audit.tool_calls || []).map(call => [call.step_no, call]));
  return (audit.planner_steps || []).map(step => ({
    step: step.step_no,
    role: step.role === 'system' ? '系统降级动作' : '模型建议',
    action: step.action?.action || '未产生有效动作',
    purpose: step.action?.rationale_summary || step.error || '',
    status: step.status,
    arguments: step.action?.arguments || {},
    observation: calls.get(step.step_no) || null
  }));
}
