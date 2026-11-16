"""A local OpenAI-compatible stub proves receipt persistence after caller termination."""
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
from review_agent.agent_state import AgentState
from review_agent.planner_requests import proposal_for,request
from review_agent.llm_clients import OpenAICompatibleClient
from review_agent.repository import ReviewRepository


class PlannerProcessLifetimeTests(unittest.TestCase):
    def test_request_worker_finishes_after_its_candidate_process_has_exited(self):
        root=Path(__file__).resolve().parents[1]
        folder=root/'outputs'/('planner_process_test_'+uuid4().hex);folder.mkdir()
        self.addCleanup(shutil.rmtree,folder)
        received=threading.Event();release=threading.Event();calls=[]
        action={'action':'inspect_ontology','arguments':{'relation_id':None,'issue':'local test'},
                'target_issue':'schema','rationale_summary':'inspect frozen schema'}
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                received.set();release.wait(30)
                body=json.dumps({'id':'local-planner-receipt','object':'chat.completion','created':0,'model':'planner',
                    'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(action)},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':8,'completion_tokens':4,'total_tokens':12}}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)+'/v1'
        repository=ReviewRepository(folder/'review.sqlite3');repository.initialize()
        state=AgentState({'candidate_id':'synthetic-local-sensor','revision':1},{},'local-audit')
        proposal=proposal_for(state,{'candidate':{'head':'sensor'}},{'inspect_ontology':{}})
        path=folder/'proposal.json';path.write_text(json.dumps(proposal),encoding='utf-8')
        script="""import json,sys
from review_agent.planner_requests import request
from review_agent.llm_clients import OpenAICompatibleClient
from review_agent.repository import ReviewRepository
client=OpenAICompatibleClient(api_key='local-fixture',base_url=sys.argv[3],model_id='planner',model_family='planner',timeout_seconds=45)
voter={'id':'planner','model_id':'planner','model_family':'planner','client':client,'request_channel':'planner'}
request(ReviewRepository(sys.argv[1]),voter,json.load(open(sys.argv[2],encoding='utf-8')),45)
"""
        process=subprocess.Popen([sys.executable,'-B','-c',script,str(repository.database_path),str(path),base],
            cwd=str(root),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            # Windows cold imports happen before the stub receives HTTP. This is
            # a startup allowance, independent of the production request timeout.
            self.assertTrue(received.wait(30),'local request process did not reach the stub within the startup allowance')
            process.terminate();process.wait(timeout=3)
            release.set()
            deadline=time.monotonic()+15;attempt=None
            while time.monotonic()<deadline:
                with repository.connect() as connection:
                    row=connection.execute('SELECT attempt_id FROM request_attempts LIMIT 1').fetchone()
                attempt=repository.request_attempt(row[0]) if row else None
                if attempt and attempt['state']=='completed':break
                time.sleep(.05)
            self.assertIsNotNone(attempt);self.assertEqual(attempt['state'],'completed',attempt)
            self.assertEqual(attempt['result']['input_tokens'],8)
            client=OpenAICompatibleClient(api_key='local-fixture',base_url=base,model_id='planner',model_family='planner',timeout_seconds=20)
            cached=request(repository,{'id':'planner','model_id':'planner','model_family':'planner','client':client,
                'request_channel':'planner'},proposal,1)
            self.assertTrue(cached['cached']);self.assertEqual(len(calls),1)
        finally:
            release.set()
            if process.poll() is None:process.terminate();process.wait(timeout=3)
            server.shutdown();server.server_close();thread.join(timeout=2)
