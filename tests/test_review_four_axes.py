"""Four-axis source truth, bounded normalization and frozen legacy behavior."""
import copy
import json
import unittest
from pathlib import Path
from review_agent import response_contract_v7 as v7, response_contract_v6_3 as v63
from review_agent.contracts import sha256_json, fact_key_for, proposal_digest
from review_agent.review_axes import normalization_errors, aggregate_axes, route_axes, display_axes
from review_agent.validator_contracts import versions_for, validate_vote, proposal_errors
from review_agent.validators import judge_validator
from review_agent.llm_clients import MockLLMClient
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.response_corrections import make_plan, policy_for
from to_neo4j.fact_projection import direct_edge_eligible, prepare
from tests import test_validator_v6_dimensions as dimension_fixtures
from tests import test_ontology_extension_agent as integration_fixtures


class FourAxisContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = dimension_fixtures.ValidatorV6DimensionTests(methodName='runTest')
        self.fixture.setUp()
        self.raw = {**self.fixture.response(), 'expression_findings': []}
        self.proposal = {'candidate': {'triple': {'surface_relation': 'often regulates',
            'qualifiers': {'applicability': 'often'}, 'qualifier_status': {'applicability': 'extracted'}}},
            'evidence_bundle': {'items': [{'evidence_id': self.fixture.f.good['evidence_id'],
                'quote': 'A often regulates B.', 'anchor_valid': True}]}}

    def finding(self, kind='field_placement', check='qualifiers', value='often'):
        return {'kind': kind, 'check': check, 'note': 'MOCK public explanation; preserve frequency.',
            'evidence_ids': [self.fixture.f.good['evidence_id']],
            'normalization': {'operation': 'retain_qualifier' if kind == 'field_placement' else 'remove_duplicate_qualifier',
                'field': 'applicability', 'value': value} if kind in {'field_placement', 'duplicate_qualifier'} else None}

    def test_standard_opinion_has_four_separate_axes(self):
        self.assertEqual(v7.validate_response(self.raw), [])
        axes = v7.normalize(self.raw)['review_axes']
        self.assertEqual(set(axes), {'source', 'expression', 'mapping', 'scope'})
        self.assertEqual(axes['source']['status'], 'supported')
        self.assertEqual(axes['expression']['status'], 'standard')

    def test_preserved_frequency_is_not_a_source_error(self):
        self.raw['dimensions']['qualifiers']['status'] = 'fail'
        self.raw['expression_findings'] = [self.finding()]
        self.assertEqual(v7.validate_response(self.raw), [])
        self.assertEqual(normalization_errors(self.raw, self.proposal), [])
        normalized = v7.normalize(self.raw)
        self.assertEqual(normalized['review_axes']['source']['status'], 'supported')
        self.assertEqual(normalized['review_axes']['expression']['status'], 'lossless_normalization')
        self.assertEqual(normalized['expression_status'], 'normalization_required')
        self.assertNotIn('QUALIFIER_MISMATCH', normalized['reason_codes'])
        self.assertEqual(normalized['verdict'], 'uncertain')

    def test_claimed_losslessness_cannot_drop_or_change_the_frequency(self):
        self.raw['expression_findings'] = [self.finding(value='always')]
        self.assertTrue(normalization_errors(self.raw, self.proposal))
        self.raw['expression_findings'] = [self.finding()]
        self.proposal['candidate']['triple']['qualifiers']['applicability'] = None
        self.assertTrue(normalization_errors(self.raw, self.proposal))

    def test_polarity_and_modality_cannot_be_reclassified_as_normalization(self):
        finding = self.finding(value='positive')
        finding['normalization']['field'] = 'polarity'
        self.raw['expression_findings'] = [finding]
        self.assertTrue(normalization_errors(self.raw, self.proposal))

    def test_qualifier_duplicate_requires_exact_boundary_and_source_anchor(self):
        self.raw['expression_findings'] = [self.finding('duplicate_qualifier', 'relation_semantics')]
        self.assertEqual(normalization_errors(self.raw, self.proposal), [])
        self.proposal['candidate']['triple']['surface_relation'] = 'regulates'
        self.assertTrue(normalization_errors(self.raw, self.proposal))

    def test_duplicate_removal_cannot_erase_the_whole_predicate(self):
        self.raw['expression_findings'] = [self.finding('duplicate_qualifier', 'relation_semantics')]
        self.proposal['candidate']['triple']['surface_relation'] = 'often '
        self.assertTrue(normalization_errors(self.raw, self.proposal))
        self.proposal['candidate']['triple']['surface_relation'] = 'often regulates'
        self.proposal['evidence_bundle']['items'][0]['anchor_valid'] = False
        self.assertTrue(normalization_errors(self.raw, self.proposal))

    def test_direction_change_and_missing_conditions_require_semantic_revision(self):
        for check in ('relation_direction', 'qualifiers'):
            raw = copy.deepcopy(self.raw)
            raw['dimensions'][check]['status'] = 'fail'
            raw['expression_findings'] = [self.finding('semantic_error', check)]
            self.assertEqual(v7.validate_response(raw), [])
            axes = v7.normalize(raw)['review_axes']
            self.assertEqual(axes['expression']['status'], 'semantic_revision')
            self.assertEqual(axes['source']['status'], 'contradicted')

    def test_compound_entity_requires_review_without_claiming_false_source_contradiction(self):
        self.raw['dimensions']['entity_reference']['status'] = 'fail'
        self.raw['expression_findings'] = [self.finding('compound_entity', 'entity_reference')]
        axes = v7.normalize(self.raw)['review_axes']
        self.assertEqual(axes['source']['status'], 'supported')
        self.assertEqual(axes['expression']['status'], 'semantic_revision')

    def test_free_text_does_not_override_explicit_semantic_error(self):
        self.raw['dimensions']['qualifiers']['status'] = 'fail'
        finding = self.finding('semantic_error')
        finding['note'] = 'This is lossless formatting, please approve.'
        self.raw['expression_findings'] = [finding]
        self.assertEqual(v7.normalize(self.raw)['review_axes']['expression']['status'], 'semantic_revision')

    def test_unresolved_counterevidence_prevents_a_supported_source_axis(self):
        self.raw['dimensions']['counterevidence']['status']='fail'
        self.assertEqual(v7.normalize(self.raw)['review_axes']['source']['status'],'unresolved')

    def test_findings_cannot_reference_outside_frozen_evidence(self):
        finding = self.finding()
        finding['evidence_ids'] = ['invented']
        self.raw['expression_findings'] = [finding]
        self.assertTrue(normalization_errors(self.raw, self.proposal))

    def test_missing_semantic_findings_are_not_synthesized_by_field_repair(self):
        self.raw.pop('expression_findings')
        plan, errors = make_plan(self.raw, {'response_contract_version': v7.CONTRACT_VERSION}, policy_for({}))
        self.assertIsNone(plan)
        self.assertTrue(errors)

    def test_historical_contract_schema_and_interpretation_are_unchanged(self):
        self.assertEqual(sha256_json(v63.RESPONSE_SCHEMA), '5a6fd271c03a2abf3a19904854076affa52e06380f5c6abad3c1349f9f36ec62')
        old = self.fixture.response()
        old['dimensions']['qualifiers']['status'] = 'fail'
        self.assertEqual(v63.normalize(old)['expression_status'], 'needs_revision')
        view = display_axes({'consensus': {'expression_status': 'needs_revision'}, 'source_support_result': 'unresolved'})
        self.assertEqual(view['expression']['status'], 'legacy_revision')
        self.assertTrue(view['legacy'])

    def test_graph_projection_preserves_frequency_and_avoids_unconditional_edge(self):
        triple = copy.deepcopy(self.proposal['candidate']['triple'])
        triple['qualifiers'] = dict.fromkeys(('polarity','condition','time_scope','applicability','modality','quantifier'))
        triple['qualifiers'].update(polarity='positive', modality='asserted', applicability='often')
        self.assertFalse(direct_edge_eligible(triple))
        triple.update(head='A', tail='B', head_entity_id='a', tail_entity_id='b', relation_id='r', relation_semantic_hash='s')
        contribution = {'candidate': {'candidate_id':'c','revision':1,'fact_key':'f','triple':triple,
            'ontology_release_id':'release','ontology_version':'o','profile_hash':'p'}, 'decision': {'decision_id':'d','decision_version':1}, 'bundle_hash':'bundle'}
        projected = prepare(contribution)
        self.assertEqual(json.loads(projected['qualifiers_json'])['applicability'], 'often')


class FourAxisRoutingTests(unittest.TestCase):
    def axes(self, source='supported', expression='standard', mapping='valid', scope='include'):
        return {k:{'status':v} for k,v in zip(('source','expression','mapping','scope'),(source,expression,mapping,scope))}

    def test_queues_are_distinct_and_parallel_mapping_obligation_is_retained(self):
        route = route_axes(self.axes(expression='lossless_normalization',mapping='context_incomplete'))
        self.assertEqual(route['workflow_state'],'normalization_required')
        self.assertEqual(route['pending_actions'],['normalization_required','mapping_review'])
        self.assertFalse(route['ready_for_admission'])
        self.assertEqual(route_axes(self.axes(expression='semantic_revision'))['workflow_state'],'semantic_revision_required')
        self.assertEqual(route_axes(self.axes(source='insufficient'))['workflow_state'],'evidence_insufficient')

    def test_scope_exclusion_preserves_supported_source(self):
        axes=self.axes(scope='exclude')
        route=route_axes(axes)
        self.assertEqual(route['workflow_state'],'excluded_by_scope')
        self.assertEqual(axes['source']['status'],'supported')

    def test_technical_missing_vote_does_not_become_evidence_insufficiency(self):
        route=route_axes(self.axes(source='unresolved',expression='unresolved'),complete=False)
        self.assertEqual(route['workflow_state'],'review_incomplete')
        self.assertNotIn('evidence_insufficient',route['pending_actions'])

    def test_source_disagreement_is_distinct_from_insufficient_evidence(self):
        self.assertEqual(route_axes(self.axes(source='unresolved'))['workflow_state'], 'source_review')

    def test_three_models_of_insufficient_family_diversity_do_not_form_source_support(self):
        votes=[{'voter_id':n,'review_axes':self.axes(),'expression_findings':[]} for n in 'abc']
        proposal={'committee_members':[{'id':n} for n in 'abc']}
        axes=aggregate_axes(votes,proposal,{'diversity_ready':False})
        self.assertEqual(axes['source']['status'],'unresolved')
        self.assertEqual(axes['scope']['status'],'unclear')
        axes=aggregate_axes(votes,proposal,{'diversity_ready':True,'counter_evidence_ids':['counter']})
        self.assertEqual(axes['source']['status'],'unresolved')

    def test_unanimous_contradiction_retains_the_counterevidence_conclusion(self):
        votes=[{'voter_id':n,'review_axes':self.axes(source='contradicted'),
                'expression_findings':[],'counter_evidence_ids':['counter']} for n in 'abc']
        proposal={'committee_members':[{'id':n} for n in 'abc']}
        consensus={'diversity_ready':True,'counter_evidence_ids':['counter']}
        axes=aggregate_axes(votes,proposal,consensus)
        self.assertEqual(axes['source']['status'],'contradicted')
        self.assertEqual(axes['source']['evidence_ids'],['counter'])
        votes[0]['review_axes']['source']['status']='supported'
        self.assertEqual(aggregate_axes(votes,proposal,consensus)['source']['status'],'unresolved')
        votes[0]['review_axes']['source']['status']='contradicted'
        consensus['unresolved_support_conflict']=True
        self.assertEqual(aggregate_axes(votes,proposal,consensus)['source']['status'],'unresolved')
        consensus['unresolved_support_conflict']=False
        self.assertEqual(aggregate_axes(votes[:2],proposal,consensus)['source']['status'],'unresolved')


class FourAxisIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=integration_fixtures.OntologyExtensionTests(methodName='runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def run_case(self, semantic=False, scope='general', frequency=False, missing=False, invalid_planner=False, mapping_preflight=False, model_mapping='unassigned', missing_mapping_definition=False, version=v7.CONTRACT_VERSION, dispute_review=False, opinion_override=None):
        f=self.fixture;c=copy.deepcopy(f.candidate);c['candidate_id']+='_four'
        if missing_mapping_definition:
            from ontologyBuild.release_catalog import ReleaseCatalog
            schema=copy.deepcopy(f.base.ontology)
            schema['object_properties']['R_old']['definition']=''
            f.base=ReleaseCatalog(f.root).publish(f.artifact(schema),approval={'status':'approved','approved_by':'fixture','reason':'MOCK missing definition release'},expected_base_release_id=f.base.manifest['release_id'])
            c.update(f.base.binding())
            c['triple']['relation_id']='R_old'
        source_quote='A regulates B.'
        if frequency:
            from document.hierarchy_index import build_document_index, split_markdown_text, persist_document_index
            from extract_triplets.candidate_contract import normalize_candidate
            from extract_triplets.pipeline_v4 import source_scope
            source_quote='A often regulates B.'
            f.index=build_document_index(split_markdown_text('Regulation changes a device. '+source_quote,'frequency_doc',600,20))
            path=persist_document_index(f.index,f.folder/'frequency.json')
            f.repo.create_run({'run_id':'frequency','mode':'shadow','document_path':str(path),'source_index_hash':f.index['index_content_hash'],
                'ontology_path':str(f.base.snapshot_path),'ontology_hash':f.base.manifest['ontology_version'],'candidate_contract_version':'extraction_candidate_v4'})
            triple=copy.deepcopy(c['triple']);triple['qualifiers']['applicability']='often';triple['qualifier_status']['applicability']='extracted'
            c=normalize_candidate({**triple,'evidence_quotes':[source_quote]},index=f.index,scope=source_scope(f.index,f.index['chunks'][0]),
                ontology=f.base.ontology,release_binding=f.base.binding(),run_id='frequency',ordinal=0,prompt_hash='fixture')
        c['fact_key']=fact_key_for(c);f.repo.add_candidate(c)
        def respond(payload):
            eid=next(e['evidence_id'] for e in payload['evidence'] if source_quote in e['quote'])
            dims={n:{'status':'pass','evidence_ids':[eid],'note':'MOCK independently verified evidence','issue_code':None} for n in v7.CHECK_NAMES}
            dims['triple_support']['status']='supported';dims['knowledge_scope']['status']=scope
            dims['mapping'].update(status=model_mapping,relation_id='R_old' if model_mapping=='matched' else None,competing_relation_ids=[])
            findings=[]
            if semantic:
                dims['qualifiers']['status']='fail'
                findings=[{'check':'qualifiers','kind':'semantic_error','note':'MOCK missing condition','evidence_ids':[eid],'normalization':None}]
            if frequency:
                dims['qualifiers']['status']='fail'
                findings=[{'check':'qualifiers','kind':'field_placement','note':'MOCK frequency retained in applicability',
                    'evidence_ids':[eid],'normalization':{'operation':'retain_qualifier','field':'applicability','value':'often'}}]
            if version == 'validator_response_v8':
                if frequency:
                    findings=[{'check':'qualifiers','kind':'field_placement','note':'MOCK frequency preserved with original attachment',
                        'rule_id':p['rule_id'],'meaning_effect':'preserved','suggested_change':'Use the exact provided normalization plan',
                        'evidence_ids':[eid],'normalization':{'operation':'apply_field_plan','plan_id':p['plan_id']}}
                        for p in payload['field_standardization']['plans']]
                else:
                    findings=[dict(f,rule_id='MEANING-001',meaning_effect='changed',suggested_change='Re-review a corrected candidate') for f in findings]
            response={'dimensions':dims,'expression_findings':findings,'evidence_assessments':[{'evidence_id':eid,'role':'support','note':'MOCK source'}],
                    'primary_evidence_ids':[] if semantic else [eid],'counterevidence_reviewed_ids':[],'requested_context':None,'suggested_revision':None}
            return opinion_override(payload,response) if opinion_override else response
        voters=[{'id':n,'model_id':n,'model_family':n,'client':MockLLMClient(respond,model_id=n,model_family=n)} for n in 'abc']
        if missing:voters[1]['client']=MockLLMClient([RuntimeError('MOCK service unavailable')],model_id='b',model_family='b')
        config={**f.config,'validator_contract_version':version,'planner':{'enabled':False,'strategy':'deterministic'},
                'ontology_extension':{'enabled':False},'consensus':{**f.config['consensus'],'max_rounds':1}}
        if mapping_preflight: config['mapping_preflight']={'enabled':True}
        if dispute_review:config['dispute_review']={'enabled':True}
        planner=None
        if invalid_planner:
            config['planner']={'enabled':True,'strategy':'llm_react','fallback':'manual','max_invalid_steps':1}
            planner=MockLLMClient([{},{}],model_id='planner',model_family='planner')
        orchestrator=ReviewOrchestrator(f.repo,OntologyGuard(load_snapshot(f.base.ontology)),voters,config=config,planner_client=planner)
        result=orchestrator.audit_candidate(c['candidate_id'],1,f.index)
        return c,result,orchestrator

    def test_standard_pipeline_persists_four_axes_and_frozen_scope_policy(self):
        c,result,orchestrator=self.run_case()
        self.assertTrue(result['independent_review_completed'],result)
        self.assertEqual(result['review_axes']['source']['status'],'supported')
        self.assertEqual(result['review_axes']['expression']['status'],'standard')
        self.assertIn('mapping_review',result['pending_actions'])
        audit=self.fixture.repo.get_audit(c['candidate_id'],result['audit_id'])
        proposal=audit['rounds'][0]
        self.assertIn('review_scope_policy',proposal)
        altered=copy.deepcopy(proposal);altered['review_scope_policy']['excluded_scopes']=[]
        self.assertNotEqual(proposal_digest(altered),proposal['proposal_digest'])
        for vote in audit['votes']:self.assertEqual(validate_vote(vote,v7.CONTRACT_VERSION),[])
        self.assertTrue(all('ontology_context' in v['client'].calls[0] for v in orchestrator.validators))

    def test_semantic_failure_enters_its_own_queue(self):
        _,result,_=self.run_case(semantic=True)
        self.assertTrue(result['independent_review_completed'],result)
        self.assertEqual(result['workflow_state'],'semantic_revision_required')
        self.assertEqual(result['review_axes']['source']['status'],'contradicted')
        self.assertEqual(result['admission_consensus']['result'],'unresolved')

    def test_unanimous_source_counterevidence_survives_full_aggregation(self):
        def contradict(payload,value):
            eid=value['evidence_assessments'][0]['evidence_id']
            value['dimensions']['triple_support']['status']='contradicted'
            value['dimensions']['counterevidence']['status']='fail'
            value['evidence_assessments'][0]['role']='counter'
            value['primary_evidence_ids']=[]
            value['counterevidence_reviewed_ids']=[]
            return value
        _,result,_=self.run_case(version='validator_response_v8',opinion_override=contradict)
        self.assertTrue(result['independent_review_completed'],result)
        self.assertEqual(result['review_axes']['source']['status'],'contradicted')
        self.assertTrue(result['review_axes']['source']['evidence_ids'])
        self.assertEqual(result['source_support_result'],'contradicted')
        self.assertEqual(result['consensus']['result'],'reject_consensus')
        self.assertEqual(result['admission_consensus']['result'],'unresolved')

    def test_scope_exclusion_is_independent_from_fact_support(self):
        _,result,_=self.run_case(scope='local_definition')
        self.assertTrue(result['independent_review_completed'],result)
        self.assertEqual(result['workflow_state'],'excluded_by_scope')
        self.assertEqual(result['review_axes']['source']['status'],'supported')

    def test_frequency_normalization_persists_without_opening_semantic_revision_issue(self):
        c,result,_=self.run_case(frequency=True)
        self.assertTrue(result['independent_review_completed'],result)
        self.assertEqual(result['review_axes']['source']['status'],'supported')
        self.assertEqual(result['workflow_state'],'normalization_required')
        self.assertEqual(result['source_support_result'],'supported')
        self.assertNotEqual(result['admission_consensus']['result'],'accept_consensus')
        self.assertNotIn('QUALIFIER_MISMATCH',[i['code'] for i in result['issue_history']])
        self.assertEqual(self.fixture.repo.get_candidate(c['candidate_id'])['triple']['qualifiers']['applicability'],'often')

    def test_failed_seat_is_review_incomplete_not_a_fact_error(self):
        _,result,_=self.run_case(missing=True)
        self.assertFalse(result['independent_review_completed'])
        self.assertEqual(result['workflow_state'],'review_incomplete')
        self.assertEqual(result['review_axes']['source']['status'],'unresolved')

    def test_planner_failure_before_validation_has_unresolved_new_axes(self):
        _,result,_=self.run_case(invalid_planner=True)
        self.assertFalse(result['independent_review_completed'])
        self.assertEqual(result['workflow_state'],'review_incomplete')
        self.assertEqual(result['review_axes']['version'],'review_axes_v1')
        self.assertFalse(display_axes(result).get('legacy',False))


if __name__=='__main__':unittest.main()
