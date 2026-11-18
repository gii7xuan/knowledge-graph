from copy import deepcopy
import unittest

from review_agent.channel_probe import judge_once
from review_agent.contracts import sha256_json
from review_agent.llm_clients import LLMResponse,MockLLMClient
from evaluation.qwen_channel_diagnostics import proposal,PLAN,allowed


class ChannelProbeTests(unittest.TestCase):
    def voter(self,response):return {'id':'qwen_diagnostic','model_id':'Qwen','model_family':'qwen',
        'model_identity':'qwen','client':MockLLMClient([response])}

    def test_nonce_reply_is_not_a_review_vote(self):
        p=proposal('synthetic_unit_study',PLAN[0]);v=self.voter(LLMResponse({'probe_token':p['expected_token']},finish_reason='stop'))
        result=judge_once(v,p)
        self.assertEqual(result['response_status'],'ok');self.assertFalse(result['counts_as_review_vote'])
        self.assertEqual(len(v['client'].calls),1)

    def test_empty_truncated_reply_is_output_incomplete_not_bad_json_or_unknown(self):
        p=proposal('synthetic_unit_study',PLAN[0])
        v=self.voter(LLMResponse('',finish_reason='length',input_tokens=81,output_tokens=64))
        result=judge_once(v,p)
        self.assertEqual(result['response_status'],'output_incomplete')
        self.assertEqual(result['output_tokens'],64)
        self.assertFalse(result.get('outcome_unknown',False))
        self.assertFalse(result['counts_as_review_vote'])

    def test_tampered_probe_never_calls_provider(self):
        p=proposal('synthetic_unit_study',PLAN[0]);p['wire_payload']['probe_token']='tampered';v=self.voter({})
        result=judge_once(v,p);self.assertTrue(result['not_sent']);self.assertEqual(v['client'].calls,[])

    def test_first_minimal_failure_and_two_unknowns_stop_more_probes(self):
        result={'small_json_stream':{'response_status':'api_error','outcome_unknown':True}}
        for row in PLAN[1:]:self.assertFalse(allowed(row[0],result)[0])
        result={'small_json_stream':{'response_status':'ok'},'small_schema_stream':{'outcome_unknown':True},
                'small_json_nonstream':{'outcome_unknown':True}}
        self.assertFalse(allowed('v8_template_sized_stream',result)[0])

    def test_http200_shape_failure_is_transport_evidence_but_not_strict_schema_proof(self):
        result={'small_json_stream':{'response_status':'invalid_contract','finish_reason':'stop','transport_metrics':{'http_status':200}},
                'small_schema_stream':{'response_status':'invalid_contract'}}
        self.assertTrue(allowed('small_schema_stream',result)[0])
        self.assertFalse(allowed('v8_schema_small_stream',result)[0])

    def test_schema_failure_does_not_trigger_equivalent_request_resend(self):
        result={'small_json_stream':{'response_status':'ok'},'small_schema_stream':{'response_status':'api_error','outcome_unknown':True}}
        self.assertFalse(allowed('v8_schema_small_stream',result)[0])
        self.assertFalse(allowed('v8_schema_sized_stream',result)[0])
        self.assertTrue(allowed('v8_template_sized_stream',result)[0])
        strict=proposal('new_independent_fixture',PLAN[4]);template=proposal('new_independent_fixture',PLAN[5])
        self.assertNotEqual(strict['wire_payload']['candidate']['candidate_id'],template['wire_payload']['candidate']['candidate_id'])

    def test_sized_input_is_generated_and_bound(self):
        p=proposal('synthetic_sized_fixture',PLAN[4])
        self.assertTrue(p['synthetic_only']);self.assertTrue(p['wire_payload']['ontology_catalog']['synthetic_only'])
        self.assertEqual(p['proposal_digest'],sha256_json({k:v for k,v in p.items() if k!='proposal_digest'}))
        self.assertEqual(p['wire_payload']['response_contract_version'],'validator_response_v8')
        self.assertTrue(p['user_suffix'])
        self.assertNotIn('火炮',str(p['wire_payload']))


if __name__=='__main__':unittest.main()
