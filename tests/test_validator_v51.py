"""Transport shape changes preserve semantic dissent and admission constraints."""
import copy
import unittest
from review_agent import response_contract_v5 as v5, response_contract_v5_1 as v51
from review_agent.validator_contracts import contract_for,proposal_errors,validate_vote,versions_for,prompt_for
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.validators import judge_validator,vote_binding
from review_agent.llm_clients import MockLLMClient
from review_agent.consensus import Committee,aggregate_votes
from tests import test_dev_semantic_revision as fixtures


class ValidatorV51Tests(unittest.TestCase):
    def setUp(self):
        f=fixtures.SemanticRevisionTests(methodName='runTest');f.setUp()
        self.f=f
        self.proposal={**f.proposal,'audit_id':'audit','round_id':'round',
            'response_contract_version':v51.CONTRACT_VERSION,'ontology_catalog':{'properties':[{'id':'R'}]},
            'ontology_catalog_hash':'catalog_hash',
            'ontology_check':{'status':'unknown','reason_codes':['RELATION_DEFINITION_UNAVAILABLE']}}
        self.proposal['candidate']['triple']['relation_id']='R'

    def test_optional_hint_and_auxiliary_issue_code_do_not_change_checks(self):
        raw=self.f.response();raw.pop('schema_change_hint');raw['schema_assessment']['issue_code']='none'
        self.assertEqual(v51.validate_response(raw),[])
        self.assertTrue(v5.validate_response(raw))
        normalized=v51.normalize(raw);normalized['schema_assessment']=raw['schema_assessment']
        self.assertEqual(validate_vote(normalized,v51.CONTRACT_VERSION),[])
        for field in ('mapping','evidence_assessments'):
            broken=copy.deepcopy(raw);broken.pop(field)
            self.assertTrue(v51.validate_response(broken))

    def test_missing_definition_dissent_is_one_valid_vote_without_retry(self):
        calls=[]
        def respond(payload): calls.append(payload);return self.f.response()
        voter={'id':'a','model_id':'a','model_family':'a','client':MockLLMClient(respond,model_id='a',model_family='a')}
        result=judge_validator(voter,self.proposal,max_retries=1)
        self.assertEqual(result['response_status'],'ok')
        self.assertEqual(len(calls),1)
        self.assertEqual(result['mapping']['status'],'matched')
        self.assertTrue(result['ontology_disagreement'])
        self.assertIn('RELATION_DEFINITION_UNAVAILABLE',result['mapping_review_reasons'])
        self.assertEqual(proposal_errors(self.f.response(),self.proposal),[])
        self.assertTrue(proposal_errors(self.f.response(),{**self.proposal,'response_contract_version':v5.CONTRACT_VERSION}))

    def test_references_counterevidence_and_nine_checks_remain_strict(self):
        raw=self.f.response();raw['mapping']['relation_id']='invented'
        self.assertTrue(proposal_errors(raw,self.proposal))
        raw=self.f.response();raw['checks'].pop()
        self.assertTrue(v51.validate_response(raw))
        raw=self.f.response();raw['counter_evidence_ids']=[self.f.counter['evidence_id']]
        self.assertTrue(v51.validate_response(raw))

    def test_guard_separates_structural_pass_and_missing_definition(self):
        guard=OntologyGuard(load_snapshot(fixtures.ONTOLOGY))
        c=copy.deepcopy(self.proposal['candidate']);c['triple'].update(relation_id='R',head_type='C',tail_type='C')
        self.assertEqual(guard.check(c)['status'],'pass')
        guard.require_explicit_mapping=True;guard.require_relation_definition=True
        result=guard.check(c)
        self.assertEqual(result['structural_status'],'pass')
        self.assertEqual(result['status'],'unknown')
        self.assertEqual(result['mapping_status'],'unassigned')
        guard.relations['R']['definition']='A relation between two devices'
        self.assertEqual(guard.check(c)['status'],'pass')

    def test_source_quorum_survives_mapping_dissent_but_admission_does_not(self):
        votes=[]
        for name in 'abc':
            voter={'id':name,'model_id':name,'model_family':name,
                'client':MockLLMClient(lambda p:self.f.response(),model_id=name,model_family=name)}
            votes.append(judge_validator(voter,self.proposal))
        result=aggregate_votes(votes,Committee(list('abc')),proposal=self.proposal,proposal_digest='p')
        self.assertEqual(result['source_support_consensus']['result'],'supported')
        self.assertEqual(result['mapping_status'],'matched')
        self.assertEqual(result['effective_mapping_status'],'unassigned')
        self.assertEqual(result['admission_consensus']['result'],'unresolved')
        changed=copy.deepcopy(self.proposal);changed['ontology_check']={'status':'pass'}
        changed['candidate']['triple']['relation_id']='R_other'
        result=aggregate_votes(votes,Committee(list('abc')),proposal=changed,proposal_digest='p')
        self.assertEqual(result['admission_consensus']['result'],'unresolved')

    def test_new_prompt_and_contract_are_frozen_independently(self):
        config={'validator_contract_version':v51.CONTRACT_VERSION}
        versions=versions_for(config)
        self.assertNotEqual(versions['response_contract_hash'],versions_for({'validator_contract_version':v5.CONTRACT_VERSION})['response_contract_hash'])
        self.assertIn('mapping',prompt_for(versions))
        versions['prompt_hash']='wrong'
        with self.assertRaises(ValueError): prompt_for(versions)


if __name__=='__main__':unittest.main()
