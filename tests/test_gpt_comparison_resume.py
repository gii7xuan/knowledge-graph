"""Unknown cases remain quarantined while untouched candidates can proceed."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from evaluation.gpt_five_group import ROOT,Fence,prepare,run,read
from evaluation.gpt_comparison_resume import restore
from review_agent.llm_clients import MockLLMClient,LLMResponse
from review_agent.model_settings import validator_settings,configured_planner
from review_agent.response_contract_v7 import CHECK_NAMES

PRIOR=ROOT/'outputs/gpt40_five_group_20261119_recovered'


class SkipUnknownFenceTests(unittest.TestCase):
    def test_unknown_case_blocks_only_its_own_calls_and_preserves_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'ledger.sqlite3'
            with closing(sqlite3.connect(path)) as c,c:
                c.execute('CREATE TABLE request_attempts(attempt_id,state,result_json,metadata_json)')
                c.execute('INSERT INTO request_attempts VALUES(?,?,?,?)',('a','unknown','{}',json.dumps({'proposal':{'candidate_id':'blocked'}})))
            fence=Fence({'llm_calls':171,'known_tokens':15000000,'seconds':60})
            fence.skip_unknown_candidates=True;fence.register(path);fence.reservations=170
            self.assertFalse(fence.allow_request({}, {'candidate_id':'blocked'},'triple_review'))
            self.assertEqual(fence.reservations,170)
            self.assertTrue(fence.allow_request({}, {'candidate':{'candidate_id':'fresh'}},'triple_review'))
            self.assertEqual(fence.reservations,171)
            self.assertFalse(fence.allow_request({}, {'candidate_id':'another'},'triple_review'))
            self.assertEqual(fence.snapshot()['stop_reason'],'study_call_cap')
            self.assertEqual(fence.snapshot()['unknown_requests'],1)


@unittest.skipUnless((PRIOR/'final_verification.json').exists(),'requires recorded study prefix')
class ResumeRecordedTests(unittest.TestCase):
    def test_restore_keeps_26_predictions_and_all_170_prior_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'resume';prepare(output,resume_from=PRIOR)
            protocol=read(output/'protocol.json');fence=Fence(protocol['comparison_limits'])
            rows,comparisons,replay,done=restore(output,protocol['resume'],read(output/'model_inputs.json'),fence)
            self.assertEqual(len(done),26)
            self.assertEqual(len(protocol['resume']['remaining_sample_ids']),13)
            self.assertEqual(fence.snapshot()['unique_provider_attempts'],170)
            self.assertEqual(fence.snapshot()['known_tokens'],3321478)
            self.assertEqual(fence.snapshot()['unknown_requests'],2)
            self.assertIsNone(fence.snapshot()['stop_reason'])
            for group,data in rows.items():
                self.assertEqual(data[:26],read(PRIOR/(group+'_predictions.json'))[:26])
                self.assertNotEqual(data[26]['status'],'skipped_unknown')
            self.assertEqual(rows['agent'][26]['status'],'human_handoff')
            self.assertTrue(rows['agent'][26]['technical_failure'])
            self.assertEqual(rows['b0_all_keep'][26]['predicted_decision'],'accept')
            self.assertEqual(rows['b1_single'][26]['axis_labels']['source_support'],'supported')
            self.assertTrue(any(r['candidate_id']==rows['agent'][26]['candidate_id'] for r in comparisons))
            self.assertEqual(len(replay),27)

    def test_resume_starts_at_28_without_repeating_a_prior_call(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'resume';prepare(output,resume_from=PRIOR)
            inputs=read(output/'model_inputs.json');received=[]
            def factory(config,fence):
                def respond(payload):
                    received.append(payload['candidate']['candidate_id']);eid=payload['allowed_evidence_ids'][0]
                    dims={n:{'status':'pass','evidence_ids':[eid],'note':'MOCK source','issue_code':None} for n in CHECK_NAMES}
                    dims['triple_support']['status']='supported';dims['knowledge_scope']['status']='general'
                    dims['mapping'].update(status='unassigned',relation_id=None,competing_relation_ids=[])
                    return LLMResponse({'dimensions':dims,'expression_findings':[],
                        'evidence_assessments':[{'evidence_id':eid,'role':'support','note':'MOCK source'}],
                        'primary_evidence_ids':[eid],'counterevidence_reviewed_ids':[],
                        'requested_context':None,'suggested_revision':None},input_tokens=10,output_tokens=5)
                voters=[]
                for slot in config['validators']:
                    v=validator_settings(slot);client=MockLLMClient(respond,model_id=v['model'],model_family=v['family'])
                    client.request_dispatch_guard=fence.allow_request
                    voters.append({'id':slot['id'],'model_id':v['model'],'model_family':v['family'],'model_identity':v['model_identity'],'client':client})
                def plan(payload):
                    fence.stop='offline_fixture_one_remaining_candidate'
                    return LLMResponse({'action':'finish_review','arguments':{'stop_reason':'mock_done','unresolved_issues':[],'evidence_ids':[]},
                        'target_issue':'mock','rationale_summary':'MOCK finish'},input_tokens=10,output_tokens=5)
                pl=configured_planner(config);planner=MockLLMClient(plan,model_id=pl['model'],model_family=pl['family'])
                planner.request_dispatch_guard=fence.allow_request
                return voters,planner
            result=run(output,services=factory)
            self.assertEqual(received,[inputs[27]['candidate']['candidate_id']]*3)
            self.assertFalse((output/'case_27').exists())
            self.assertTrue((output/'case_28/common.sqlite3').exists())
            self.assertEqual(result['usage']['unique_provider_attempts'],174)
            self.assertEqual(result['usage']['dispatch_reservations'],174)
            self.assertEqual(result['usage']['known_tokens'],3321538)
            self.assertEqual(result['usage']['unknown_requests'],2)
            self.assertEqual(result['groups']['agent']['skipped_unknown_count'],0)
            self.assertEqual(result['groups']['agent']['manual_handoff_count'],25)
            self.assertEqual(read(output/'agent_predictions.json')[:26],read(PRIOR/'agent_predictions.json')[:26])


if __name__=='__main__':unittest.main()
