import test from 'node:test';
import assert from 'node:assert/strict';
import { meaningEffectLabel, ruleTitle, normalizationLabel } from '../src/fieldStandards.js';

test('meaning preservation, change and uncertainty remain distinct', () => {
  assert.equal(new Set(['preserved', 'changed', 'unresolved'].map(meaningEffectLabel)).size, 3);
  assert.match(meaningEffectLabel(null), /旧版/);
});
test('frequency migration and duplicate cleanup explain what remains', () => {
  assert.match(normalizationLabel({ operation: 'move_frequency', source_field: 'applicability', value: '常常' }), /适用范围 → 频率.*常常/);
  assert.match(normalizationLabel({ operation: 'remove_duplicate_frequency', value: '总是' }), /独立频率仍保留/);
  assert.equal(ruleTitle({ rules: [{ id: 'FREQ-001', title: '频率与适用范围分开' }] }, 'FREQ-001'), '频率与适用范围分开');
});
