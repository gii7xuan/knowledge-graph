"""Metadata completeness is separate from semantic mapping and ontology absence."""
import copy
import json
import unittest
from review_agent.mapping_preflight import prepare, policy_for, frozen, definitions, diagnostic_report
from review_agent.ontology_context import select_context, policy_for as context_policy
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.contracts import sha256_json, proposal_digest
from review_agent.validator_contracts import versions_for
from review_agent.request_cache import validation_request_key
from review_agent.monitor import ReviewMonitor
from schema.entity_registry import local_entity_id
from tests import test_dynamic_ontology_context as context_fixtures
from tests import test_review_four_axes as integration_fixtures


class MappingPreflightTests(unittest.TestCase):
    def setUp(self):
        self.schema=context_fixtures.schema()
        self.candidate={'candidate_id':'c','revision':1,'document_id':'doc','document_version':'v',
            'triple':{'head':'弹丸','tail':'阻力','surface_relation':'受阻','relation_id':'drag',
                      'head_type':'gun','tail_type':'force','tail_kind':'entity','qualifiers':{'polarity':'positive'}}}
        for side in ('head','tail'):
            self.candidate['triple'][side+'_entity_id']=local_entity_id('doc','v',self.candidate['triple'][side])
        self.bundle={'items':[{'evidence_id':'e','quote':'弹丸受到阻力。','anchor_valid':True}]}
        self.bundle['evidence_hash']=sha256_json(self.bundle)
        self.context_policy=context_policy({});self.policy=policy_for({})

    def setup_context(self):
        guard=OntologyGuard(load_snapshot(self.schema));self.candidate['ontology_version']=guard.snapshot.ontology_version
        context=select_context(guard,self.candidate,self.bundle,self.context_policy)
        return guard,context

    def run_check(self, context=None):
        guard,original=self.setup_context()
        return prepare(guard,self.candidate,self.bundle,context or original,self.context_policy,self.policy)

    def test_complete_packet_is_checked_before_semantic_review_without_global_replacement(self):
        _,report=self.run_check()
        self.assertTrue(report['judgment_ready'])
        self.assertEqual(len(report['search_trace']),1)
        self.assertFalse(report['formal_mapping']['semantic_approval'])
        self.assertFalse(report['global_gap_confirmed'])

    def test_missing_subset_definition_triggers_full_release_retrieval(self):
        guard,context=self.setup_context();context['properties']=[p for p in context['properties'] if p['id']!='drag']
        enriched,report=prepare(guard,self.candidate,self.bundle,context,self.context_policy,self.policy)
        self.assertTrue(report['judgment_ready'])
        self.assertIn('drag',{p['id'] for p in enriched['properties']})
        self.assertEqual(report['search_trace'][-1]['stage'],'full_bound_release_and_aliases')

    def test_definition_empty_in_release_remains_incomplete_after_search(self):
        self.schema['object_properties']['drag']['definition']=' '
        _,report=self.run_check()
        self.assertFalse(report['judgment_ready'])
        self.assertIn('definition_missing_in_bound_release',[f for b in report['blocking_information'] for f in b['fields']])
        self.assertFalse(report['global_gap_confirmed'])
        self.assertNotEqual(report['semantic_applicability'],'inapplicable')

    def test_declared_multilingual_alias_is_recalled_without_assigning_a_formal_id(self):
        self.schema['object_properties']['drag']['labels']=[{'value':'experiences resistance','language':'en'}]
        self.candidate['triple'].update(surface_relation='experiences resistance',relation_id=None)
        before=copy.deepcopy(self.candidate['triple'])
        _,report=self.run_check()
        self.assertIn('drag',report['formal_mapping']['candidate_relation_ids'])
        self.assertIsNone(report['formal_mapping']['assigned_relation_id'])
        self.assertEqual(self.candidate['triple'],before)

    def test_same_name_competitors_and_type_definitions_are_preserved(self):
        self.candidate['triple']['surface_relation']='受到阻力'
        enriched,report=self.run_check()
        self.assertEqual(report['confusable_relations'][0]['candidate_ids'],['drag','drag_competing'])
        self.assertTrue({'root','device','gun','force'} <= {c['id'] for c in enriched['classes']})

    def test_unassigned_entity_type_is_not_inferred_from_relation_constraints(self):
        self.candidate['triple']['head_type']=None
        _,report=self.run_check()
        self.assertFalse(report['judgment_ready'])
        self.assertIsNone(self.candidate['triple']['head_type'])
        self.assertIn('endpoint_type_unassigned_or_ambiguous',report['entities']['head']['missing_information'])

    def test_same_name_classes_are_not_silently_merged(self):
        self.schema['classes']['second_gun']={'id':'second_gun','label':'弹丸','definition':'Another referent'}
        self.schema['classes']['gun']['aliases']=['弹丸']
        self.candidate['triple']['head_type']=None
        _,report=self.run_check()
        self.assertTrue({'gun','second_gun'}<=set(report['entities']['head']['type_candidate_ids']))
        self.assertFalse(report['judgment_ready'])

    def test_incorrect_document_local_identity_does_not_pass_preflight(self):
        self.candidate['triple']['head_entity_id']='foreign_id'
        _,report=self.run_check()
        self.assertFalse(report['judgment_ready'])
        self.assertIn('candidate_identity_unverified',report['entities']['head']['missing_information'])

    def test_budget_failure_does_not_drop_competitors_or_claim_absence(self):
        self.schema['object_properties']['drag_competing']['aliases']=['受阻']
        guard=OntologyGuard(load_snapshot(self.schema));self.candidate['ontology_version']=guard.snapshot.ontology_version
        original=select_context(guard,self.candidate,self.bundle,context_policy({}))
        original['properties']=[p for p in original['properties'] if p['id']=='drag']
        small=context_policy({'ontology_context':{'max_properties':1}})
        enriched,report=prepare(guard,self.candidate,self.bundle,original,small,self.policy)
        self.assertFalse(report['judgment_ready'])
        self.assertIn('drag_competing',report['budget_excluded_property_ids'])
        self.assertFalse(report['global_gap_confirmed'])

    def test_empty_definition_containers_are_not_mistaken_for_definition_text(self):
        self.assertEqual(definitions({'definitions':[{}, {'language':'zh','value':' '}]}),[])
        self.assertEqual(definitions({'definitions':[{'value':'A relation definition'}]}),['A relation definition'])

    def test_preflight_is_frozen_to_candidate_evidence_and_context(self):
        context,report=self.run_check()
        proposal={**versions_for({'validator_contract_version':'validator_response_v7','mapping_preflight':{'enabled':True}}),
            'audit_id':'a','candidate':copy.deepcopy(self.candidate),'candidate_id':'c','revision':1,
            'evidence_bundle':self.bundle,'ontology_context':context,'ontology_context_hash':sha256_json(context),
            'ontology_catalog_hash':context['source_catalog_hash'],'mapping_preflight':report,'mapping_preflight_hash':sha256_json(report)}
        proposal['proposal_digest']=proposal_digest(proposal)
        self.assertEqual(frozen(proposal),report)
        changed=copy.deepcopy(proposal);changed['mapping_preflight']['judgment_ready']=False
        with self.assertRaisesRegex(ValueError,'binding mismatch'):frozen(changed)
        changed=copy.deepcopy(proposal);changed['candidate']['triple']['tail']='另一个对象'
        with self.assertRaisesRegex(ValueError,'binding mismatch'):frozen(changed)
        self.assertIsNone(frozen({'response_contract_version':'validator_response_v7'}))

    def test_incomplete_mapping_metadata_and_lexical_scan_cannot_authorize_extension(self):
        from review_agent.agent_state import AgentState
        from review_agent.extension_eligibility import extension_eligibility
        state=AgentState(candidate=copy.deepcopy(self.candidate), context=versions_for({
            'validator_contract_version':'validator_response_v7','mapping_preflight':{'enabled':True}}),audit_id='a')
        state.ontology_lookup={'scope':'full_frozen_ontology','candidate_revision':1,
            'mapping_preflight':{'judgment_ready':False,'global_gap_confirmed':False}}
        consensus={'expression_status':'faithful','source_support_consensus':{'result':'supported'},
            'eligible_voter_ids':['a','b','c'],'ontology_coverage_review':{'global_gap_confirmable':True}}
        state.validations[state.validation_key()]={'consensus':consensus,
            'votes':[{'voter_id':v,'schema_issue':'not_covered'} for v in 'abc']}
        allowed=extension_eligibility(state)
        self.assertIn('mapping information incomplete',allowed['blocked_change_kinds']['add_object_property'])
        state.ontology_lookup['mapping_preflight']['judgment_ready']=True
        consensus['ontology_coverage_review']['global_gap_confirmable']=False
        self.assertIn('subset cannot confirm',extension_eligibility(state)['blocked_change_kinds']['add_object_property'])


class MappingPreflightIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=integration_fixtures.FourAxisIntegrationTests('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_preflight_is_dispatched_before_mapping_and_preserves_valid_source_votes(self):
        candidate,result,orchestrator=self.fixture.run_case(mapping_preflight=True,model_mapping='matched',missing_mapping_definition=True)
        self.assertTrue(result['independent_review_completed'],result)
        self.assertEqual(result['review_axes']['source']['status'],'supported')
        self.assertEqual(result['review_axes']['mapping']['status'],'context_incomplete')
        self.assertNotEqual(result['admission_consensus']['result'],'accept_consensus')
        for voter in orchestrator.validators:
            self.assertFalse(voter['client'].calls[0]['mapping_preflight']['judgment_ready'])
        audit=self.fixture.fixture.repo.get_audit(candidate['candidate_id'],result['audit_id'])
        self.assertTrue(all(v['response_status']=='ok' for v in audit['votes']))
        self.assertTrue(all(v['mapping']['status']=='matched' for v in audit['votes']))
        self.assertEqual(self.fixture.fixture.repo.get_candidate(candidate['candidate_id'])['triple']['relation_id'],'R_old')
        proposal=audit['rounds'][0]
        old=copy.deepcopy(proposal)
        for key in ('mapping_preflight','mapping_preflight_hash','mapping_preflight_policy','mapping_preflight_policy_hash'):
            old.pop(key,None)
        old['proposal_digest']=proposal_digest(old)
        self.assertNotEqual(validation_request_key(orchestrator.validators[0],proposal),validation_request_key(orchestrator.validators[0],old))

    def test_post_run_diagnostic_does_not_modify_the_historical_decision(self):
        candidate,result,_=self.fixture.run_case()
        f=self.fixture.fixture;before=copy.deepcopy(f.repo.latest_decision(candidate['candidate_id']))
        proposal=f.repo.get_audit(candidate['candidate_id'],result['audit_id'])['rounds'][0]
        binding={'audit_id':result['audit_id'],'proposal_digest':proposal['proposal_digest'],'candidate_hash':sha256_json(proposal['candidate']),
            'evidence_hash':proposal['evidence_bundle']['evidence_hash'],'ontology_context_hash':proposal['ontology_context_hash']}
        report={'judgment_ready':False,'entities':{},'global_gap_confirmed':False}
        entry={'binding':binding,'report':report,'report_hash':sha256_json(report)}
        (f.folder/'mapping_diagnostics.json').write_text(json.dumps({'entries':{result['audit_id']:entry}}),encoding='utf-8')
        registry=f.folder/'monitor.json';registry.write_text(json.dumps({'sources':[{'id':'s','database':'review.sqlite3','experiment_dir':'.'}]}),encoding='utf-8')
        monitor=ReviewMonitor(registry,root=f.folder)
        snapshot=monitor.snapshot('s',result['audit_id'])
        self.assertTrue(snapshot['mapping_preflight']['post_run_diagnostic'])
        self.assertEqual(before,f.repo.latest_decision(candidate['candidate_id']))
        entry['binding']['candidate_hash']='different_candidate'
        (f.folder/'mapping_diagnostics.json').write_text(json.dumps({'entries':{result['audit_id']:entry}}),encoding='utf-8')
        self.assertIsNone(monitor.snapshot('s',result['audit_id'])['mapping_preflight'])


if __name__=='__main__':unittest.main()
