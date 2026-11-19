import copy
from dataclasses import asdict
import hashlib
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import unittest
from uuid import uuid4

import httpx
from extract_triplets.candidate_contract import RESPONSE_SCHEMA
from extract_triplets.streaming_extraction import VERSION,POLICY,binding,judge_once
from review_agent.contracts import sha256_json
from review_agent.llm_clients import LLMResponse,OpenAICompatibleClient
from review_agent.repository import ReviewRepository
from tests.test_transport_diagnostics import Pieces,event,sdk_client

ROOT=Path(__file__).resolve().parents[1]


def proposal():
    p={'task_id':'local-extraction-fixture','system_prompt':'Extract generic test concepts as JSON.',
       'payload':{'core_text':'A relates B.'},'schema_hash':sha256_json(RESPONSE_SCHEMA),
       'delivery_policy':POLICY,'max_output_tokens':512}
    return {**p,'proposal_digest':sha256_json(p)}


def voter(client):
    return {'id':'extractor','model_id':'fixture','model_family':'fixture','model_identity':'fixture',
            'client':client,'http_timeout_seconds':30,'request_timeout_seconds':30,'max_output_tokens':512}


class StreamingExtractionTests(unittest.TestCase):
    def setUp(self):
        self.folder=ROOT/'outputs'/('stream_extraction_test_'+uuid4().hex);self.folder.mkdir()
        self.addCleanup(shutil.rmtree,self.folder)

    def test_known_output_limit_is_not_hidden_by_json_parse_failure(self):
        stream=Pieces([event('{"triplets":[{"head":"A"}',finish='length'),b'data: [DONE]\n\n'])
        client=sdk_client(lambda request:httpx.Response(200,stream=stream),stream=True)
        self.addCleanup(client._client.close)
        result=judge_once(voter(client),proposal())
        self.assertEqual(result['response_status'],'output_incomplete')
        self.assertEqual(result['finish_reason'],'length')
        self.assertFalse(result.get('outcome_unknown',False))
        self.assertIn('triplets',result['raw_response'])

    def test_interrupted_stream_checkpoints_public_content_and_ids_without_reasoning(self):
        seen=[]
        stream=Pieces([event('{"triplets":',reasoning='PRIVATE_REASONING'),httpx.ReadError('fixture disconnected')])
        client=sdk_client(lambda request:httpx.Response(200,headers={'x-request-id':'provider-header'},stream=stream),stream=True)
        self.addCleanup(client._client.close);v=voter(client);v['on_stream_progress']=seen.append
        result=judge_once(v,proposal())
        self.assertTrue(result['outcome_unknown']);self.assertEqual(result['response_id'],'body-response')
        self.assertEqual(seen[-1].content,'{"triplets":')
        self.assertNotIn('PRIVATE_REASONING',json.dumps([asdict(x) for x in seen]))
        self.assertNotIn('PRIVATE_REASONING',json.dumps(result))

    def test_progress_callback_is_checked_before_any_http_dispatch(self):
        calls=[]
        client=sdk_client(lambda r:calls.append(r),stream=True);self.addCleanup(client._client.close)
        with self.assertRaisesRegex(ValueError,'callable'):client.complete_json({},on_progress='invalid')
        self.assertEqual(calls,[])

    def test_checkpoint_survives_wait_timeout_without_becoming_a_completed_result(self):
        from review_agent.request_reconciliation import report
        from evaluation.extraction_chain_v2 import intake_items
        repo=ReviewRepository(self.folder/'review.sqlite3');repo.initialize()
        p=proposal();b={'model_id':'fixture'};owner='owner'
        repo.claim_validation_request('key',owner)
        attempt=repo.begin_request_attempt('key',owner,1,{'binding':b,'task_type':VERSION,'proposal':p})
        content='{"triplets":[{"head":"A"},{"head":"unfinished'
        repo.record_extraction_progress(attempt,owner,LLMResponse(content,response_id='early-provider-id',
            transport_metrics={'client_request_id':attempt}))
        repo.finish_request_attempt(attempt,owner,{**b,'response_status':'timeout','outcome_unknown':True})
        row=repo.request_attempt(attempt)
        self.assertEqual(row['state'],'unknown');self.assertEqual(row['provider_request_id'],'early-provider-id')
        reconciliation=report(repo)['requests'][0]
        self.assertEqual(reconciliation['client_request_id'],attempt)
        self.assertEqual(reconciliation['last_progress_visible_characters'],len(content))
        items,kind=intake_items({'attempt_id':attempt,'response_status':'timeout'},repo)
        self.assertEqual(items,[{'head':'A'}]);self.assertEqual(kind,'complete_visible_prefix_only')
        self.assertEqual(repo.request_attempt(attempt)['state'],'unknown')

    def test_gpt_reference_binds_all_40_original_packets_and_keeps_its_provenance(self):
        from evaluation.gpt_reference import build_reference,compare,SOURCE
        before=hashlib.sha256(SOURCE.read_bytes()).hexdigest();r=build_reference()
        self.assertEqual(len(r['annotations']),40);self.assertEqual(r['human_gold_count'],0)
        self.assertEqual(r['label_distributions']['source_support']['supported'],35)
        a=r['annotations'][0];b=a['binding']
        prediction={'candidate_id':b['candidate_id'],'revision':b['revision'],
            'candidate_content_hash':b['candidate_content_hash'],'binding':b,'axis_labels':a['labels']}
        result=compare([prediction],r)
        self.assertEqual(result['label_source'],'gpt_preannotation')
        self.assertEqual(result['metrics']['mapping_status']['value'],1)
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(),before)
        for patch in ({'candidate_id':'new-test-candidate'},{'candidate_content_hash':'changed'},{'binding':None}):
            with self.assertRaises(ValueError):compare([{**prediction,**patch}],r)

    def test_plain_extractor_client_really_enables_streaming_before_creation(self):
        from runs.regress_v8 import configuration
        from evaluation.extraction_chain_v2 import extraction_voter
        v=extraction_voter(configuration(profile='weighted_diagnostic',directory=self.folder))
        self.assertIsInstance(v['client'],OpenAICompatibleClient)
        self.assertTrue(v['client'].stream_responses)
        self.assertEqual(v['client'].timeout_seconds,180)
        self.assertEqual(v['max_output_tokens'],12288)

    def test_gpt_reliability_requires_actual_model_comparisons_and_retains_ai_origin(self):
        from evaluation.gpt_reference import build_reference,reliability
        from review_agent.vote_weighting import normalize_policy
        r=build_reference()
        self.assertEqual(reliability([],r)['policy_reliability']['models'],{})
        a=next(a for a in r['annotations'] if a['labels']['source_support']=='supported')
        b=a['binding'];row={'candidate_id':b['candidate_id'],'revision':b['revision'],
            'candidate_content_hash':b['candidate_content_hash'],'binding':b,'axis_labels':a['labels'],
            'voter_id':'judge_a','model_identity':'fixture-model','model_family':'fixture-family'}
        stats=reliability([row],r)
        self.assertEqual(stats['label_source'],'gpt_preannotation');self.assertEqual(stats['human_gold_count'],0)
        self.assertEqual(stats['policy_reliability']['models']['judge_a']['axes']['source'],{'correct':1,'evaluated':1})
        normalize_policy({'mode':'static','reliability':stats['policy_reliability']},3)
        with self.assertRaisesRegex(ValueError,'duplicate'):reliability([row,row],r)

    def test_provider_worker_keeps_stream_and_late_receipt_after_caller_exit(self):
        from review_agent.request_execution import judge_typed
        repo=ReviewRepository(self.folder/'review.sqlite3');repo.initialize()
        received=threading.Event();release=threading.Event();calls=[]
        final=json.dumps({'triplets':[],'coverage_status':'complete_for_supplied_scope'})
        def chunk(text,finish=None,usage=None):
            value={'id':'durable-extraction-id','object':'chat.completion.chunk','created':1,'model':'fixture',
                'choices':[{'index':0,'delta':{'content':text},'finish_reason':finish}]}
            if usage:value['usage']=usage
            return ('data: '+json.dumps(value)+'\n\n').encode()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append({'body':body,'client_id':self.headers.get('X-Client-Request-Id')})
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                self.wfile.write(chunk(final[:15]));self.wfile.flush();received.set();release.wait(25)
                self.wfile.write(chunk(final[15:],'stop',{'prompt_tokens':8,'completion_tokens':4,'total_tokens':12}))
                self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)+'/v1'
        path=self.folder/'proposal.json';path.write_text(json.dumps(proposal()),encoding='utf-8')
        script='''import json,sys
from review_agent.llm_clients import OpenAICompatibleClient
from review_agent.repository import ReviewRepository
from review_agent.request_execution import judge_typed
from extract_triplets.streaming_extraction import VERSION,binding,judge_once
p=json.load(open(sys.argv[2],encoding='utf-8'))
c=OpenAICompatibleClient(api_key='local-fixture',base_url=sys.argv[3],model_id='fixture',model_family='fixture',timeout_seconds=30,stream_responses=True)
v={'id':'extractor','model_id':'fixture','model_family':'fixture','model_identity':'fixture','client':c,'http_timeout_seconds':30,'request_timeout_seconds':30,'max_output_tokens':512}
r=judge_typed(v,p,ReviewRepository(sys.argv[1]),task_type=VERSION,binding=binding(v,p),invoke=lambda f:judge_once(v,p,f),validate_cached=lambda r:[],prompt_hash='fixture-prompt',retries=0,timeout=.15)
print(r['response_status'])
'''
        process=subprocess.Popen([sys.executable,'-B','-c',script,str(repo.database_path),str(path),base],
            cwd=str(ROOT),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            stdout,stderr=process.communicate(timeout=20)
            self.assertEqual(process.returncode,0,stderr.decode());self.assertIn(b'timeout',stdout)
            self.assertTrue(received.wait(25),'durable extraction worker did not reach the local stub')
            deadline=time.monotonic()+5;progress=[]
            while time.monotonic()<deadline:
                with repo.connect() as c:
                    progress=c.execute('SELECT detail_json FROM request_attempt_events WHERE event=?',('visible_extraction_checkpoint',)).fetchall()
                if progress:break
                time.sleep(.05)
            self.assertTrue(progress,'visible stream was not persisted after the caller exited')
            with repo.connect() as c:attempt_id=c.execute('SELECT attempt_id FROM request_attempts').fetchone()[0]
            self.assertEqual(repo.request_attempt(attempt_id)['provider_request_id'],'durable-extraction-id')
            release.set();deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                row=repo.request_attempt(attempt_id)
                if row['state']=='completed':break
                time.sleep(.05)
            self.assertEqual(row['state'],'completed',row);self.assertTrue(row['result']['late_response'])
            self.assertEqual(row['result']['input_tokens'],8)
            self.assertEqual(calls[0]['client_id'],attempt_id)
            self.assertTrue(calls[0]['body']['stream'])
            client=OpenAICompatibleClient(api_key='local-fixture',base_url=base,model_id='fixture',model_family='fixture',timeout_seconds=30,stream_responses=True)
            v=voter(client);p=proposal()
            cached=judge_typed(v,p,repo,task_type=VERSION,binding=binding(v,p),invoke=lambda f:judge_once(v,p,f),
                validate_cached=lambda r:[],prompt_hash='fixture-prompt',retries=0,timeout=1)
            self.assertTrue(cached['cached']);self.assertEqual(len(calls),1)
        finally:
            release.set()
            if process.poll() is None:process.terminate();process.wait(timeout=3)
            server.shutdown();server.server_close();thread.join(timeout=2)
