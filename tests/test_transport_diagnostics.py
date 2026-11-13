"""Offline transport metadata, secrecy, lifecycle and ontology propagation tests."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import shutil
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from openai import OpenAI
from review_agent.llm_clients import LLMResponse, MockLLMClient, OpenAICompatibleClient, response_metadata
from review_agent.transport_diagnostics import TransportDiagnostics, active_transport, request_hook, response_hook
from review_agent.execution import api_error_fields
from review_agent.repository import ReviewRepository
from ontology_review import validators, semantic_competency as cq
from ontology_review import clarification
from review_agent import validators as fact_validators
from ontology_review.contracts import CHECKS


class Pieces(httpx.SyncByteStream):
    def __init__(self, values):
        self.values = values
        self.closed = False

    def __iter__(self):
        for value in self.values:
            if isinstance(value, Exception):
                raise value
            yield value

    def close(self):
        self.closed = True


def sdk_client(handler, *, stream=False):
    wrapper = OpenAICompatibleClient(api_key='fixture-secret', base_url='https://example.test/v1',
        model_id='fixture', model_family='fixture', stream_responses=stream)
    wrapper._client = OpenAI(api_key=wrapper.api_key, base_url=wrapper.base_url, max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler),
            event_hooks={'request': [request_hook], 'response': [response_hook]}))
    return wrapper


def event(content=None, *, finish=None, reasoning=None):
    value = {'id': 'body-response', 'model': 'fixture', 'object': 'chat.completion.chunk', 'created': 1,
             'choices': [{'index': 0, 'delta': {'content': content, 'reasoning_content': reasoning}, 'finish_reason': finish}]}
    return ('data: ' + json.dumps(value) + '\n\n').encode()


class TransportDiagnosticsTests(unittest.TestCase):
    def test_real_sdk_stream_keeps_timing_usage_and_safe_ids_without_secrets(self):
        stream = Pieces([event('{"ok":', reasoning='private-reasoning-fixture'),
                         event('true}', finish='stop'), b'data: [DONE]\n\n'])
        client = sdk_client(lambda request: httpx.Response(200, headers={
            'content-type': 'text/event-stream', 'x-oneapi-request-id': 'gateway-response',
            'x-request-id': 'fixture-secret', 'set-cookie': 'private-cookie', 'authorization': 'private-header'},
            stream=stream), stream=True)
        self.addCleanup(client._client.close)
        response = client.complete_json({'fixture': True})
        self.assertEqual(response.content, '{"ok":true}')
        self.assertEqual(response.gateway_request_id, 'gateway-response')
        self.assertEqual(response.response_id, 'body-response')
        metrics = response.transport_metrics
        self.assertGreaterEqual(metrics['first_body_byte_ms'], metrics['response_headers_ms'])
        self.assertGreaterEqual(metrics['body_chunk_count'], 2)
        self.assertIsNotNone(metrics['first_chunk_ms'])
        self.assertIsNotNone(metrics['first_content_ms'])
        self.assertGreater(metrics['reasoning_chars'], 0)
        text = json.dumps(response_metadata(response))
        for secret in ('fixture-secret', 'private-reasoning-fixture', 'private-cookie', 'private-header'):
            self.assertNotIn(secret, text)
        self.assertTrue(stream.closed)

    def test_nonstream_headers_and_first_byte_are_kept(self):
        payload = {'id': 'body-response', 'model': 'fixture', 'object': 'chat.completion', 'created': 1,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '{}'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10}}
        client = sdk_client(lambda request: httpx.Response(200, headers={
            'content-type': 'application/json', 'x-oneapi-request-id': 'gw-nonstream'},
            stream=Pieces([json.dumps(payload).encode()])))
        self.addCleanup(client._client.close)
        response = client.complete_json({})
        self.assertEqual(response.input_tokens, 7)
        self.assertEqual(response.gateway_request_id, 'gw-nonstream')
        self.assertIsNotNone(response.transport_metrics['first_body_byte_ms'])
        self.assertFalse(response.transport_metrics['stream'])

    def test_http504_keeps_gateway_identity_and_unknown_state(self):
        client = sdk_client(lambda request: httpx.Response(504, headers={
            'content-type': 'text/html', 'x-oneapi-request-id': 'gw-504'},
            stream=Pieces([b'gateway timeout'])))
        self.addCleanup(client._client.close)
        with self.assertRaises(Exception) as raised:
            client.complete_json({})
        fields = api_error_fields(raised.exception)
        self.assertEqual(fields['http_status'], 504)
        self.assertEqual(fields['gateway_request_id'], 'gw-504')
        self.assertTrue(fields['outcome_unknown'])
        self.assertFalse(fields['retryable'])
        self.assertIsNotNone(fields['transport_metrics']['response_headers_ms'])
        self.assertIsNotNone(fields['transport_metrics']['first_body_byte_ms'])

    def test_interrupted_stream_retains_diagnostics_but_never_partial_text(self):
        client = sdk_client(lambda request: httpx.Response(200, headers={
            'content-type': 'text/event-stream', 'x-oneapi-request-id': 'gw-partial'},
            stream=Pieces([event('{"untrusted":'), httpx.ReadError('fixture disconnected')])), stream=True)
        self.addCleanup(client._client.close)
        with self.assertRaises(Exception) as raised:
            client.complete_json({})
        fields = api_error_fields(raised.exception)
        self.assertTrue(fields['outcome_unknown'])
        self.assertEqual(fields['gateway_request_id'], 'gw-partial')
        self.assertIsNotNone(fields['transport_metrics']['first_content_ms'])
        self.assertNotIn('untrusted', json.dumps(fields))
        self.assertNotIn('content', fields)

    def test_contexts_are_isolated_for_simultaneous_requests(self):
        barrier = Barrier(2)
        def handle(request):
            barrier.wait(timeout=5)
            return httpx.Response(200, headers={'x-oneapi-request-id': request.url.path}, stream=Pieces([b'{}']))
        client = httpx.Client(transport=httpx.MockTransport(handle),
            event_hooks={'request': [request_hook], 'response': [response_hook]})
        self.addCleanup(client.close)
        def execute(identity):
            diagnostics = TransportDiagnostics()
            with active_transport(diagnostics):
                client.get('https://example.test/' + identity)
            return diagnostics.gateway_request_id
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(execute, ['first', 'second'])), ['/first', '/second'])

    def test_ontology_wrapper_keeps_metadata_for_valid_and_bad_json(self):
        good = {'decision': 'approve', 'necessity': 'confirmed_gap', 'semantic_status': 'pass',
            'structure_status': 'pass', 'impact_status': 'pass', 'supporting_evidence_ids': ['ev'],
            'counter_evidence_ids': [], 'checks': [{'check': name, 'status': 'pass',
                'evidence_ids': ['ev'], 'note': 'fixture'} for name in CHECKS],
            'reason_codes': [], 'suggested_changes': None, 'requested_context': None}
        proposal = {'evidence': [{'evidence_id': 'ev'}], 'operations': [], 'check_records': {}}
        for body in (good, '{bad-json'):
            response = LLMResponse(body, input_tokens=9, output_tokens=4, gateway_request_id='gw-model',
                                   transport_metrics={'first_body_byte_ms': 10})
            voter = {'id': 'a', 'client': MockLLMClient([response])}
            with patch.object(validators, 'vote_binding', return_value={'voter_id': 'a'}):
                vote = validators.judge_once(voter, proposal)
            self.assertEqual(vote['gateway_request_id'], 'gw-model')
            self.assertEqual(vote['input_tokens'], 9)
            self.assertEqual(vote['transport_metrics']['first_body_byte_ms'], 10)

    def test_competency_fail_and_invalid_json_keep_metadata_and_judgment(self):
        request = {'task_type': 'ontology_competency_review', 'task_id': 'task', 'object_revision': 1,
            'parent_proposal_digest': 'digest', 'base_release_id': 'base', 'delta_hash': 'delta',
            'response_contract_version': cq.VERSION, 'request_hash': 'hash', 'questions': [{'id': 'q'}],
            'source_documents': [{'document_id': 'doc', 'text': 'This is the complete fixture source.'}]}
        fail = {'coverage': 'partial', 'unresolved_terms': ['term'],
                'assessments': [{'question_id': 'q', 'status': 'fail', 'quotes': [], 'note': 'fixture objection'}]}
        for body in (fail, '{bad-json'):
            voter = {'id': 'a', 'model_id': 'fixture', 'model_family': 'fixture',
                'client': MockLLMClient([LLMResponse(body, gateway_request_id='gw-cq',
                    transport_metrics={'first_body_byte_ms': 20})])}
            vote = cq.judge_once(voter, request)
            self.assertEqual(vote['gateway_request_id'], 'gw-cq')
            self.assertEqual(vote['transport_metrics']['first_body_byte_ms'], 20)
            if body is fail:
                self.assertEqual(vote['assessments'][0]['status'], 'fail')
            else:
                self.assertEqual(vote['response_status'], 'invalid_json')

    def test_fact_timeout_keeps_latest_transport_metrics_without_partial_response(self):
        error = TimeoutError('fixture timeout')
        error.gateway_request_id = 'gw-timeout'
        error.transport_metrics = {'response_headers_ms': 10, 'first_body_byte_ms': 20}
        voter = {'client': MockLLMClient([error])}
        with patch.object(fact_validators, 'vote_binding', return_value={'voter_id': 'a'}), \
             patch.object(fact_validators, 'validator_payload', return_value={}), \
             patch('review_agent.validator_contracts.prompt_for', return_value='fixture'):
            vote = fact_validators._judge_validator_once(voter, {})
        self.assertEqual(vote['response_status'], 'timeout')
        self.assertTrue(vote['outcome_unknown'])
        self.assertFalse(vote['retryable'])
        self.assertEqual(vote['gateway_request_id'], 'gw-timeout')
        self.assertEqual(vote['transport_metrics']['first_body_byte_ms'], 20)

    def test_original_reviewer_invalid_json_keeps_transport_metadata(self):
        response = LLMResponse('{bad-json', gateway_request_id='gw-clarification',
                               transport_metrics={'first_body_byte_ms': 10})
        voter = {'client': MockLLMClient([response])}
        with patch.object(clarification, 'binding', return_value={'voter_id': 'a'}):
            vote = clarification.judge_once(voter, {})
        self.assertEqual(vote['response_status'], 'invalid_json')
        self.assertEqual(vote['gateway_request_id'], 'gw-clarification')
        self.assertEqual(vote['transport_metrics']['first_body_byte_ms'], 10)

    def test_sqlite_keeps_gateway_id_separate_from_body_id_and_unknown(self):
        workspace_runs = (Path(__file__).resolve().parents[1] / 'runs').resolve()
        directory = workspace_runs / ('transport-test-' + uuid4().hex)
        directory.mkdir()
        self.assertTrue(directory.resolve().is_relative_to(workspace_runs))
        try:
            repository = ReviewRepository(directory / 'review.sqlite3')
            repository.initialize()
            repository.claim_validation_request('fixture-key', 'owner')
            identity = repository.begin_request_attempt('fixture-key', 'owner', 1,
                {'binding': {'task_id': 'fixture'}, 'task_type': 'fixture', 'proposal': {}})
            repository.finish_request_attempt(identity, 'owner', {'task_id': 'fixture', 'response_status': 'api_error',
                'http_status': 504, 'outcome_unknown': True, 'response_id': 'body-id', 'gateway_request_id': 'gateway-id',
                'transport_metrics': {'response_headers_ms': 60}})
            attempt = repository.request_attempt(identity)
            self.assertEqual(attempt['state'], 'unknown')
            self.assertEqual(attempt['provider_request_id'], 'gateway-id')
            self.assertEqual(attempt['result']['response_id'], 'body-id')
        finally:
            self.assertTrue(directory.resolve().is_relative_to(workspace_runs))
            shutil.rmtree(directory)


if __name__ == '__main__':
    unittest.main()
