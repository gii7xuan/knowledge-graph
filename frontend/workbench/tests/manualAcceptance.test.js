import assert from 'node:assert/strict';
import test from 'node:test';
import { prepareAcceptance, acceptanceErrorMessage, requiresScopeReview } from '../src/manualAcceptance.js';

function fixture() {
  const item = { triple: { head: 'A', tail: 'B', relation_id: 'R', surface_relation: 'connects' },
    audit_summary: { status: 'completed', audit_id: 'audit', selected_round_id: 2,
      selected_proposal_digest: 'proposal', selected_evidence_hash: 'evidence',
      evidence_bundle: { items: [{ evidence_id: 'ev_support' }, { evidence_id: 'ev_counter' }] }, votes: [] } };
  const draft = { evidence_mode: 'proposal', selections: {
    ev_support: { selected: false, role: 'context', note: '', resolutions: '' },
    ev_counter: { selected: false, role: 'context', note: '', resolutions: '' } } };
  return { item, draft };
}

test('clicking accept with default empty selection is blocked without changing human input', () => {
  const { item, draft } = fixture(); const before = structuredClone(draft);
  const result = prepareAcceptance(item, draft);
  assert.equal(result.ok, false); assert.equal(result.issues[0].field, 'evidence');
  assert.match(result.issues[0].message, /至少勾选/);
  assert.deepEqual(draft, before);
});
test('a checked row still requires explicit support and a note', () => {
  const { item, draft } = fixture(); draft.selections.ev_support.selected = true;
  const result = prepareAcceptance(item, draft);
  assert(result.issues.some(issue => issue.field === 'role:ev_support'));
  assert(result.issues.some(issue => issue.field === 'note:ev_support'));
});
test('valid human selection retains the exact proposal and all semantic assessments', () => {
  const { item, draft } = fixture();
  Object.assign(draft.selections.ev_support, { selected: true, role: 'support', note: ' Direct source statement. ' });
  const result = prepareAcceptance(item, draft);
  assert.equal(result.ok, true);
  assert.deepEqual(result.bundleRequest.evidence_ids, ['ev_support']);
  assert.equal(result.bundleRequest.selected_round_id, 2);
  assert.equal(result.bundleRequest.selected_proposal_digest, 'proposal');
  assert.equal(result.bundleRequest.selected_evidence_hash, 'evidence');
  assert.equal(result.bundleRequest.evidence_assessments[0].role, 'support');
  assert.equal(result.bundleRequest.evidence_assessments[0].note, 'Direct source statement.');
});
test('foreign IDs and stale proposal bindings cannot be submitted', () => {
  const { item, draft } = fixture(); item.audit_summary.selected_proposal_digest = '';
  draft.selections.foreign = { selected: true, role: 'support', note: 'explicit' };
  const result = prepareAcceptance(item, draft);
  assert.equal(result.ok, false); assert(result.issues.some(issue => issue.message.includes('不属于当前')));
});
test('material counterevidence cannot disappear or silently become support', () => {
  const { item, draft } = fixture();
  item.audit_summary.votes = [{ response_status: 'ok', counter_evidence_ids: ['ev_counter'] }];
  Object.assign(draft.selections.ev_support, { selected: true, role: 'support', note: 'matches' });
  assert(prepareAcceptance(item, draft).issues.some(issue => issue.field === 'evidence'));
  Object.assign(draft.selections.ev_counter, { selected: true, role: 'context', note: 'outside current scope' });
  assert(prepareAcceptance(item, draft).issues.some(issue => issue.field === 'resolution:ev_counter'));
  draft.selections.ev_counter.resolutions = 'ev_support';
  assert.equal(prepareAcceptance(item, draft).ok, true);
});
test('scope confirmation is required only for a nonempty semantic context', () => {
  const { item, draft } = fixture(); item.triple.semantic_context = {};
  assert.equal(requiresScopeReview(item), false);
  item.triple.semantic_context = { interpretation: 'conditioned statement' };
  Object.assign(draft.selections.ev_support, { selected: true, role: 'support', note: 'matches' });
  assert(prepareAcceptance(item, draft).issues.some(issue => issue.field === 'scope'));
  draft.annotation = { knowledge_scope: 'conditional', expression_status: 'faithful' };
  draft.annotation_note = 'The original scope is retained.';
  const result = prepareAcceptance(item, draft);
  assert.equal(result.ok, true); assert.equal(result.bundleRequest.scope_review.knowledge_scope, 'conditional');
});
test('source quotes need text, occurrence, role and notes and are never fabricated', () => {
  const { item, draft } = fixture(); draft.evidence_mode = 'source';
  draft.spans = [{ quote: '', occurrence: 1, role: 'support', note: '' }];
  assert.equal(prepareAcceptance(item, draft).ok, false);
  draft.spans[0] = { quote: 'A connects B.', occurrence: 2, role: 'support', note: 'Verified second occurrence.' };
  const result = prepareAcceptance(item, draft);
  assert.equal(result.ok, true); assert.equal(result.bundleRequest.spans[0].quote, 'A connects B.');
});
test('unsaved candidate changes require a new revision instead of accepting the original', () => {
  const { item, draft } = fixture(); draft.head = 'changed A';
  assert(prepareAcceptance(item, draft).issues.some(issue => issue.field === 'candidate'));
});
test('known server errors become actionable Chinese and conflicts still require refresh', () => {
  assert.match(acceptanceErrorMessage({ message: 'select nonempty evidence IDs from this exact candidate proposal' }), /至少勾选/);
  assert.match(acceptanceErrorMessage({ status: 409, message: 'changed' }), /版本已更新/);
  assert.equal(acceptanceErrorMessage({ message: 'Other deterministic rejection' }), 'Other deterministic rejection');
});
