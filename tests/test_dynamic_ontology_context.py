"""Offline coverage of retrieval, frozen requests and safe extension routing."""
import copy
import json
import unittest

from review_agent.ontology_context import select_context, policy_for, frozen_context
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.contracts import sha256_json, proposal_digest
from review_agent.validator_contracts import versions_for, proposal_errors, prompt_for
from review_agent.validators import validator_payload
from review_agent.request_cache import validation_request_key
from review_agent.request_identity import semantic_request_key
from review_agent.agent_state import AgentState
from review_agent.extension_eligibility import extension_eligibility
from review_agent.runtime import AgentRuntime
from review_agent import response_contract_v6_3 as v63
from tests import test_review_v6_integration as integration


def schema():
    classes = {'root': {'id': 'root', 'label': '事物', 'definition': '所有对象'},
               'device': {'id': 'device', 'label': '设备', 'parents': ['root']},
               'gun': {'id': 'gun', 'label': '火炮', 'parents': ['device']},
               'force': {'id': 'force', 'label': '阻力', 'parents': ['root']}}
    properties = {
        'drag': {'id': 'drag', 'label': '受到阻力', 'aliases': ['受阻'], 'definition': '运动物体受到的阻力',
                 'domain': ['device'], 'range': ['force'], 'domain_semantics': 'all'},
        'drag_competing': {'id': 'drag_competing', 'label': '受到阻力', 'definition': '同名竞争定义必须核查',
                           'domain': ['gun'], 'range': ['force']},
        'other': {'id': 'other', 'label': '具有气象参数', 'definition': '气象环境中的数值', 'domain': [], 'range': []}}
    return {'classes': classes, 'object_properties': properties,
            'datatype_properties': {'speed': {'id': 'speed', 'label': '速度', 'definition': '运动快慢',
                                             'domain': ['gun'], 'datatype': 'decimal'}},
            'application_profile': {'constraint_semantics': 'allowed_any'}}


class DynamicOntologySelectionTests(unittest.TestCase):
    def setUp(self):
        self.guard = OntologyGuard(load_snapshot(schema()))
        self.candidate = {'candidate_id': 'c', 'revision': 1, 'ontology_version': self.guard.snapshot.ontology_version,
                          'triple': {'head': '弹丸', 'tail': '阻力', 'relation_id': 'drag',
                                     'surface_relation': '受到阻力', 'head_type': 'gun', 'tail_type': 'force'}}
        self.bundle = {'items': [{'evidence_id': 'ev', 'quote': '弹丸受到阻力。'}]}

    def select(self, **settings):
        return select_context(self.guard, self.candidate, self.bundle, policy_for({'ontology_context': settings}))

    def test_exact_and_competing_definitions_and_ancestors_are_mandatory(self):
        context = self.select(max_properties=2)
        self.assertEqual({p['id'] for p in context['properties']}, {'drag', 'drag_competing'})
        self.assertEqual({c['id'] for c in context['classes']}, {'gun', 'device', 'force', 'root'})
        self.assertEqual(context['properties'][0]['definition'], '运动物体受到的阻力')
        self.assertEqual(context['properties'][0]['domain_semantics'], 'all')
        self.assertFalse(context['full_catalog_included'])
        self.assertFalse(context['absence_confirmed'])

    def test_alias_recall_does_not_confirm_semantic_mapping(self):
        self.candidate['triple'].update(relation_id=None, surface_relation='受阻')
        context = self.select(max_properties=1)
        self.assertEqual(context['properties'][0]['id'], 'drag')
        self.assertFalse(context['retrieval']['recall_is_mapping_confirmation'])

    def test_literal_property_keeps_datatype(self):
        self.candidate['triple'].update(relation_id='speed', surface_relation='速度', tail_kind='literal')
        context = self.select(max_properties=1)
        prop = context['properties'][0]
        self.assertEqual(prop['property_kind'], 'data')
        self.assertEqual(prop['datatype'], 'decimal')

    def test_missing_relation_remains_unconfirmed_gap(self):
        self.candidate['triple'].update(relation_id=None, surface_relation='尚未定义关系')
        context = self.select(max_properties=1)
        self.assertTrue(context['retrieval']['searched_full_frozen_schema'])
        self.assertFalse(context['absence_confirmed'])
        self.assertFalse(context['full_catalog_included'])

    def test_required_records_are_never_silently_truncated(self):
        with self.assertRaisesRegex(ValueError, 'exceed context budget'):
            self.select(max_properties=1)
        self.guard.relations['drag']['definition'] = '很长' * 20000
        self.guard.ontology['object_properties']['drag']['definition'] = '很长' * 20000
        with self.assertRaisesRegex(ValueError, 'exceed context budget'):
            self.select()

    def test_required_ancestor_overflow_stops_instead_of_omitting_parent(self):
        with self.assertRaisesRegex(ValueError, 'exceed context budget'):
            self.select(max_classes=2)

    def test_selection_is_repeatable_and_input_is_immutable(self):
        before = copy.deepcopy((self.guard.ontology, self.candidate, self.bundle))
        a, b = self.select(), self.select()
        self.assertEqual(a, b)
        self.assertLessEqual(len(json.dumps(a, ensure_ascii=False)), 20000)
        self.assertEqual(before, (self.guard.ontology, self.candidate, self.bundle))

    def test_unconfirmed_extraction_type_is_only_a_retrieval_hint(self):
        self.candidate['extraction_hints'] = {'head_type_candidate': 'gun'}
        for value in (None, 'Unknown'):
            self.candidate['triple']['head_type'] = value
            c = self.select()
            self.assertFalse(c['retrieval']['endpoint_queries']['head']['confirmed_candidate_type'])
            self.assertEqual(c['retrieval']['endpoint_queries']['head']['class_id'],'gun')
            self.assertEqual(self.candidate['triple']['head_type'],value)

    def test_extra_query_expands_from_the_full_schema(self):
        context = select_context(self.guard, self.candidate, self.bundle, policy_for({}),
                                 {'retrieval_queries': ['具有气象参数']})
        self.assertIn('other', context['retrieval']['required_property_ids'])
        self.assertIn('other', {p['id'] for p in context['properties']})


class DynamicOntologyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = integration.ReviewV6IntegrationTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def run_review(self, **kwargs):
        return self.fixture.run_review(version=v63.CONTRACT_VERSION, **kwargs)

    def proposal(self, candidate, result):
        return self.fixture.f.repo.get_audit(candidate['candidate_id'], result['audit_id'])['rounds'][0]

    def test_three_seats_receive_identical_frozen_subset_without_full_catalog(self):
        c, result, payloads, _ = self.run_review()
        self.assertTrue(result['independent_review_completed'])
        self.assertEqual(len(payloads), 3)
        contexts = [p['ontology_context'] for p in payloads]
        self.assertTrue(all(contexts[0] == x for x in contexts))
        self.assertTrue(all('ontology_catalog' not in p for p in payloads))
        proposal = self.proposal(c, result)
        self.assertNotIn('ontology_catalog', proposal)
        self.assertEqual(frozen_context(proposal), contexts[0])
        self.assertEqual(proposal_digest(proposal), proposal['proposal_digest'])
        self.assertIn('动态本体上下文', prompt_for(proposal))

    def test_valid_formal_mapping_can_complete_admission_consensus(self):
        from review_agent.llm_clients import MockLLMClient
        from review_agent.orchestrator import ReviewOrchestrator
        from tests.test_admission_graph_sync import AdmissionFixture
        f=AdmissionFixture(self.fixture.f.folder/'dynamic_admission',text='A connects B.')
        c=f.candidate()
        def respond(payload):
            eid=payload['allowed_evidence_ids'][0]
            dims={n:{'status':'pass','evidence_ids':[eid],'note':'MOCK transport/contract check only','issue_code':None}
                  for n in v63.CHECK_NAMES}
            dims['triple_support']['status']='supported';dims['knowledge_scope']['status']='general'
            dims['mapping'].update(status='matched',relation_id='R',competing_relation_ids=[])
            return {'dimensions':dims,'evidence_assessments':[{'evidence_id':eid,'role':'support','note':'MOCK vote'}],
                    'primary_evidence_ids':[eid],'counterevidence_reviewed_ids':[],
                    'requested_context':None,'suggested_revision':None}
        voters=[{'id':n,'model_id':'mapped_'+n,'model_family':n,
                 'client':MockLLMClient(respond,model_id='mapped_'+n,model_family=n)} for n in 'abc']
        config={**self.fixture.f.config,'validator_contract_version':v63.CONTRACT_VERSION,
                'planner':{'enabled':False,'strategy':'deterministic'},'ontology_extension':{'enabled':False}}
        result=ReviewOrchestrator(f.repo,OntologyGuard(load_snapshot(f.release.ontology)),voters,config=config).audit_candidate(c['candidate_id'],1,f.index)
        self.assertTrue(result['independent_review_completed'])
        self.assertEqual(result['consensus']['admission_consensus']['result'],'accept_consensus')
        self.assertEqual(result['consensus']['effective_mapping_status'],'matched')

    def test_tampered_context_cannot_reuse_frozen_digest(self):
        c, result, _, orchestrator = self.run_review()
        proposal = self.proposal(c, result)
        proposal['ontology_context']['properties'].append({'id': 'forged'})
        with self.assertRaisesRegex(ValueError, 'hash/policy mismatch'):
            validator_payload(proposal, orchestrator.validators[0])
        proposal['ontology_context_hash'] = sha256_json(proposal['ontology_context'])
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            frozen_context(proposal)

    def test_mapping_ids_must_be_present_in_the_frozen_subset(self):
        c, result, _, _ = self.run_review()
        proposal = self.proposal(c, result)
        errors=proposal_errors({'dimensions': {'mapping': {'relation_id': 'not_in_subset'}}}, proposal)
        self.assertIn('outside frozen ontology context',errors[0])

    def test_context_changes_both_cache_and_semantic_request_identity(self):
        c, result, _, orchestrator = self.run_review()
        p = self.proposal(c, result);q = copy.deepcopy(p)
        q['ontology_context']['retrieval']['queries'].append('additional lookup')
        q['ontology_context_hash'] = sha256_json(q['ontology_context'])
        q['proposal_digest'] = proposal_digest(q)
        voter = orchestrator.validators[0]
        self.assertNotEqual(validation_request_key(voter, p), validation_request_key(voter, q))
        self.assertNotEqual(semantic_request_key('triple_review', voter, p), semantic_request_key('triple_review', voter, q))

    def test_recovery_keeps_original_subset_and_negative_opinion(self):
        from review_agent.recovery_service import recover_frozen_audit
        c, result, _, orchestrator = self.run_review(missing_seat=True, failed_dimension='qualifiers')
        before = self.proposal(c, result)
        recovered = recover_frozen_audit(self.fixture.f.repo, orchestrator, c['candidate_id'], result['audit_id'],
            reason='MOCK recover frozen dynamic subset', extra_calls=1, extra_tokens=20000, extra_seconds=30)
        self.assertTrue(recovered['independent_review_completed'])
        self.assertEqual(recovered['workflow_state'], 'revision_required')
        after = self.proposal(c, result)
        for key in ('ontology_context', 'ontology_context_hash', 'proposal_digest', 'ontology_context_policy'):
            self.assertEqual(after[key], before[key])
        self.assertEqual([len(v['client'].calls) for v in orchestrator.validators], [2, 1, 1])

    def test_planner_uses_subset_and_further_lookup_changes_validation_key(self):
        _, _, _, orchestrator = self.run_review()
        f = self.fixture.f
        # A separate audit initialized offline; no external model calls.
        runtime = AgentRuntime(orchestrator)
        runtime.initialize(f.candidate['candidate_id'], 1, f.index, {})
        runtime.tools.inspect_ontology(None, 'MOCK full lookup')
        old = runtime.state.validation_key()
        runtime.tools.inspect_ontology('R_old', 'MOCK further lookup', query='connects')
        self.assertNotEqual(old, runtime.state.validation_key())
        view = runtime.state.planner_input(runtime.budget, runtime.policy.definitions())
        self.assertNotIn('full_reference_catalog', json.dumps(view))
        self.assertIn('retrieved_context', view['ontology_lookup'])
        self.assertNotIn('ontology_catalog', view)

    def test_subset_gap_votes_are_preserved_but_cannot_authorize_extension(self):
        c, result, _, _ = self.run_review(mapping='not_covered')
        proposal = self.proposal(c, result)
        audit = self.fixture.f.repo.get_audit(c['candidate_id'], result['audit_id'])
        state = AgentState(candidate=c, context=versions_for({'validator_contract_version':v63.CONTRACT_VERSION}), audit_id=result['audit_id'])
        state.ontology_lookup = {'scope':'full_frozen_ontology', 'candidate_revision':1,
                                 'retrieved_context': {'full_catalog_included':True}}
        consensus = copy.deepcopy(result['consensus'])
        consensus['ontology_coverage_review'] = {'global_gap_confirmable':False}
        state.validations[state.validation_key()] = {'votes':audit['votes'], 'consensus':consensus}
        allowed = extension_eligibility(state)
        self.assertNotIn('add_object_property', allowed['allowed_change_kinds'])
        self.assertIn('subset cannot confirm', allowed['blocked_change_kinds']['add_object_property'])
        self.assertTrue(all(v['raw_response']['dimensions']['mapping']['status']=='not_covered' for v in audit['votes']))

    def test_v62_retains_original_prompt_and_full_catalog(self):
        from review_agent import response_contract_v6_2 as old
        _, result, payloads, _ = self.fixture.run_review(version=old.CONTRACT_VERSION)
        self.assertTrue(result['independent_review_completed'])
        self.assertTrue(all('ontology_catalog' in p and 'ontology_context' not in p for p in payloads))
        self.assertEqual(old.PROMPT_HASH, 'b5c98eb4abbe9e3694085113e3ece19c88f659a7a5830906a108f56864bffde2')

    def test_workbench_reports_the_active_context_mode(self):
        from review_agent.service import ReviewService
        service=ReviewService(self.fixture.f.repo,config={'validator_contract_version':v63.CONTRACT_VERSION})
        status=service.config_status()
        self.assertEqual(status['validator_contract_version'],v63.CONTRACT_VERSION)
        self.assertEqual(status['ontology_context_mode'],'dynamic_subset')


if __name__ == '__main__':
    unittest.main()
