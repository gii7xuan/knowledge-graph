import test from 'node:test';
import assert from 'node:assert/strict';
import { qualityMetricValue } from '../src/reviewMonitor.js';

test('missing quality truth is unevaluable while measured zero remains zero', () => {
  for (const value of [null, undefined, NaN, Infinity, '0', -1, 2]) assert.equal(qualityMetricValue(value), '暂不可评估');
  assert.equal(qualityMetricValue(0), '0.0%');
  assert.equal(qualityMetricValue(1), '100.0%');
});
