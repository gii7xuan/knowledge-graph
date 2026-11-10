import test from 'node:test';
import assert from 'node:assert/strict';
import { mappingInformationLabel } from '../src/reviewMonitor.js';

test('not retrieved and missing definition in the bound release remain distinct', () => {
  assert.notEqual(mappingInformationLabel('definition_not_retrieved'), mappingInformationLabel('definition_missing_in_bound_release'));
  assert.match(mappingInformationLabel('no_relation_candidate_recalled'), /尚不能认定本体缺失/);
});
test('candidate identity and type assignment are separate information obligations', () => {
  assert.notEqual(mappingInformationLabel('candidate_identity_unverified'), mappingInformationLabel('endpoint_type_unassigned_or_ambiguous'));
});
