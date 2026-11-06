"""Auxiliary format compatibility must not repair substantive review content."""
import copy
import json
import unittest

from review_agent import response_contract_v5_1 as v51,response_contract_v5_2 as v52
from review_agent.consensus import Committee,aggregate_votes
from review_agent.llm_clients import LLMResponse,MockLLMClient
from review_agent.request_cache import validation_request_key
from review_agent.validator_contracts import versions_for,prompt_for,validate_vote
from review_agent.validators import judge_validator
from tests import test_dev_semantic_revision as fixtures


class ValidatorV52FormatTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.SemanticRevisionTests(methodName='runTest');f.setUp();self.f=f
        self.proposal={**f.proposal,'audit_id':'audit','round_id':'round',
            **versions_for({'validator_contract_version':v52.CONTRACT_VERSION}),
            'ontology_catalog':{'properties':[{'id':'R'}]},'ontology_catalog_hash':'catalog_hash',
            'ontology_check':{'status':'unknown','reason_codes':['RELATION_DEFINITION_UNAVAILABLE']}}
        self.proposal['candidate']['triple']['relation_id']='R'

    def response(self):
        raw=self.f.response()
        for check in raw['checks']: check['issue_code']=None
        return raw

    def voter(self,name='a',raw=None):
        raw=self.response() if raw is None else raw
        return {'id':name,'model_id':name,'model_family':name,
            'client':MockLLMClient(lambda p:raw,model_id=name,model_family=name)}

    def test_null_codes_preserve_pass_fail_unknown_and_not_applicable(self):
        for status in ('pass','fail','unknown','not_applicable'):
            with self.subTest(status=status):
                raw=self.response();check=next(c for c in raw['checks'] if c['check']=='entity_reference')
                check['status']=status
                if status in {'fail','unknown'}:raw.update(verdict='uncertain',expression_status='needs_revision' if status=='fail' else 'unresolved')
                before=copy.deepcopy(raw)
                self.assertEqual(v52.validate_response(raw),[])
                normalized=v52.normalize(raw)
                after=next(c for c in normalized['checks'] if c['check']=='entity_reference')
                self.assertEqual(after['status'],status);self.assertEqual(after['issue_code'],'')
                for key in ('note','evidence_ids'):self.assertEqual(after[key],check[key])
                for key in ('source_support','expression_status','knowledge_scope','mapping','supporting_evidence_ids','counter_evidence_ids'):
                    self.assertEqual(normalized[key],raw[key])
                self.assertEqual(raw,before)

    def test_missing_blank_auxiliary_codes_and_hint_are_audited(self):
        raw=self.f.response();raw['checks'][0].pop('issue_code')
        raw['checks'][1]['issue_code']='  \t';raw.pop('schema_change_hint')
        self.assertEqual(v52.validate_response(raw),[])
        normalized=v52.normalize(raw)
        self.assertIsNone(normalized['schema_change_hint'])
        changes=normalized['format_normalization']['changes']
        self.assertEqual(changes,[{'path':'/checks/0/issue_code','operation':'add','to':''},
            {'path':'/checks/1/issue_code','operation':'replace','from':'  \t','to':''},
            {'path':'/schema_change_hint','operation':'add','to':None}])
        self.assertNotIn('issue_code',raw['checks'][0]);self.assertNotIn('schema_change_hint',raw)

    def test_nullable_optional_schema_auxiliary_code_keeps_core_issue(self):
        raw=self.response();raw['schema_assessment']['issue_code']=None
        self.assertEqual(v52.validate_response(raw),[])
        normalized=v52.normalize(raw)
        self.assertEqual(normalized['schema_issue'],raw['schema_assessment']['issue'])
        self.assertEqual(normalized['schema_status'],raw['schema_assessment']['status'])
        normalized['schema_assessment']=copy.deepcopy(raw['schema_assessment'])
        self.assertEqual(validate_vote(normalized,v52.CONTRACT_VERSION),[])

    def test_missing_substantive_fields_are_not_filled(self):
        for field in ('mapping','evidence_assessments','source_support','primary_evidence_ids','checks',
                      'expression_status','knowledge_scope','schema_assessment','supporting_evidence_ids'):
            with self.subTest(field=field):
                raw=self.response();raw.pop(field)
                self.assertTrue(v52.validate_response(raw))
                self.assertNotIn(field,v52.canonicalize(raw)[0])
        for field in ('check','status','evidence_ids','note'):
            with self.subTest(check_field=field):
                raw=self.response();raw['checks'][0].pop(field)
                self.assertTrue(v52.validate_response(raw))

    def test_invalid_auxiliary_types_are_not_stringified(self):
        for bad in (True,0,1.5,[],{}):
            for location in ('check','schema_assessment'):
                with self.subTest(value=bad,location=location):
                    raw=self.response();obj=raw['checks'][0] if location=='check' else raw['schema_assessment']
                    obj['issue_code']=bad
                    self.assertTrue(v52.validate_response(raw))

    def test_semantic_inconsistencies_are_not_repaired(self):
        raw=self.response();raw['schema_assessment'].update(status='fail',issue='not_covered')
        self.assertTrue(v52.validate_response(raw))
        raw=self.response();raw['checks'][0]['status']='fail'
        self.assertTrue(v52.validate_response(raw))
        raw=self.response();raw['supporting_evidence_ids']=None
        self.assertTrue(v52.validate_response(raw))
        raw=self.response();raw['counter_evidence_ids']=[self.f.counter['evidence_id']]
        self.assertTrue(v52.validate_response(raw))
        raw=self.response();raw['checks'].pop()
        self.assertTrue(v52.validate_response(raw))
        raw=self.response();raw['checks'][0]['note']=' '
        self.assertTrue(v52.validate_response(raw))

    def test_one_request_preserves_original_raw_response_and_trace(self):
        raw=self.response();raw['schema_assessment']['issue_code']=None;before=copy.deepcopy(raw)
        voter=self.voter(raw=raw)
        result=judge_validator(voter,self.proposal,max_retries=1)
        self.assertEqual(result['response_status'],'ok')
        self.assertEqual(len(voter['client'].calls),1)
        self.assertEqual(result['raw_response'],before);self.assertEqual(raw,before)
        self.assertEqual(len(result['format_normalization']['changes']),10)
        self.assertEqual(result['schema_assessment'],before['schema_assessment'])
        self.assertTrue(result['ontology_disagreement'])
        self.assertEqual(validate_vote(result,v52.CONTRACT_VERSION),[])

    def test_original_json_text_is_preserved_verbatim(self):
        raw=self.response();text='```json\n'+json.dumps(raw,ensure_ascii=False,indent=2)+'\n```'
        voter=self.voter(raw=LLMResponse(text))
        result=judge_validator(voter,self.proposal)
        self.assertEqual(result['response_status'],'ok')
        self.assertEqual(result['raw_response'],text)

    def test_auxiliary_normalization_does_not_hide_missing_mapping(self):
        raw=self.response();raw.pop('mapping')
        result=judge_validator(self.voter(raw=raw),self.proposal)
        self.assertEqual(result['response_status'],'invalid_contract')
        self.assertIn('mapping',result['error'])
        self.assertNotIn('mapping',result['raw_response'])
        self.assertEqual(len(result['format_normalization']['changes']),9)

    def test_unknown_and_non_substantive_evidence_ids_are_still_rejected(self):
        for eid in ('invented',self.f.marker['evidence_id']):
            with self.subTest(evidence_id=eid):
                raw=self.response();raw['primary_evidence_ids']=[eid]
                result=judge_validator(self.voter(raw=raw),self.proposal)
                self.assertEqual(result['response_status'],'invalid_reference')
                self.assertEqual(result['primary_evidence_ids'],[eid])

    def test_normalization_metadata_is_server_owned(self):
        for field in ('format_normalization','raw_response'):
            raw=self.response();raw[field]={}
            self.assertTrue(v52.validate_response(raw))

    def test_three_seats_still_required_and_mapping_guard_still_blocks_admission(self):
        votes=[judge_validator(self.voter(n),self.proposal) for n in 'abc']
        for vote in votes:self.assertEqual(vote['response_status'],'ok')
        committee=Committee(list('abc'))
        result=aggregate_votes(votes,committee,proposal=self.proposal,proposal_digest='p')
        self.assertEqual(result['source_support_consensus']['result'],'supported')
        self.assertEqual(result['effective_mapping_status'],'unassigned')
        self.assertEqual(result['admission_consensus']['result'],'unresolved')
        result=aggregate_votes(votes[:2],committee,proposal=self.proposal,proposal_digest='p')
        self.assertEqual(result['source_support_consensus']['result'],'unresolved')
        votes[0]['response_contract_version']=v51.CONTRACT_VERSION
        result=aggregate_votes(votes,committee,proposal=self.proposal,proposal_digest='p')
        self.assertEqual(result['source_support_consensus']['result'],'unresolved')

    def test_old_frozen_contract_remains_strict_and_has_distinct_request_identity(self):
        self.assertTrue(v51.validate_response(self.response()))
        old_versions=versions_for({'validator_contract_version':v51.CONTRACT_VERSION})
        self.assertEqual(old_versions['response_contract_hash'],'06af7a0f9d0fae6ddfc9818e7a591e0f86cc30e6bffdad3f4c210dadfbaca525')
        self.assertEqual(old_versions['prompt_hash'],'09dcd59dfc8bfd6879ae5cacd75c068975f9d49e912b08efdc1f2091133cd593')
        self.assertNotEqual(old_versions['response_contract_hash'],self.proposal['response_contract_hash'])
        voter=self.voter()
        old={**self.proposal,**old_versions}
        self.assertNotEqual(validation_request_key(voter,old),validation_request_key(voter,self.proposal))
        self.assertIn('issue_code',prompt_for(self.proposal))
        with self.assertRaises(ValueError):prompt_for({**self.proposal,'prompt_hash':'wrong'})


if __name__=='__main__':unittest.main()
