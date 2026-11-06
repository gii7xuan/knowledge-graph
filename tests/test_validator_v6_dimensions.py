"""Model dimensions are authoritative; server summaries cannot be model-authored."""
import copy
import json
import unittest
from review_agent import response_contract_v6 as v6,response_contract_v5_2 as v52
from review_agent.consensus import Committee,aggregate_votes
from review_agent.contracts import sha256_json,proposal_digest
from review_agent.llm_clients import MockLLMClient
from review_agent.request_cache import validation_request_key
from review_agent.validator_contracts import versions_for,prompt_for,validate_vote,proposal_errors
from review_agent.validators import judge_validator,validator_payload
from review_agent.planner_view import summarize_vote
from tests import test_dev_semantic_revision as fixtures


class ValidatorV6DimensionTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.SemanticRevisionTests(methodName='runTest');f.setUp();self.f=f
        self.proposal={**f.proposal,'audit_id':'audit','round_id':'round',
            **versions_for({'validator_contract_version':v6.CONTRACT_VERSION}),
            'ontology_catalog':{'properties':[{'id':'R'}]},'ontology_catalog_hash':'catalog_hash',
            'ontology_check':{'status':'pass'}}
        self.proposal['candidate']['triple']['relation_id']='R'

    def response(self,mapping='matched',source='supported',scope='general'):
        eid=self.f.good['evidence_id']
        dims={n:{'status':'pass','evidence_ids':[eid],'note':'MOCK frozen evidence reviewed','issue_code':None} for n in v6.CHECK_NAMES}
        dims['triple_support']['status']=source;dims['knowledge_scope']['status']=scope
        dims['mapping'].update(status=mapping,relation_id='R' if mapping=='matched' else None,competing_relation_ids=[])
        roles=[{'evidence_id':eid,'role':'support','note':'MOCK direct source'}]
        primary=[eid]
        if source=='contradicted':
            counter=self.f.counter['evidence_id'];dims['triple_support']['evidence_ids']=[counter]
            dims['counterevidence'].update(status='fail',evidence_ids=[counter])
            roles=[{'evidence_id':counter,'role':'counter','note':'MOCK material contradiction'}];primary=[]
        if source=='insufficient':
            dims['evidence_sufficiency']['status']='unknown';roles=[];primary=[]
        return {'dimensions':dims,'evidence_assessments':roles,'primary_evidence_ids':primary,
            'counterevidence_reviewed_ids':[],'requested_context':None,'suggested_revision':None}

    def voter(self,name='a',raw=None):
        return {'id':name,'model_id':name,'model_family':name,
            'client':MockLLMClient([raw if raw is not None else self.response()],model_id=name,model_family=name)}

    def vote(self,name='a',raw=None,proposal=None):
        return judge_validator(self.voter(name,raw),proposal or self.proposal)

    def test_model_has_nine_dimensions_and_no_duplicate_summary_fields(self):
        raw=self.response();self.assertEqual(v6.validate_response(raw),[])
        self.assertEqual(set(raw['dimensions']),set(v6.CHECK_NAMES))
        for name in ('verdict','source_support','expression_status','schema_assessment','schema_status','schema_issue',
                     'knowledge_scope','mapping','checks','reason_codes','supporting_evidence_ids','counter_evidence_ids'):
            with self.subTest(field=name):
                self.assertNotIn(name,v6.RESPONSE_SCHEMA['properties'])
                invalid=copy.deepcopy(raw);invalid[name]=None
                self.assertTrue(v6.validate_response(invalid))

    def test_not_covered_generates_unknown_schema_and_uncertain_verdict(self):
        raw=self.response(mapping='not_covered');before=copy.deepcopy(raw)
        self.assertEqual(v6.validate_response(raw),[])
        vote=v6.normalize(raw)
        self.assertEqual((vote['schema_status'],vote['schema_issue'],vote['verdict']),('unknown','not_covered','uncertain'))
        self.assertEqual(vote['source_support'],'supported');self.assertEqual(vote['expression_status'],'faithful')
        self.assertIn('SCHEMA_GAP',vote['reason_codes']);self.assertNotIn('CONSTRAINT_VIOLATION',vote['reason_codes'])
        self.assertEqual(raw,before)

    def test_each_mapping_opinion_has_one_deterministic_summary(self):
        for state,(mapped,schema,issue,check) in v6.MAPPING_SUMMARY.items():
            with self.subTest(status=state):
                raw=self.response(mapping=state);self.assertEqual(v6.validate_response(raw),[])
                normalized=v6.normalize(raw)
                self.assertEqual(normalized['mapping_diagnosis'],state)
                self.assertEqual(normalized['mapping']['status'],mapped)
                self.assertEqual((normalized['schema_status'],normalized['schema_issue']),(schema,issue))
                self.assertEqual(next(c['status'] for c in normalized['checks'] if c['check']=='mapping'),check)
                self.assertEqual(normalized['verdict'],'valid' if state=='matched' else 'uncertain')

    def test_source_support_and_expression_failure_remain_independent(self):
        for dimension in ('entity_reference','relation_semantics','relation_direction','qualifiers'):
            with self.subTest(dimension=dimension):
                raw=self.response();raw['dimensions'][dimension]['status']='fail';raw['primary_evidence_ids']=[]
                self.assertEqual(v6.validate_response(raw),[])
                normalized=v6.normalize(raw)
                self.assertEqual(normalized['source_support'],'supported')
                self.assertEqual(normalized['expression_status'],'needs_revision')
                self.assertEqual(normalized['verdict'],'uncertain')
                self.assertEqual(normalized['dimensions'][dimension]['status'],'fail')

    def test_insufficient_and_contradicted_generate_different_outcomes(self):
        for source,verdict,expression in [('insufficient','uncertain','unresolved'),('contradicted','invalid','needs_revision')]:
            raw=self.response(source=source);self.assertEqual(v6.validate_response(raw),[])
            normalized=v6.normalize(raw)
            self.assertEqual((normalized['verdict'],normalized['expression_status']),(verdict,expression))
            self.assertEqual(normalized['source_support'],source)
            self.assertIn('SOURCE_CONTRADICTS' if source=='contradicted' else 'EVIDENCE_INSUFFICIENT',normalized['reason_codes'])

    def test_support_and_counter_collections_come_only_from_roles(self):
        raw=self.response();counter=self.f.counter['evidence_id']
        raw['evidence_assessments'].append({'evidence_id':counter,'role':'counter','note':'MOCK unresolved material counter'})
        raw['dimensions']['counterevidence'].update(status='fail',evidence_ids=[counter])
        self.assertEqual(v6.validate_response(raw),[])
        normalized=v6.normalize(raw)
        self.assertEqual(normalized['counter_evidence_ids'],[counter]);self.assertEqual(normalized['verdict'],'uncertain')
        self.assertEqual(normalized['supporting_evidence_ids'],[self.f.good['evidence_id']])
        votes=[self.vote(n,raw) for n in 'abc']
        result=aggregate_votes(votes,Committee(list('abc')),proposal=self.proposal,proposal_digest='p')
        self.assertEqual(result['counter_evidence_ids'],[counter]);self.assertEqual(result['result'],'unresolved')

    def test_program_guards_and_three_seats_are_still_required(self):
        votes=[self.vote(n) for n in 'abc'];committee=Committee(list('abc'))
        for vote in votes:self.assertEqual(vote['response_status'],'ok')
        result=aggregate_votes(votes,committee,proposal=self.proposal,proposal_digest='p')
        self.assertEqual(result['admission_consensus']['result'],'accept_consensus')
        result=aggregate_votes(votes[:2],committee,proposal=self.proposal,proposal_digest='p')
        self.assertEqual(result['source_support_consensus']['result'],'unresolved')
        unknown={**self.proposal,'ontology_check':{'status':'unknown','reason_codes':['RELATION_DEFINITION_UNAVAILABLE']}}
        vote=self.vote(proposal=unknown)
        self.assertEqual(vote['response_status'],'ok');self.assertEqual(vote['mapping']['status'],'matched')
        self.assertTrue(vote['ontology_disagreement']);self.assertEqual(validate_vote(vote,v6.CONTRACT_VERSION),[])
        self.assertIn('ONTOLOGY_DISAGREEMENT',vote['program_reason_codes'])
        self.assertNotIn('ONTOLOGY_DISAGREEMENT',vote['reason_codes'])
        result=aggregate_votes(votes,committee,proposal=unknown,proposal_digest='p')
        self.assertEqual(result['admission_consensus']['result'],'unresolved')
        self.assertEqual(result['effective_mapping_status'],'unassigned')

    def test_raw_response_and_model_dimensions_remain_available(self):
        raw=self.response();before=copy.deepcopy(raw);vote=self.vote(raw=raw)
        self.assertEqual(vote['response_status'],'ok');self.assertEqual(vote['raw_response'],before);self.assertEqual(raw,before)
        self.assertEqual(vote['dimensions']['triple_support']['status'],'supported')
        self.assertEqual(len(vote['format_normalization']['changes']),10)
        self.assertEqual(vote['summary_derivation']['policy_hash'],self.proposal['summary_policy_hash'])
        self.assertEqual(summarize_vote(vote)['summary_derivation'],vote['summary_derivation'])

    def test_derived_fields_cannot_be_overwritten_in_a_stored_vote(self):
        vote=self.vote();self.assertEqual(validate_vote(vote,v6.CONTRACT_VERSION),[])
        for field in v6.NORMALIZED_COMPARE_FIELDS:
            with self.subTest(field=field):
                tampered=copy.deepcopy(vote);tampered[field]=None
                self.assertTrue(validate_vote(tampered,v6.CONTRACT_VERSION))

    def test_missing_dimensions_and_core_fields_are_not_guessed(self):
        for name in v6.CHECK_NAMES:
            raw=self.response();raw['dimensions'].pop(name)
            self.assertTrue(any('dimensions.'+name in e for e in v6.validate_response(raw)))
        for name in ('status','evidence_ids','note'):
            raw=self.response();raw['dimensions']['entity_reference'].pop(name)
            self.assertTrue(v6.validate_response(raw))
        raw=self.response();raw['dimensions']['mapping']['relation_id']=None
        self.assertTrue(v6.validate_response(raw))

    def test_actual_reference_and_counterevidence_rules_remain_strict(self):
        raw=self.response();raw['evidence_assessments'][0]['evidence_id']='invented'
        self.assertEqual(self.vote(raw=raw)['response_status'],'invalid_reference')
        raw=self.response();raw['primary_evidence_ids']=[self.f.marker['evidence_id']]
        self.assertEqual(self.vote(raw=raw)['response_status'],'invalid_reference')
        raw=self.response();raw['evidence_assessments']*=2
        self.assertTrue(v6.validate_response(raw))
        raw=self.response(source='contradicted');raw['evidence_assessments']=[]
        self.assertTrue(v6.validate_response(raw))
        raw=self.response();raw['evidence_assessments'].append({'evidence_id':self.f.counter['evidence_id'],'role':'counter','note':'real counter'})
        self.assertTrue(v6.validate_response(raw))
        raw=self.response();raw['dimensions']['counterevidence']['evidence_ids']=[]
        self.assertTrue(v6.validate_response(raw))

    def test_auxiliary_compatibility_does_not_alter_dimension_judgments(self):
        raw=self.response();raw['dimensions']['qualifiers'].update(status='fail',issue_code=' \t ')
        raw['dimensions']['entity_reference'].pop('issue_code');raw['primary_evidence_ids']=[]
        vote=self.vote(raw=raw)
        self.assertEqual(vote['response_status'],'ok');self.assertEqual(vote['expression_status'],'needs_revision')
        self.assertEqual(vote['dimensions']['qualifiers']['status'],'fail')
        raw=self.response();raw['dimensions']['qualifiers']['issue_code']=False
        self.assertTrue(v6.validate_response(raw))

    def test_schema_and_source_opinions_cannot_be_replaced_by_diagnostic_codes(self):
        raw=self.response();raw['dimensions']['entity_reference']['issue_code']='SOURCE_CONTRADICTS'
        vote=self.vote(raw=raw)
        self.assertEqual(vote['source_support'],'supported');self.assertNotIn('SOURCE_CONTRADICTS',vote['reason_codes'])
        self.assertEqual(vote['raw_response']['dimensions']['entity_reference']['issue_code'],'SOURCE_CONTRADICTS')

    def test_corrective_request_uses_only_the_new_dimensions_contract(self):
        raw=self.response();missing=copy.deepcopy(raw);missing['dimensions'].pop('mapping')
        voter=self.voter();voter['client']=MockLLMClient([missing,raw],model_id='a',model_family='a')
        vote=judge_validator(voter,self.proposal,max_retries=1)
        self.assertEqual(vote['response_status'],'ok');self.assertEqual(len(voter['client'].calls),2)
        feedback=voter['client'].calls[1]['correction_feedback']
        self.assertIn('dimensions',feedback['field_constraints']['required_fields'])
        self.assertNotIn('schema_status_allowed',feedback['field_constraints'])
        self.assertIn('dimensions.mapping',feedback['error'])

    def test_new_payload_freezes_summary_policy_and_old_contracts_are_unchanged(self):
        payload=validator_payload(self.proposal)
        self.assertEqual(payload['server_summary_policy']['summary_policy_hash'],self.proposal['summary_policy_hash'])
        self.assertIn('diagnostic_codes',payload);self.assertNotIn('reason_codes',payload)
        old=versions_for({'validator_contract_version':v52.CONTRACT_VERSION})
        self.assertEqual(old['response_contract_hash'],'3f68310d202b16b688519ca33cf1fc76661809efeb0579a291a28a8109e65cca')
        self.assertEqual(old['prompt_hash'],'f814b70f111144c289c30e19aa2bc6425b704632b9dc174bd251eda82f0a2637')
        self.assertNotIn('summary_policy_hash',old)
        old_proposal={**self.proposal,**old};voter=self.voter()
        self.assertNotEqual(validation_request_key(voter,self.proposal),validation_request_key(voter,old_proposal))
        bad={**self.proposal,'summary_policy_hash':'wrong'}
        with self.assertRaises(ValueError):prompt_for(bad)
        seed={**self.proposal,'candidate_id':self.proposal['candidate']['candidate_id'],'canonical_triple':{}}
        self.assertNotEqual(proposal_digest(seed),proposal_digest({**seed,'summary_policy_hash':'different'}))


if __name__=='__main__':unittest.main()
