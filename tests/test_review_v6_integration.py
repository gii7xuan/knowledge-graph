"""V6 exercises persisted proposals, recovery and routing with offline models."""
import copy
import unittest
from review_agent import response_contract_v6 as v6
from review_agent.contracts import fact_key_for
from review_agent.llm_clients import MockLLMClient
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.recovery_service import recover_frozen_audit
from evaluation.review_samples import freeze_review_sample
from tests import test_ontology_extension_agent as fixtures


class ReviewV6IntegrationTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.OntologyExtensionTests(methodName='runTest');f.setUp();self.addCleanup(f.doCleanups);self.f=f

    def run_review(self,*,mapping='unassigned',scope='general',failed_dimension=None,missing_seat=False,version=v6.CONTRACT_VERSION):
        f=self.f;c=copy.deepcopy(f.candidate);c['candidate_id']+='_v6';c['fact_key']=fact_key_for(c);f.repo.add_candidate(c)
        calls=[]
        def respond(payload,missing=False):
            calls.append(copy.deepcopy(payload));eid=next(e['evidence_id'] for e in payload['evidence'] if 'A regulates B.' in e['quote'])
            dims={n:{'status':'pass','evidence_ids':[eid],'note':'MOCK independent evidence check','issue_code':None} for n in v6.CHECK_NAMES}
            dims['triple_support']['status']='supported';dims['knowledge_scope']['status']=scope
            dims['mapping'].update(status=mapping,relation_id=None,competing_relation_ids=[])
            if failed_dimension:dims[failed_dimension]['status']='fail'
            if missing:dims.pop('mapping')
            if payload.get('task_type')=='triple_review_field_correction':
                return {'repairs':{e['key']:dims['mapping'] for e in payload['errors']}}
            return {'dimensions':dims,'evidence_assessments':[{'evidence_id':eid,'role':'support','note':'MOCK statement support'}],
                'primary_evidence_ids':[] if failed_dimension else [eid],'counterevidence_reviewed_ids':[],
                'requested_context':None,'suggested_revision':None}
        voters=[]
        for name in 'abc':
            seen=[0]
            def callback(p,name=name,seen=seen):
                seen[0]+=1
                return respond(p,missing=missing_seat and name=='a' and seen[0]==1)
            voters.append({'id':name,'model_id':'v6_'+name,'model_family':name,
                'client':MockLLMClient(callback,model_id='v6_'+name,model_family=name)})
        config={**f.config,'validator_contract_version':version,
            'planner':{'enabled':False,'strategy':'deterministic'},'ontology_extension':{'enabled':False},
            'consensus':{**f.config['consensus'],'max_rounds':1}}
        orchestrator=ReviewOrchestrator(f.repo,OntologyGuard(load_snapshot(f.base.ontology)),voters,config=config)
        result=orchestrator.audit_candidate(c['candidate_id'],1,f.index)
        return c,result,calls,orchestrator

    def test_full_committee_uses_derived_results_and_exports_raw_dimensions(self):
        c,result,payloads,_=self.run_review(mapping='not_covered')
        self.assertTrue(result['independent_review_completed'])
        self.assertEqual(result['source_support_result'],'supported')
        self.assertEqual(result['workflow_state'],'mapping_review')
        self.assertEqual(result['review_versions']['summary_policy_version'],v6.SUMMARY_POLICY_VERSION)
        self.assertEqual(len(payloads),3)
        for p in payloads:
            self.assertIn('dimensions',p['output_contract']['required'])
            self.assertNotIn('verdict',p['output_contract']['properties'])
            self.assertIn('ontology_catalog',p);self.assertNotIn('human_labels',p);self.assertNotIn('votes',p)
        exported=freeze_review_sample(self.f.repo,c['candidate_id'])
        self.assertEqual(exported['binding']['evidence_hash'],result['selected_evidence_hash'])
        for vote in exported['votes']:
            self.assertEqual(vote['verdict'],'uncertain')
            self.assertEqual(vote['schema_status'],'unknown');self.assertEqual(vote['schema_issue'],'not_covered')
            self.assertEqual(vote['raw_response']['dimensions']['mapping']['status'],'not_covered')

    def test_expression_failure_routes_to_revision_without_false_source_rejection(self):
        _,result,_,_=self.run_review(failed_dimension='qualifiers')
        self.assertTrue(result['independent_review_completed'])
        self.assertEqual(result['source_support_result'],'unresolved')
        self.assertEqual(result['consensus']['core_source_support_result'],'supported')
        self.assertEqual(result['workflow_state'],'revision_required')
        self.assertEqual(result['consensus']['invalid_votes'],0)

    def test_scope_policy_preserves_source_support_and_excludes_local_definition(self):
        _,result,_,_=self.run_review(scope='local_definition')
        self.assertTrue(result['independent_review_completed'])
        self.assertEqual(result['source_support_result'],'supported')
        self.assertEqual(result['workflow_state'],'excluded_by_scope')
        self.assertTrue(result['scope_policy_decision']['excluded_from_main_graph'])

    def test_failed_seat_recovery_retains_original_contract_and_other_two_votes(self):
        c,result,_,orchestrator=self.run_review(missing_seat=True)
        self.assertFalse(result['independent_review_completed'])
        self.assertEqual([len(v['client'].calls) for v in orchestrator.validators],[1,1,1])
        recovered=recover_frozen_audit(self.f.repo,orchestrator,c['candidate_id'],result['audit_id'],
            reason='MOCK restore omitted mapping dimension',extra_calls=1,extra_tokens=10000,extra_seconds=30)
        self.assertTrue(recovered['independent_review_completed'])
        self.assertEqual(recovered['source_support_result'],'supported')
        self.assertEqual([len(v['client'].calls) for v in orchestrator.validators],[2,1,1])
        feedback=orchestrator.validators[0]['client'].calls[-1]['correction_feedback']
        self.assertIn('dimensions',feedback['field_constraints']['required_fields'])
        self.assertNotIn('schema_status_allowed',feedback['field_constraints'])
        audit=self.f.repo.get_audit(c['candidate_id'],result['audit_id'])
        self.assertEqual({p['response_contract_version'] for p in audit['rounds']},{v6.CONTRACT_VERSION})
        self.assertEqual(len(audit['rounds']),1)

    def test_v61_freezes_three_delivery_profiles_and_template_payloads(self):
        c,result,payloads,_=self.run_review(version='validator_response_v6_1')
        self.assertTrue(result['independent_review_completed'])
        self.assertEqual(result['source_support_result'],'supported')
        self.assertEqual(result['review_versions']['output_delivery_version'],'typed_review_output_v1')
        for payload in payloads:
            self.assertEqual(payload['output_structure']['mode'],'json_template')
            self.assertEqual(set(payload['output_template']['dimensions']),set(v6.CHECK_NAMES))
        audit=self.f.repo.get_audit(c['candidate_id'],result['audit_id'])
        self.assertEqual(len(audit['rounds'][0]['validator_generation']),3)
        self.assertTrue(all(g['output_transport']['mode']=='json_template' for g in audit['rounds'][0]['validator_generation']))

    def test_v61_recovers_original_template_seat_without_replacing_other_votes(self):
        c,result,_,orchestrator=self.run_review(missing_seat=True,version='validator_response_v6_1')
        recovered=recover_frozen_audit(self.f.repo,orchestrator,c['candidate_id'],result['audit_id'],
            reason='MOCK restore original structured seat',extra_calls=1,extra_tokens=10000,extra_seconds=30)
        self.assertTrue(recovered['independent_review_completed'])
        self.assertEqual([len(v['client'].calls) for v in orchestrator.validators],[2,1,1])
        payload=orchestrator.validators[0]['client'].calls[-1]
        self.assertEqual(payload['output_structure']['mode'],'json_template')
        self.assertIn('output_template',payload)

    def test_v62_recovery_repairs_one_missing_dimension_and_keeps_other_seats(self):
        c,result,_,orchestrator=self.run_review(missing_seat=True,version='validator_response_v6_2',failed_dimension='qualifiers')
        self.assertFalse(result['independent_review_completed'])
        recovered=recover_frozen_audit(self.f.repo,orchestrator,c['candidate_id'],result['audit_id'],
            reason='MOCK bounded field-only recovery',extra_calls=1,extra_tokens=20000,extra_seconds=30)
        self.assertTrue(recovered['independent_review_completed'],recovered)
        self.assertEqual(recovered['workflow_state'],'revision_required')
        self.assertEqual(recovered['consensus']['core_source_support_result'],'supported')
        self.assertEqual([len(v['client'].calls) for v in orchestrator.validators],[2,1,1])
        self.assertEqual(orchestrator.validators[0]['client'].calls[-1]['task_type'],'triple_review_field_correction')
        audit=self.f.repo.get_audit(c['candidate_id'],result['audit_id'])
        corrected=next(v for v in audit['votes'] if v['voter_id']=='a')
        self.assertEqual(corrected['raw_response']['dimensions']['qualifiers']['status'],'fail')
        self.assertEqual(corrected['corrected_response']['dimensions']['qualifiers']['status'],'fail')

    def test_v62_explicit_recovery_budget_too_small_cannot_send_patch(self):
        c,result,_,orchestrator=self.run_review(missing_seat=True,version='validator_response_v6_2')
        recovered=recover_frozen_audit(self.f.repo,orchestrator,c['candidate_id'],result['audit_id'],
            reason='MOCK insufficient token envelope',extra_calls=1,extra_tokens=100,extra_seconds=30)
        self.assertFalse(recovered['independent_review_completed'])
        self.assertEqual([len(v['client'].calls) for v in orchestrator.validators],[1,1,1])


if __name__=='__main__':unittest.main()
