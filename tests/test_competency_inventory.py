"""No network: exhaustive ID gates, citation integrity, historical compatibility."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from ontology_review import coverage_inventory as coverage
from ontology_review import semantic_competency as semantic
from review_agent.contracts import sha256_json
from review_agent.llm_clients import MockLLMClient
from ontology_review.contracts import proposal_digest
from tests import test_semantic_competency as semantic_fixtures


class CoverageInventoryTests(unittest.TestCase):
    def setUp(self):
        self.entries = [{'id': 'C_A', 'schema_kind': 'class', 'label': 'A', 'description': 'A fixture concept'},
                        {'id': 'R_AB', 'schema_kind': 'object_property', 'label': 'regulates',
                         'description': 'A regulates B', 'domain': ['C_A'], 'range': ['C_B']}]
        self.request = {'task_type': 'ontology_competency_review', 'task_id': 'task', 'object_revision': 1,
            'parent_proposal_digest': 'digest', 'base_release_id': 'base', 'delta_hash': 'delta',
            'response_contract_version': coverage.VERSION, 'coverage_inventory': deepcopy(self.entries),
            'coverage_inventory_hash': sha256_json(self.entries),
            'questions': [{'id': 'q', 'text': 'Are all frozen items grounded?'}],
            'source_documents': [{'document_id': 'doc', 'text': 'A regulates B. This is the complete frozen fixture.'}],
            'committee_members': [{'id': c, 'model_id': c, 'model_family': c} for c in ['a', 'b', 'c']],
            'committee_policy': {'committee_size': 3, 'required_votes': 3, 'minimum_distinct_families': 3}}
        self.request['coverage_contract_hash'] = coverage.contract_hash(semantic.SCHEMA, self.request)
        self.request['request_hash'] = sha256_json(self.request)
        self.answer = {'coverage': 'complete', 'unresolved_terms': [],
            'assessments': [{'question_id': 'q', 'status': 'pass', 'quotes': [{'document_id': 'doc', 'quote': 'A regulates B.'}], 'note': 'fixture'}],
            'coverage_matrix': {identity: ['pass', 'pass', 'pass', ['quote1']] for identity in ['C_A', 'R_AB']},
            'quotation_catalog': [{'quotation_id': 'quote1', 'document_id': 'doc', 'quote': 'A regulates B.'}],
            'item_issues': {}, 'definition_gaps': []}

    def test_complete_exact_matrix_and_quotes_are_required_by_schema_and_validator(self):
        self.assertEqual(semantic.errors(self.answer, self.request), [])
        schema = semantic.response_schema(self.request)
        self.assertEqual(set(schema['properties']['coverage_matrix']['required']), {'C_A', 'R_AB'})
        for key in ['C_A', 'invented']:
            bad = deepcopy(self.answer)
            if key == 'C_A': del bad['coverage_matrix'][key]
            else: bad['coverage_matrix'][key] = ['pass'] * 3 + [['quote1']]
            self.assertTrue(semantic.errors(bad, self.request))

    def test_global_only_answer_and_single_changed_item_cannot_count_as_full_coverage(self):
        bad = {k: self.answer[k] for k in semantic.SCHEMA['required']}
        self.assertTrue(semantic.errors(bad, self.request))
        bad = deepcopy(self.answer); bad['coverage_matrix'] = {'R_AB': bad['coverage_matrix']['R_AB']}
        self.assertTrue(semantic.errors(bad, self.request))

    def test_citations_cannot_be_fabricated_ambiguous_or_unreferenced(self):
        for kind in ['wrong_quote', 'wrong_id', 'empty_reference', 'duplicate_id']:
            bad = deepcopy(self.answer)
            if kind == 'wrong_quote': bad['quotation_catalog'][0]['quote'] = 'A invents B.'
            elif kind == 'wrong_id': bad['coverage_matrix']['C_A'][3] = ['missing']
            elif kind == 'empty_reference': bad['coverage_matrix']['C_A'][3] = []
            else: bad['quotation_catalog'] *= 2
            self.assertTrue(semantic.errors(bad, self.request), kind)
        request = deepcopy(self.request); request['source_documents'][0]['text'] += ' A regulates B.'
        self.assertTrue(semantic.errors(self.answer, request))

    def test_unknown_or_fail_requires_a_specific_issue_and_cannot_be_global_pass(self):
        bad = deepcopy(self.answer); bad['coverage_matrix']['R_AB'][2] = 'unknown'
        self.assertTrue(semantic.errors(bad, self.request))
        bad['item_issues'] = {'R_AB': 'Direction has not been established.'}
        self.assertTrue(semantic.errors(bad, self.request))
        bad['assessments'][0]['status'] = 'unknown'
        self.assertEqual(semantic.errors(bad, self.request), [])

    def test_metadata_gap_cannot_be_hidden_or_confused_with_source_absence(self):
        request = deepcopy(self.request); request['coverage_inventory'][0]['description'] = ''
        request['coverage_inventory_hash'] = sha256_json(request['coverage_inventory'])
        self.assertTrue(semantic.errors(self.answer, request))
        answer = deepcopy(self.answer); answer['definition_gaps'] = ['C_A']
        self.assertEqual(semantic.errors(answer, request), [])
        answer['definition_gaps'] *= 2
        self.assertTrue(semantic.errors(answer, request))

    def test_corrupted_inventory_or_malformed_rows_are_rejected(self):
        request = deepcopy(self.request); request['coverage_inventory_hash'] = 'corrupted'
        self.assertTrue(semantic.errors(self.answer, request))
        for row in [[], ['pass'] * 3, ['pass', {}, 'pass', []], ['pass', 'pass', 'pass', [[], 'quote1']]]:
            bad = deepcopy(self.answer); bad['coverage_matrix']['C_A'] = row
            self.assertTrue(semantic.errors(bad, self.request))

    def test_valid_failure_is_preserved_as_a_valid_vote_and_never_becomes_approval(self):
        answer = deepcopy(self.answer)
        answer['coverage_matrix']['R_AB'][2] = 'fail'
        answer['item_issues'] = {'R_AB': 'Fixture argument direction is wrong.'}
        answer['assessments'][0]['status'] = 'fail'
        votes = [semantic.judge_once({**member, 'client': MockLLMClient([answer])}, self.request)
                 for member in self.request['committee_members']]
        self.assertTrue(all(v['response_status'] == 'ok' for v in votes))
        result = semantic.aggregate(votes, self.request)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(len(result['eligible_voter_ids']), 3)
        self.assertEqual(result['passed_voter_ids'], [])

    def test_three_bound_distinct_seats_can_pass_but_tampered_cached_matrix_cannot(self):
        votes = [semantic.judge_once({**member, 'client': MockLLMClient([deepcopy(self.answer)])}, self.request)
                 for member in self.request['committee_members']]
        self.assertEqual(semantic.aggregate(votes, self.request)['status'], 'pass')
        del votes[0]['coverage_matrix']['C_A']
        result = semantic.aggregate(votes, self.request)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(len(result['eligible_voter_ids']), 2)

    def test_historical_schema_prompt_and_response_remain_exact(self):
        request = deepcopy(self.request); request['response_contract_version'] = semantic.VERSION
        answer = {k: self.answer[k] for k in semantic.SCHEMA['required']}
        self.assertIs(semantic.response_schema(request), semantic.SCHEMA)
        self.assertEqual(semantic.prompt_for(request), semantic.PROMPT)
        self.assertEqual(semantic.errors(answer, request), [])
        self.assertNotIn('coverage_matrix', semantic.SCHEMA['required'])

    def test_request_builder_opt_in_binds_complete_schema_without_changing_legacy_request(self):
        fixture = semantic_fixtures.SemanticCompetencyTests('runTest'); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        f = fixture.fixture
        question_root = f.root / 'coverage-question-fixture'; question_root.mkdir()
        (question_root / 'competency_questions.json').write_text(json.dumps([{'id': 'q', 'text': 'Are all items grounded?'}]), encoding='utf-8')
        parent = deepcopy(fixture.parent)
        parent['semantic_coverage_mode'] = coverage.MODE
        parent['proposal_digest'] = proposal_digest(parent)
        with patch.object(semantic, 'load_release', return_value=SimpleNamespace(path=question_root)):
            before, _ = semantic.request_for(f.repo, f.root, fixture.parent)
            fresh, _ = semantic.request_for(f.repo, f.root, parent)
            after, _ = semantic.request_for(f.repo, f.root, fixture.parent)
        self.assertEqual(before, after)
        self.assertNotEqual(before['request_hash'], fresh['request_hash'])
        self.assertEqual(fresh['coverage_inventory'], coverage.inventory(parent['merged_schema']))
        self.assertEqual(fresh['coverage_inventory_hash'], sha256_json(fresh['coverage_inventory']))
        self.assertEqual(fresh['response_contract_version'], coverage.VERSION)

    def test_saved_v2_receipt_round_trips_all_matrix_fields(self):
        fixture = semantic_fixtures.SemanticCompetencyTests('runTest'); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.request.update(response_contract_version=coverage.VERSION,
            coverage_inventory=deepcopy(self.entries), coverage_inventory_hash=sha256_json(self.entries))
        fixture.request['coverage_contract_hash'] = coverage.contract_hash(semantic.SCHEMA, fixture.request)
        fixture.request['request_hash'] = sha256_json({k: v for k, v in fixture.request.items() if k != 'request_hash'})
        fixture.answer.update({k: deepcopy(self.answer[k]) for k in ('coverage_matrix', 'quotation_catalog', 'item_issues', 'definition_gaps')})
        result, proof, voters = fixture.review()
        self.assertEqual(proof['status'], 'pass')
        self.assertTrue(all(v['coverage_matrix'] == self.answer['coverage_matrix'] for v in result['votes']))


if __name__ == '__main__':
    unittest.main()
