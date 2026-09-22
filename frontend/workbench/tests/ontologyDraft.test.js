import assert from 'node:assert/strict';
import test from 'node:test';
import { candidateModifications, draftForCandidate } from '../src/ontologyDraft.js';

test('initializes each candidate draft from its own fields', () => {
  assert.deepEqual(draftForCandidate({
    candidate_type: 'datatype_property',
    name: 'mass',
    range_candidate: 'Projectile',
    range_datatype: 'float'
  }), {
    name: 'mass', definition: '', parent: '', domain: '', range: 'float',
    propertyType: 'object_property', mappedTo: '', mappedType: '', comment: ''
  });
  assert.equal(draftForCandidate({ candidate_type: 'constraint', property_name: 'diameter' }).name, 'diameter');
  assert.equal(draftForCandidate({ candidate_type: 'mapping', text_mention: '弹丸' }).name, '弹丸');
});

const draft = {
  name: ' edited ', definition: ' definition ', parent: ' parent ',
  domain: ' domain ', range: ' range ', propertyType: 'object_property',
  mappedTo: ' target ', mappedType: ' class '
};

const cases = [
  ['class', { name: 'edited', parent_candidate: 'parent', definition_candidate: 'definition' }],
  ['object_property', { name: 'edited', domain_candidate: 'domain', range_candidate: 'range', definition_candidate: 'definition' }],
  ['datatype_property', { name: 'edited', domain_candidate: 'domain', range_datatype: 'range', definition_candidate: 'definition' }],
  ['constraint', { property_name: 'edited', property_type: 'object_property', domain_candidate: 'domain', range_candidate: 'range', range_datatype: '' }],
  ['mapping', { text_mention: 'edited', mapped_to: 'target', mapped_type: 'class' }]
];

for (const [candidate_type, expected] of cases) {
  test(`maps ${candidate_type} edits to the API fields`, () => {
    assert.deepEqual(candidateModifications({ candidate_type }, draft), expected);
  });
}

test('maps datatype constraints to range_datatype only', () => {
  assert.deepEqual(candidateModifications({ candidate_type: 'constraint' }, {
    ...draft, propertyType: 'datatype_property'
  }), {
    property_name: 'edited', property_type: 'datatype_property',
    domain_candidate: 'domain', range_candidate: '', range_datatype: 'range'
  });
});
