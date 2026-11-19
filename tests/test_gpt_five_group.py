"""Offline checks of frozen-input branch reuse and study dispatch accounting."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from evaluation.gpt_five_group import Fence, prepare, run, labels_from_votes
from review_agent.llm_clients import MockLLMClient, LLMResponse
from review_agent.response_contract_v7 import CHECK_NAMES


class FiveGroupTests(unittest.TestCase):
    def test_exact_prior_planner_task_is_suppressed_before_budget_reservation(self):
        from review_agent.request_identity import semantic_request_key
        voter={'model_id':'Qwen','model_family':'qwen','model_identity':'qwen'}
        proposal={'version':'planner_action_v1','candidate_id':'c','candidate_revision':1,
            'candidate_hash':'candidate','tool_contract_hash':'tools','decision_hash':'original','prompt_hash':'prompt'}
        fence=Fence({'llm_calls':520,'known_tokens':15000000,'seconds':60})
        fence.prior_semantics.add(semantic_request_key('planner_action',voter,proposal,'prompt'))
        self.assertFalse(fence.allow_request(voter,proposal,'planner_action'))
        self.assertEqual(fence.reservations,0)
        self.assertTrue(fence.allow_request(voter,{**proposal,'decision_hash':'changed_recovery_obligations'},'planner_action'))

    def test_shared_ballots_survive_both_branches_without_duplicate_initial_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'study'
            prepare(folder)
            clients=[];branch_count=0;fences=[]

            def factory(config,fence):
                nonlocal branch_count
                fences.append(fence);branch_count+=1
                def respond(payload):
                    self.assertNotIn('labels',payload)
                    self.assertNotIn('gpt_reference',payload)
                    eid=payload['allowed_evidence_ids'][0]
                    dims={n:{'status':'pass','evidence_ids':[eid],
                        'note':'MOCK source review','issue_code':None} for n in CHECK_NAMES}
                    dims['triple_support']['status']='supported'
                    dims['knowledge_scope']['status']='general'
                    dims['mapping'].update(status='unassigned',relation_id=None,competing_relation_ids=[])
                    return LLMResponse({'dimensions':dims,'expression_findings':[],
                        'evidence_assessments':[{'evidence_id':eid,'role':'support','note':'MOCK source'}],
                        'primary_evidence_ids':[eid],'counterevidence_reviewed_ids':[],
                        'requested_context':None,'suggested_revision':None},input_tokens=10,output_tokens=5)
                voters=[]
                for name in ('judge_a','judge_b','judge_c'):
                    client=MockLLMClient(respond,model_id=name,model_family=name)
                    client.dispatch_guard=fence.allow;clients.append(client)
                    voters.append({'id':name,'model_id':name,'model_family':name,'model_identity':name,'client':client})
                def plan(payload):
                    # Stop future cases only after the branch planner was dispatched.
                    fence.stop='offline_fixture_one_case'
                    return LLMResponse({'action':'finish_review',
                        'arguments':{'stop_reason':'mock_finished','unresolved_issues':[],'evidence_ids':[]},
                        'target_issue':'mock','rationale_summary':'MOCK finish'},input_tokens=10,output_tokens=5)
                planner=MockLLMClient(plan,model_id='mock_planner',model_family='mock_planner')
                planner.dispatch_guard=fence.allow
                return voters,planner
            result=run(folder,services=factory)
            self.assertEqual(branch_count,3,json.dumps(result,ensure_ascii=False))
            common=folder/'case_01/common.sqlite3'
            with closing(sqlite3.connect(common)) as connection:
                original=connection.execute('SELECT attempt_id FROM request_attempts').fetchall()
            self.assertEqual(len(original),3)
            for group in ('b3_fixed_workflow','agent'):
                with closing(sqlite3.connect(folder/('case_01/'+group+'.sqlite3'))) as connection:
                    actual=connection.execute('SELECT attempt_id FROM request_attempts').fetchall()
                self.assertTrue(set(original)<=set(actual))
                self.assertEqual(result['groups'][group]['not_started_count'],39)
                rows=json.loads((folder/(group+'_predictions.json')).read_text(encoding='utf-8'))
                self.assertNotIn(rows[0]['status'],{'failed','not_started'},rows[0])
                self.assertEqual(rows[0]['axis_labels']['source_support'],'supported',rows[0])
            self.assertEqual(sum(len(c.calls) for c in clients),3)
            self.assertEqual(result['usage']['unique_provider_attempts'],4)
            self.assertEqual(result['usage']['known_tokens'],60)
            self.assertEqual(result['groups']['b1_single']['deployment_usage']['provider_attempts'],1)
            self.assertEqual(result['groups']['b0_all_keep']['population'],40)
            self.assertEqual(result['groups']['b0_all_keep']['deployment_usage']['provider_attempts'],0)

    def test_unknown_and_token_stop_prevent_next_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'ledger.sqlite3'
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute('CREATE TABLE request_attempts(attempt_id,state,result_json,metadata_json)')
                connection.execute('INSERT INTO request_attempts VALUES(?,?,?,?)',('a','unknown','{}','{}'))
            fence=Fence({'llm_calls':5,'known_tokens':100,'seconds':60});fence.register(path)
            fence.skip_unknown_candidates=False # Explicit historical stop-study policy.
            self.assertFalse(fence.allow());self.assertEqual(fence.snapshot()['stop_reason'],'new_request_outcome_unknown')
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute('UPDATE request_attempts SET state=?,result_json=?',('completed',json.dumps({'input_tokens':100,'output_tokens':2})))
            fresh=Fence({'llm_calls':5,'known_tokens':100,'seconds':60});fresh.register(path)
            self.assertFalse(fresh.allow());self.assertEqual(fresh.snapshot()['stop_reason'],'known_token_stop')


if __name__=='__main__':unittest.main()
