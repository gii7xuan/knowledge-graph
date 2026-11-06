"""Verified endpoint modes, wire schemas, local validation and immutable requests."""
import copy
import json
from pathlib import Path
import shutil
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

from review_agent import response_contract_v6 as v6,response_contract_v6_1 as v61
from review_agent.contracts import sha256_json,proposal_digest
from review_agent.llm_clients import OpenAICompatibleClient
from review_agent.output_structure import endpoint_binding,resolve_profile
from review_agent.repository import ReviewRepository
from review_agent.request_cache import validation_request_key
from review_agent.request_identity import semantic_request_key
from review_agent.schema_validation import schema_errors,fixed_template
from review_agent.validator_contracts import versions_for,validate_vote
from review_agent.validators import judge_validator,validator_payload
from tests import test_validator_v6_dimensions as fixtures


class FakeAPI:
    def __init__(self,response):self.response=response;self.calls=[];self.chat=SimpleNamespace(completions=self)
    def create(self,**request):
        self.calls.append(copy.deepcopy(request))
        if isinstance(self.response,Exception):raise self.response
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.response)),finish_reason='stop')],
            id='synthetic_response',model=request['model'],usage=None)


class HTTPFailure(Exception):
    def __init__(self,status):self.status_code=status;super().__init__('synthetic HTTP '+str(status))


class OutputStructureTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.ValidatorV6DimensionTests(methodName='runTest');f.setUp();self.f=f
        self.folder=Path(__file__).resolve().parents[1]/'runs/_test_output_structure'/uuid4().hex
        self.folder.mkdir(parents=True);self.addCleanup(shutil.rmtree,self.folder)
        self.registry=self.folder/'capabilities.json'
        self.client=OpenAICompatibleClient(api_key='fixture-only-key',base_url='https://provider.example/v1',
            model_id='test-model',model_family='test-family')
        self.api=FakeAPI(f.response());self.client._client=self.api
        self.voter={'id':'a','model_id':'test-model','model_family':'test-family','client':self.client}
        self.config={'output_structure':{'capabilities_path':str(self.registry)}}

    def write_registry(self,strict=True,**changes):
        entry={**endpoint_binding(self.voter),'schema_hash':sha256_json(v61.RESPONSE_SCHEMA),
            'strict_supported':strict,'verification':'strict_schema_observed' if strict else 'schema_ignored',**changes}
        self.registry.write_text(json.dumps({'version':'review_output_capabilities_v1','entries':[entry]}),encoding='utf-8')

    def proposal(self,strict=True):
        self.write_registry(strict)
        profile=resolve_profile(self.voter,self.config,v61.RESPONSE_SCHEMA)
        return {**self.f.proposal,**versions_for({'validator_contract_version':v61.CONTRACT_VERSION}),
            'validator_generation':[{'voter_id':'a','max_output_tokens':4096,'output_transport':profile}]}

    def test_strict_profile_uses_actual_schema_in_wire_parameter(self):
        p=self.proposal();vote=judge_validator(self.voter,p)
        self.assertEqual(vote['response_status'],'ok')
        wire=self.api.calls[0]['response_format']
        self.assertEqual(wire['type'],'json_schema');self.assertTrue(wire['json_schema']['strict'])
        self.assertEqual(wire['json_schema']['schema'],v61.RESPONSE_SCHEMA)
        payload=json.loads(self.api.calls[0]['messages'][1]['content'])
        self.assertNotIn('output_template',payload);self.assertEqual(payload['output_structure']['mode'],'json_schema')
        self.assertNotIn('fixture-only-key',json.dumps(payload))

    def test_unsupported_or_ignored_schema_uses_complete_fixed_template(self):
        p=self.proposal(strict=False);vote=judge_validator(self.voter,p)
        self.assertEqual(vote['response_status'],'ok')
        self.assertEqual(self.api.calls[0]['response_format'],{'type':'json_object'})
        payload=json.loads(self.api.calls[0]['messages'][1]['content'])
        self.assertEqual(set(payload['output_template']['dimensions']),set(v6.CHECK_NAMES))
        self.assertTrue(schema_errors(payload['output_template'],v61.RESPONSE_SCHEMA))
        self.assertTrue(all(d['status'].startswith('__CHOOSE__') for d in payload['output_template']['dimensions'].values()))

    def test_missing_registry_is_unverified_template_without_network_probe(self):
        profile=resolve_profile(self.voter,self.config,v61.RESPONSE_SCHEMA)
        self.assertEqual(profile['mode'],'json_template');self.assertIsNone(profile['capability_record_hash'])
        self.assertEqual(self.api.calls,[])

    def test_capability_does_not_follow_a_different_endpoint_model_stream_or_schema(self):
        for change in ({'base_url':'https://other.example/v1'},{'model':'other-model'},
                       {'stream_responses':True},{'thinking_mode':'disabled'},{'schema_hash':'other-schema'}):
            with self.subTest(change=change):
                self.write_registry(**change)
                self.assertEqual(resolve_profile(self.voter,self.config,v61.RESPONSE_SCHEMA)['mode'],'json_template')

    def test_frozen_profile_survives_registry_replacement(self):
        p=self.proposal();self.write_registry(strict=False)
        self.assertEqual(validator_payload(p,self.voter)['output_structure']['mode'],'json_schema')
        self.assertEqual(judge_validator(self.voter,p)['response_status'],'ok')

    def test_schema_and_template_mutation_fail_before_dispatch(self):
        for strict,field in ((True,'schema_hash'),(False,'template_hash')):
            p=self.proposal(strict);p['validator_generation'][0]['output_transport'][field]='changed'
            vote=judge_validator(self.voter,p,max_retries=1)
            self.assertEqual(vote['error_code'],'OUTPUT_DELIVERY_PREFLIGHT_FAILED')
            self.assertEqual(vote['actual_calls'],0);self.assertEqual(self.api.calls,[])

    def test_runtime_400_and_504_never_trigger_hidden_mode_fallback(self):
        for code in (400,504):
            with self.subTest(status=code):
                self.client._client=api=FakeAPI(HTTPFailure(code));p=self.proposal()
                vote=judge_validator(self.voter,p,max_retries=1)
                self.assertEqual(len(api.calls),1);self.assertFalse(vote['retryable'])
                self.assertEqual(api.calls[0]['response_format']['type'],'json_schema')
                if code==504:self.assertTrue(vote['outcome_unknown'])

    def test_transport_mode_changes_request_key_but_not_unknown_outcome_fence(self):
        strict=self.proposal();fallback=self.proposal(False)
        k1=validation_request_key(self.voter,strict);k2=validation_request_key(self.voter,fallback)
        s1=semantic_request_key('triple_review',self.voter,strict);s2=semantic_request_key('triple_review',self.voter,fallback)
        self.assertNotEqual(k1,k2);self.assertEqual(s1,s2)
        repo=ReviewRepository(self.folder/'review.sqlite3');repo.initialize()
        repo.claim_validation_request(k1,'owner',semantic_key=s1)
        repo.finish_validation_request(k1,'owner',{'response_status':'timeout','outcome_unknown':True,'retryable':False})
        blocked=repo.claim_validation_request(k2,'next-owner',semantic_key=s2)
        self.assertEqual(blocked['blocking_request_key'],k1)
        seed={**strict,'candidate_id':'c'}
        self.assertNotEqual(proposal_digest(seed),proposal_digest({**seed,'validator_generation':fallback['validator_generation']}))

    def test_provider_claimed_schema_mode_still_gets_local_validation(self):
        p=self.proposal();bad=self.f.response();bad['dimensions'].pop('mapping')
        self.client._client=FakeAPI(bad)
        vote=judge_validator(self.voter,p)
        self.assertEqual(vote['response_status'],'invalid_contract');self.assertIn('mapping',vote['error'])

    def test_business_evidence_checks_run_after_shape_validation(self):
        p=self.proposal();bad=self.f.response();bad['dimensions']['counterevidence']['evidence_ids']=[]
        self.assertEqual(schema_errors(bad,v61.RESPONSE_SCHEMA),[])
        self.client._client=FakeAPI(bad)
        self.assertEqual(judge_validator(self.voter,p)['response_status'],'invalid_contract')

    def test_template_placeholders_cannot_be_used_as_a_review_reason(self):
        p=self.proposal(False);bad=self.f.response();bad['dimensions']['entity_reference']['note']='__FILL_FROM_REVIEW__'
        self.assertEqual(schema_errors(bad,v61.RESPONSE_SCHEMA),[])
        self.client._client=FakeAPI(bad)
        vote=judge_validator(self.voter,p)
        self.assertEqual(vote['response_status'],'invalid_contract')
        self.assertIn('entity_reference.note',vote['error'])

    def test_client_legacy_default_and_explicit_invalid_mode(self):
        self.client.complete_json({});self.assertEqual(self.api.calls[0]['response_format'],{'type':'json_object'})
        for mode in (False,None,{'type':'text'},{'type':'json_schema','json_schema':{'strict':False}}):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):self.client.complete_json({},response_format=mode)
        self.assertEqual(len(self.api.calls),1)

    def test_local_schema_subset_checks_types_and_exact_field_paths(self):
        self.assertTrue(schema_errors({'x':True},{'type':'object','required':['x'],'properties':{'x':{'type':'integer'}}}))
        self.assertTrue(schema_errors({}, {'$ref':'unsupported'}))
        self.assertTrue(schema_errors(float('nan'),{}))
        self.assertTrue(schema_errors({'x':float('inf')},{'type':'object'}))
        self.assertTrue(schema_errors({'x':'a'},{'type':'object','additionalProperties':False,'properties':{}}))
        self.assertEqual(schema_errors(None,{'anyOf':[{'type':'null'},{'type':'string'}]}),[])

    def test_real_request_worker_preserves_frozen_schema_transport(self):
        captured=[];raw=self.f.response()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));captured.append(request)
                body=json.dumps({'id':'local_response','model':'test-model','choices':[{'index':0,'finish_reason':'stop',
                    'message':{'role':'assistant','content':json.dumps(raw)}}],'usage':{'prompt_tokens':10,'completion_tokens':10}}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        self.client.base_url='http://127.0.0.1:'+str(server.server_port)+'/v1';self.client._client=None
        p=self.proposal();repo=ReviewRepository(self.folder/'worker.sqlite3');repo.initialize()
        vote=judge_validator(self.voter,p,repository=repo,per_call_timeout_seconds=8)
        self.assertEqual(vote['response_status'],'ok')
        self.assertEqual(len(captured),1);self.assertEqual(captured[0]['response_format']['type'],'json_schema')
        attempt=repo.request_history(vote['request_key'])[0]
        self.assertEqual(attempt['metadata']['generation']['output_transport']['mode'],'json_schema')
        self.assertEqual(validate_vote(vote,v61.CONTRACT_VERSION),[])


if __name__=='__main__':unittest.main()
