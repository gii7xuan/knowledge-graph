import assert from 'node:assert/strict';
import test from 'node:test';
import { dimensionLabels, requestLabels, tone, label, filterCandidates, outcome, formatTime } from '../src/reviewMonitor.js';

test('nine dimensions are distinct from request execution states', () => {
  assert.equal(Object.keys(dimensionLabels).length, 9);
  assert.match(requestLabels.outcome_unknown, /待对账/);
  assert.notEqual(requestLabels.outcome_unknown, label('contradicted'));
  assert.equal(tone('contradicted'), 'danger');
  assert.equal(tone('outcome_unknown'), 'warning');
});
test('late response is labelled pending original acceptance', () => {
  assert.match(requestLabels.validated_pending_commit, /待原轮采纳/);
  const value = outcome({ slots: [{ state: 'validated_pending_commit' }], decision: {}, three_seats_ready: false });
  assert.match(value.title, /尚无本轮/);
});
test('three recorded seats are not automatically admitted', () => {
  assert.match(outcome({ slots: [], decision: {}, three_seats_ready: true }).detail, /等待原审核流程汇总/);
});
test('actual program revision decision remains visible despite three valid seats', () => {
  const value = outcome({ slots: [], three_seats_ready: true, decision: { workflow_state: 'revision_required', source_support_result: 'supported', admission_consensus: { result: 'unresolved' } } });
  assert.equal(value.title, '需修订');
});
test('historical decision never becomes current admission', () => {
  assert.equal(outcome({ historical: true, slots: [], decision: { workflow_state: 'auto_accept' } }).title, '历史审核记录');
});
test('filters distinguish processing ended from independent review complete', () => {
  const items = [{ sample_id: 'DEV-001', status: 'completed', independent_review_completed: false },
    { sample_id: 'DEV-002', status: 'completed', independent_review_completed: true, has_disagreement: true }];
  assert.equal(filterCandidates(items, 'incomplete', '').length, 1);
  assert.equal(filterCandidates(items, 'disagreement', '002').length, 1);
  assert.equal(filterCandidates(items, 'running', '').length, 0);
});
test('malformed status is not silently coerced into a passing opinion', () => {
  assert.equal(label(['pass']), '字段类型异常');
  assert.equal(label(null), '尚无记录');
  assert.equal(formatTime('invalid time'), '未记录时间');
});
