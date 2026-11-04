import unittest
from ontology_review.semantic_competency import verified_context_spans, errors


class SemanticContextDeliveryTests(unittest.TestCase):
    def fixture(self):
        sources = [{'document_id': 'd', 'document_version': 'v', 'text': '稳定飞行时，攻角很小。'}]
        item = {'evidence_id': 'ev_a', 'document_id': 'd', 'document_version': 'v',
                'offset_basis': 'canonical_document', 'start': 0, 'end': 11, 'quote': sources[0]['text'],
                'semantic_status': 'support', 'reviewer_vote': 'approve'}
        return {'semantic_context_delivery': 'verified_frozen_spans_v1', 'evidence': [item]}, sources

    def test_historical_parents_keep_old_delivery(self):
        self.assertIsNone(verified_context_spans({'evidence': [{'quote': 'unverified'}]}, []))

    def test_excerpts_are_exact_and_do_not_leak_roles_or_votes(self):
        p, sources = self.fixture()
        spans = verified_context_spans(p, sources)
        self.assertEqual(spans[0]['quote'], sources[0]['text'])
        self.assertNotIn('semantic_status', spans[0]); self.assertNotIn('reviewer_vote', spans[0])

    def test_changed_quotes_or_conflicting_ids_are_rejected(self):
        p, sources = self.fixture(); p['evidence'][0]['quote'] = '未经原文支持的解释'
        with self.assertRaises(ValueError): verified_context_spans(p, sources)

    def test_bad_quote_diagnostic_identifies_the_question_and_occurrence_count(self):
        request = {'questions': [{'id': 'q'}], 'source_documents': [{'document_id': 'd', 'text': '冻结原文中的实际引文。'}]}
        value = {'coverage': 'partial', 'unresolved_terms': ['x'], 'assessments': [
            {'question_id': 'q', 'status': 'fail', 'quotes': [{'document_id': 'd', 'quote': '模型改写过的错误引文。'}], 'note': '保留实质反对。'}]}
        result = errors(value, request)
        self.assertIn('question_id=q', result[0]); self.assertIn('quote_index=0', result[0]); self.assertIn('occurrences=0', result[0])
        p, sources = self.fixture(); other = {**p['evidence'][0], 'start': 6, 'end': 10, 'quote': sources[0]['text'][6:10]}
        p['evidence'].append(other)
        with self.assertRaises(ValueError): verified_context_spans(p, sources)
