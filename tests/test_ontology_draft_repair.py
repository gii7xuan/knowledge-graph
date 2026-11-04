"""Real draft repairs and semantic guard regressions; model clients are mocks."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from rdflib import RDFS, URIRef, XSD
from ontology_review.draft_repair import repair_draft
from ontology_review.tools import OntologyTools
from ontology_review.delta import apply_delta, catalog
from ontologyBuild.rdf_graph import parse_turtle
from review_agent.ontology_guard import OntologyGuard
from review_agent.repository import ConflictError, ValidationError
from tests import test_ontology_extension_agent as fixtures
from tests import test_owl_extension_publication as owl_fixtures


class DatatypeTests(unittest.TestCase):
    def test_prefixed_datatype_compiles_to_standard_iri_with_original_literal_lexical_form(self):
        fixture = owl_fixtures.OwlDeltaTests(methodName='runTest')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        delta = apply_delta(fixture.base, [fixture.op('add_data_property',
            label='serial code', definition='An uninterpreted device serial code.',
            domain=[fixture.ids[owl_fixtures.EX + 'Device']], datatype='xsd:string')], {'source_definition'})
        item = catalog(delta['merged_schema'])[delta['operations'][0]['target']]
        self.assertEqual(item['datatype'], str(XSD.string))
        graph = parse_turtle(delta['owl_patch']['merged_rdf'])
        self.assertIn((URIRef(item['iri']), RDFS.range, XSD.string), graph)
        self.assertFalse(any(str(value) == 'xsd:string' for triple in graph for value in triple))
        self.assertEqual(OntologyGuard._datatype_status({'datatype':'xsd:string','value':'001'}, item), 'pass')
        self.assertEqual(OntologyGuard._datatype_status({'datatype':'xsd:integer','value':'no'}, {'datatype':str(XSD.integer)}), 'fail')
        self.assertEqual(OntologyGuard._datatype_status({'datatype':'xsd:madeUp','value':'x'}, {'datatype':str(XSD)+'madeUp'}), 'unknown')


class CompetencyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.OntologyExtensionTests(methodName='runTest')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.fixture.review()

    def test_unknown_endpoint_is_not_failure_and_explicit_constraint_failure_stays_failure(self):
        runtime = self.fixture.runtime
        runtime.candidates = deepcopy(runtime.candidates)
        candidate = next(iter(runtime.candidates.values()))
        candidate['triple']['tail_type'] = None
        result = runtime.tools.run_competency_checks()
        self.assertEqual(result['checks'][0]['status'], 'unknown')
        self.assertEqual(result['status'], 'unknown')
        candidate['triple']['tail_type'] = 'C_other'
        runtime.state['delta']['merged_schema']['classes']['C_other'] = {'id':'C_other','label':'other device'}
        result = runtime.tools.run_competency_checks()
        self.assertEqual(result['checks'][0]['status'], 'fail')

    def test_alias_binding_checks_the_named_source_term_not_an_unrelated_target(self):
        candidate = {'triple': {'head':'source term','tail':'alternative term'}}
        delta = {'operations':[{'kind':'add_alias','target':'C_wrong','changes':{'aliases':['alternative term']}}],
            'merged_schema':{'classes':{'C_wrong':{'id':'C_wrong','label':'different term','aliases':['alternative term']}}}}
        self.assertEqual(OntologyTools.lexical_competency(candidate,delta,change_kind='add_alias')['status'], 'fail')
        delta['merged_schema']['classes']['C_wrong']['label'] = 'source term'
        result = OntologyTools.lexical_competency(candidate,delta,change_kind='add_alias')
        self.assertEqual(result['status'], 'pass')
        self.assertFalse(result['binary_fact_admission_checked'])
        self.assertTrue(result['semantic_alias_approval_still_required'])
        delta['merged_schema']['classes']['C_duplicate'] = {'id':'C_duplicate','label':'alternative term'}
        self.assertEqual(OntologyTools.lexical_competency(candidate,delta,change_kind='add_alias')['status'], 'fail')
        self.assertIsNone(OntologyTools.lexical_competency(candidate,delta,change_kind='add_object_property'))

    def test_retrieval_context_does_not_turn_a_natural_language_question_into_a_pass(self):
        runtime = self.fixture.runtime
        import json
        (runtime.base.path/'competency_questions.json').write_text(json.dumps([
            {'id':'semantic-question','status':'requires_semantic_review','text':'Are meanings preserved?'}]), encoding='utf-8')
        result = runtime.tools.run_competency_checks()
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['checks'][-1]['status'], 'unknown')


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.OntologyExtensionTests(methodName='runTest')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.fixture.review()
        self.repo = self.fixture.repo
        with self.repo.transaction(immediate=True) as connection:
            connection.execute("UPDATE jobs SET status='paused'")
        task = self.repo.extension_status(self.fixture.extension_id)
        self.evidence = [item['evidence_id'] for item in self.fixture.runtime.state['evidence']]
        self.kwargs = dict(expected_row_version=task['row_version'], repair_key='fixture-repair',
            action='withdraw', reason='Withdraw a demonstrably invalid constraint without changing the source truth.',
            actor='fixture_operator', evidence_ids=self.evidence, resolution={'disposition':'requires_remodeling'},
            review_references=[{'audit_id':self.fixture.audit['audit_id'],'round_id':1,'voter_id':'a'}])

    def repair(self, **overrides):
        return repair_draft(self.repo, self.fixture.root, self.fixture.extension_id, **{**self.kwargs, **overrides})

    def test_withdrawal_archives_delta_and_preserves_frozen_reviews_source_revisions_and_costs(self):
        before = self.repo.ontology_audit(self.fixture.audit['audit_id'])
        candidate = self.repo.get_revision(self.fixture.candidate['candidate_id'])
        account = self.repo.case_status(self.repo.ensure_case(candidate['candidate_id']))
        old = self.repo.extension_status(self.fixture.extension_id)
        self.repair()
        current = self.repo.extension_status(self.fixture.extension_id)
        self.assertIsNone(current['delta'])
        self.assertEqual(current['delta_revision'], old['delta_revision']+1)
        self.assertEqual(current['draft_repair_history'][-1]['previous_delta'], old['delta'])
        self.assertFalse(current['draft_repair_history'][-1]['independent_approval'])
        from ontology_review.publisher import OntologyPublisher
        with self.assertRaisesRegex(ValueError, 'fresh modeling review'):
            OntologyPublisher(self.repo,self.fixture.root).preview(self.fixture.extension_id)
        self.assertEqual(self.repo.ontology_audit(before['audit_id']), before)
        self.assertEqual(self.repo.get_revision(candidate['candidate_id']), candidate)
        self.assertEqual(self.repo.case_status(account['case_id']), account)
        self.assertTrue(self.repair()['reused'])
        self.assertEqual(self.repo.extension_status(self.fixture.extension_id)['row_version'], current['row_version'])
        with self.assertRaises(ConflictError):
            self.repair(reason='A different request using the same key')

    def test_replacement_runs_real_tools_and_does_not_produce_independent_approval(self):
        op = deepcopy(self.fixture.runtime.state['delta']['operations'][0])
        op['target'] = None
        op['changes']['definition'] = 'The source-supported definition clarified for review.'
        result = self.repair(action='replace', operations=[op])
        self.assertEqual(result['repair']['checks']['structure']['status'], 'pass')
        self.assertFalse(result['repair']['independent_approval'])
        self.assertTrue(self.repo.extension_status(self.fixture.extension_id)['review_invalidated_by_human'])

    def test_queued_task_and_unverified_evidence_block_mutation(self):
        self.assertRaises(ValidationError, self.repair, evidence_ids=['invented-evidence-id'])
        with self.repo.transaction(immediate=True) as connection:
            connection.execute("UPDATE jobs SET status='queued'")
        self.assertRaisesRegex(ConflictError, 'pause', self.repair)

    def test_annotation_retains_original_delta_and_original_vote(self):
        old = self.repo.extension_status(self.fixture.extension_id)
        self.repair(action='annotate', resolution={'disposition':'reviewer_clarification_required','resolved':False})
        current = self.repo.extension_status(self.fixture.extension_id)
        self.assertEqual(current['delta'], old['delta'])
        self.assertEqual(current['delta_revision'], old['delta_revision'])


if __name__ == '__main__':
    unittest.main()
