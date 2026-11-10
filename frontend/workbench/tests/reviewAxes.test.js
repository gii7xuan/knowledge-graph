import test from 'node:test';
import assert from 'node:assert/strict';
import { axisLabels, axisStatusLabel, filterCandidates, label, tone } from '../src/reviewMonitor.js';

test('four axes have separate vocabularies and legacy revision remains unclassified', () => {
  assert.equal(Object.keys(axisLabels).length, 4);
  assert.equal(axisStatusLabel('expression', 'lossless_normalization'), '可无损规范化');
  assert.equal(axisStatusLabel('source', 'supported'), '支持');
  assert.equal(axisStatusLabel('expression', 'legacy_revision'), '待区分修订类型');
  assert.equal(axisStatusLabel('mapping', undefined), '尚无结论');
});
test('normalization and semantic revision have distinct queues', () => {
  const items = [{ id: 'format', review_axes: { expression: { status: 'lossless_normalization' } } },
    { id: 'meaning', needs_revision: true, review_axes: { expression: { status: 'semantic_revision' } } }];
  assert.deepEqual(filterCandidates(items, 'normalization', '').map(i => i.id), ['format']);
  assert.deepEqual(filterCandidates(items, 'revision', '').map(i => i.id), ['meaning']);
  assert.notEqual(label('normalization_required'), label('semantic_revision_required'));
  assert.equal(tone('lossless_normalization'), 'warning');
});
test('mapping and scope filters remain independent', () => {
  const item = { review_axes: { mapping: { status: 'context_incomplete' }, scope: { status: 'exclude' } } };
  assert.equal(filterCandidates([item], 'mapping', '').length, 1);
  assert.equal(filterCandidates([item], 'scope', '').length, 1);
});
