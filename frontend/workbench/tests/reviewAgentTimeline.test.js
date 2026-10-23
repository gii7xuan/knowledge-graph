import assert from 'node:assert/strict';
import test from 'node:test';
import { agentTimeline } from '../src/reviewAgentTimeline.js';

test('planner suggestion remains distinct from an observed tool result', () => {
  const events = agentTimeline({planner_steps: [
    {step_no: 1, status: 'ok', action: {action: 'search_evidence', rationale_summary: '查找条件'}},
    {step_no: 2, status: 'failed', error: 'timeout'}
  ], tool_calls: [{step_no: 1, status: 'empty', new_evidence_count: 0}]});
  assert.equal(events[0].role, '模型建议');
  assert.equal(events[0].observation.status, 'empty');
  assert.equal(events[1].observation, null);
  assert.equal(events[1].action, '未产生有效动作');
});

test('fallback is labelled as a system action, not successful model planning', () => {
  const events = agentTimeline({planner_steps: [{step_no: 4, role: 'system', action: {action: 'finish_review'}}]});
  assert.equal(events[0].role, '系统降级动作');
  assert.deepEqual(agentTimeline(null), []);
  assert.deepEqual(agentTimeline({}), []);
});
