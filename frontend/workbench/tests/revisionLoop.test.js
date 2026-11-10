import test from 'node:test';
import assert from 'node:assert/strict';
import { changeKinds, focusStatus, routeNames, displayValue } from '../src/revisionLoop.js';
test('preservation review and semantic review retain separate descriptions', () => {
  assert.notEqual(changeKinds.semantic_change, changeKinds.lossless_duplicate_cleanup);
  assert.match(changeKinds.semantic_change, /完整复核/);
});
test('a resolved dispute is labelled for that question and not as whole-candidate admission', () => {
  assert.match(focusStatus('resolved_supports'), /该争议/);
  assert.match(focusStatus('human_review'), /人工/);
  assert.notEqual(routeNames.supplement_source_context, routeNames.supplement_mapping_information);
  assert.equal(displayValue(null), '未填写');
});
