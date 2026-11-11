"""Regression boundaries for repeated citations, truncated repairs and slow streams."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from review_agent import response_contract_v8 as v8
from review_agent.llm_clients import LLMResponse,OpenAICompatibleClient
from review_agent.response_corrections import make_plan
from review_agent.targeted_validation import judge_repair_once
from review_agent.validator_contracts import prompt_for
from review_agent.validators import _judge_validator_once,validator_payload
from review_agent.request_cache import validation_request_key
from review_agent.contracts import proposal_digest
from review_agent.output_structure import resolve_profile
from tests import test_field_spec as fixtures

ROOT=Path(__file__).resolve().parents[1]


class MissingVoteCausesTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.FieldSpecIntegrationTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        _,_,o,a,self.p=self.f.run_case(frequency=False)
        self.voter=o.validators[0];self.raw=deepcopy(a['votes'][0]['raw_response'])

    def test_new_prompt_has_separate_identity_and_historical_prompt_is_exact(self):
        old=(ROOT/'prompts/triple_validator_v8.txt').read_text(encoding='utf-8')
        historical={**self.p,'prompt_version':'triple_validator_v8_field_rules_v1',
            'prompt_hash':hashlib.sha256(old.encode()).hexdigest()}
        historical['proposal_digest']=proposal_digest(historical)
        self.assertEqual(prompt_for(historical),old)
        self.assertEqual(prompt_for(self.p),v8.SYSTEM_PROMPT)
        self.assertNotEqual(validation_request_key(self.voter,historical),validation_request_key(self.voter,self.p))
        with self.assertRaisesRegex(ValueError,'prompt hash mismatch'):
            bad={**historical,'prompt_hash':v8.PROMPT_HASH};bad['proposal_digest']=proposal_digest(bad)
            prompt_for(bad)
        with self.assertRaisesRegex(ValueError,'unsupported frozen V8 prompt'):
            bad={**self.p,'prompt_version':'unknown-v8-prompt'};bad['proposal_digest']=proposal_digest(bad)
            prompt_for(bad)

    def test_new_prompt_does_not_accept_absent_counter_review_or_contradictory_findings(self):
        raw=deepcopy(self.raw);raw['dimensions']['counterevidence']['evidence_ids']=[]
        self.assertTrue(v8.validate_response(raw))
        raw=deepcopy(self.raw);raw['dimensions']['relation_semantics']['status']='unknown'
        self.assertTrue(v8.validate_response(raw),'an unresolved check still needs a substantive finding')
        raw=deepcopy(self.raw);raw['evidence_assessments'][0]['role']='counter'
        self.assertTrue(v8.validate_response(raw),'a genuine counter cannot be removed to fill citations')

    def test_final_output_instruction_is_frozen_and_old_v2_wire_is_unchanged(self):
        with patch.object(self.voter['client'],'complete_json',return_value=LLMResponse(self.raw)) as call:
            result=_judge_validator_once(self.voter,self.p)
        self.assertEqual(result['response_status'],'ok')
        self.assertEqual(call.call_args.kwargs['user_suffix'],v8.USER_SUFFIX)
        self.assertEqual(call.call_args.args[0]['output_instruction'],v8.USER_SUFFIX)
        legacy=(ROOT/'prompts/triple_validator_v8_citations_v2.txt').read_text(encoding='utf-8')
        p={**self.p,'prompt_version':'triple_validator_v8_citations_v2','prompt_hash':hashlib.sha256(legacy.encode()).hexdigest()}
        p['proposal_digest']=proposal_digest(p)
        self.assertEqual(prompt_for(p),legacy)
        self.assertNotIn('output_instruction',validator_payload(p,self.voter))

    def repair(self,response):
        raw=deepcopy(self.raw);raw['dimensions']['counterevidence']['evidence_ids']=[]
        plan,errors=make_plan(raw,self.p,self.p['correction_policy']);self.assertFalse(errors)
        feedback={'original_response':raw,'original_raw_response':deepcopy(raw),'repair_plan':plan}
        with patch.object(self.voter['client'],'complete_json',return_value=response):
            return judge_repair_once(self.voter,self.p,feedback),raw

    def test_even_parseable_token_limited_patch_never_becomes_a_vote(self):
        eid=self.raw['dimensions']['counterevidence']['evidence_ids'][0]
        result,raw=self.repair(LLMResponse({'repairs':{'field_0':[eid]}},finish_reason='length',
            output_tokens=1536,reasoning_tokens=1500,returned_model_id='fixture',
            transport_metrics={'visible_chars':10,'first_content_ms':100}))
        self.assertEqual(result['response_status'],'output_incomplete')
        self.assertTrue(result['correction_terminal']);self.assertFalse(result['retryable'])
        self.assertEqual(result['raw_response'],raw);self.assertNotIn('corrected_response',result)
        self.assertEqual(result['finish_reason'],'length');self.assertEqual(result['reasoning_tokens'],1500)

    def test_empty_patch_keeps_usage_finish_and_original_without_inventing_a_repair(self):
        result,raw=self.repair(LLMResponse('',finish_reason='stop',input_tokens=2500,output_tokens=1536))
        self.assertEqual(result['error_code'],'EMPTY_MODEL_RESPONSE')
        self.assertEqual(result['response_status'],'invalid_json');self.assertEqual(result['raw_response'],raw)
        self.assertEqual(result['input_tokens'],2500);self.assertEqual(result['finish_reason'],'stop')
        self.assertNotIn('corrected_response',result)

    def test_empty_initial_response_is_not_reported_as_a_substantive_judgment(self):
        with patch.object(self.voter['client'],'complete_json',return_value=LLMResponse('',finish_reason='length',output_tokens=8192)):
            result=_judge_validator_once(self.voter,self.p)
        self.assertEqual(result['response_status'],'output_incomplete')
        self.assertEqual(result['output_tokens'],8192);self.assertFalse(result['retryable'])


class StreamingDiagnosticsTests(unittest.TestCase):
    def test_current_v8_qwen_schema_capability_is_bound_to_the_actual_endpoint_and_stream(self):
        client=OpenAICompatibleClient(api_key='fixture',base_url='https://43.143.151.41:3000/v1',
            model_id='Qwen',model_family='qwen',stream_responses=True)
        voter={'id':'a','model_id':'Qwen','client':client}
        self.assertEqual(resolve_profile(voter,{},v8.RESPONSE_SCHEMA)['mode'],'json_schema')
        client.stream_responses=False
        self.assertEqual(resolve_profile(voter,{},v8.RESPONSE_SCHEMA)['mode'],'json_template')
        client.stream_responses=True;client.base_url='https://unverified.invalid/v1'
        self.assertEqual(resolve_profile(voter,{},v8.RESPONSE_SCHEMA)['mode'],'json_template')

    def client(self,chunks):
        client=OpenAICompatibleClient(api_key='fixture',base_url='https://fixture.invalid/v1',
            model_id='m',model_family='f',stream_responses=True)
        class Stream:
            closed=False
            def __iter__(self):
                for chunk in chunks:
                    if isinstance(chunk,Exception):raise chunk
                    yield chunk
            def close(self):self.closed=True
        stream=Stream()
        client._client=NS(chat=NS(completions=NS(create=lambda **kwargs:stream)))
        return client,stream

    @staticmethod
    def chunk(content=None,reasoning=None,finish=None):
        return NS(id='id',model='m',usage=None,
            choices=[NS(delta=NS(content=content,reasoning_content=reasoning),finish_reason=finish)])

    def test_reasoning_only_output_exposes_counts_without_retaining_private_text(self):
        chunks=[self.chunk(reasoning='private text'),self.chunk(finish='length'),
            NS(choices=[],usage=NS(prompt_tokens=8,completion_tokens=1536,completion_tokens_details=NS(reasoning_tokens=1536)))]
        client,stream=self.client(chunks)
        with patch('review_agent.llm_clients.time.monotonic',side_effect=[0,1,3,4,4]):
            response=client.complete_json({})
        self.assertEqual(response.content,'');self.assertEqual(response.finish_reason,'length')
        metrics=response.transport_metrics
        self.assertEqual(metrics['first_chunk_ms'],1000);self.assertIsNone(metrics['first_content_ms'])
        self.assertEqual(metrics['terminal_ms'],3000);self.assertEqual(metrics['max_inter_chunk_gap_ms'],2000)
        self.assertNotIn('private text',json.dumps(metrics));self.assertEqual(metrics['reasoning_chars'],12)
        self.assertEqual(response.reasoning_tokens,1536);self.assertTrue(stream.closed)

    def test_interrupted_stream_retains_untrusted_partial_diagnostics_and_remains_unknown(self):
        client,stream=self.client([self.chunk(content='{'),ConnectionError('fixture disconnected')])
        with self.assertRaises(ConnectionError) as raised:client.complete_json({})
        self.assertEqual(raised.exception.llm_response.content,'{')
        self.assertIsNone(raised.exception.llm_response.finish_reason)
        self.assertIsNotNone(raised.exception.llm_response.transport_metrics['first_content_ms'])
        self.assertTrue(stream.closed)

    def test_missing_terminal_marker_is_never_a_complete_response(self):
        client,_=self.client([self.chunk(content='{}')])
        with self.assertRaises(ConnectionError) as raised:client.complete_json({})
        self.assertEqual(raised.exception.llm_response.content,'{}')


if __name__=='__main__':unittest.main()
