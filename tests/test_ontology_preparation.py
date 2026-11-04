import unittest
from review_agent.llm_clients import MockLLMClient
from review_agent.repository import ConflictError
from ontology_review.preparation import OntologyPreparationRuntime
from tests import test_ontology_extension_agent as helpers


class OntologyPreparationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.OntologyExtensionTests(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def test_real_preparation_keeps_delta_but_never_dispatches_independent_votes(self):
        f=self.fixture;runtime=OntologyPreparationRuntime(f.repo,f.root,f.voters,MockLLMClient(f.plan),f.config)
        result=runtime.run(f.extension_id)
        self.assertTrue(result['operator_preparation_only']);self.assertFalse(result['independent_review_completed'])
        self.assertFalse(result['release_published']);self.assertTrue(f.repo.extension_status(f.extension_id).get('delta'))
        self.assertEqual([len(v['client'].calls) for v in f.voters],[0,0,0])
        audit=f.repo.ontology_audit(result['audit_id']);self.assertEqual(audit['proposals'],[]);self.assertEqual(audit['votes'],[])

    def test_existing_delta_is_not_replanned_to_escape_an_earlier_review(self):
        f=self.fixture;f.review()
        runtime=OntologyPreparationRuntime(f.repo,f.root,f.voters,MockLLMClient(f.plan),f.config)
        with self.assertRaises(ConflictError):runtime.run(f.extension_id)

    def test_three_planning_calls_are_available_and_local_checks_finish_without_votes(self):
        f=self.fixture
        f.config['ontology_extension']['budget']['max_llm_calls_per_candidate']=3
        runtime=OntologyPreparationRuntime(f.repo,f.root,f.voters,MockLLMClient(f.plan),f.config)
        result=runtime.run(f.extension_id)
        self.assertTrue(f.repo.extension_status(f.extension_id).get('delta'))
        self.assertEqual(result['budget']['usage']['llm_calls'],3)
        self.assertTrue(all(k in runtime.state['checks'] for k in ('structure','impact','competency')))
        self.assertEqual([len(v['client'].calls) for v in f.voters],[0,0,0])
        self.assertEqual(f.repo.ontology_audit(result['audit_id'])['proposals'],[])
