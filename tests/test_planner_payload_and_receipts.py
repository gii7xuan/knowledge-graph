from copy import deepcopy
import json
import sqlite3
import unittest
from review_agent.contracts import sha256_json
from review_agent.planner_view import compact_planner_payload
from review_agent.planner_receipts import reconcile_attempts
from evaluation.regression_gate import request_accounting
from tests import test_review_planner as fixtures
from review_agent.llm_clients import LLMResponse


def receipt_fixture(state='completed'):
    p={'candidate_id':'c','candidate_revision':1,'audit_id':'a','input_hash':'i','payload':{'x':1}}
    p['proposal_digest']=sha256_json(p)
    row={'attempt_id':'r','state':state,'result':{'response_status':'ok','input_tokens':9,'output_tokens':8},
        'metadata':{'task_type':'planner_action','proposal':deepcopy(p),'binding':{
            'proposal_digest':p['proposal_digest'],'input_hash':'i','task_id':'c','object_revision':1}}}
    step={'request_proposal':p,'attempts':[{'response_status':'started','input_hash':'i'}]}
    return step,row


class PlannerReceiptTests(unittest.TestCase):
    def test_exact_terminal_receipt_settles_stub_without_mutation(self):
        step,row=receipt_fixture();original=deepcopy(step)
        result=reconcile_attempts(step,[row])
        self.assertEqual(step,original)
        self.assertEqual(result[0]['attempt_id'],'r')
        self.assertEqual(result[0]['response_status'],'ok')
        self.assertFalse(result[0]['outcome_unknown'])

    def test_mismatched_or_unverifiable_proposal_cannot_settle_stub(self):
        step,row=receipt_fixture();row['metadata']['proposal']['audit_id']='other'
        self.assertEqual(reconcile_attempts(step,[row])[0]['response_status'],'started')
        step,row=receipt_fixture();step['request_proposal']['payload']['x']=2
        self.assertEqual(reconcile_attempts(step,[row])[0]['response_status'],'started')

    def test_actual_unknown_stays_unknown(self):
        step,row=receipt_fixture('unknown')
        result=reconcile_attempts(step,[row])
        self.assertTrue(result[0]['outcome_unknown'])

    def test_accounting_uses_bound_receipt_instead_of_unidentified_started_stub(self):
        step,row=receipt_fixture()
        with sqlite3.connect(':memory:') as c:
            c.executescript('CREATE TABLE request_attempts(attempt_id TEXT,state TEXT,result_json TEXT,metadata_json TEXT);CREATE TABLE validation_requests(status TEXT);CREATE TABLE planner_steps(record_json TEXT);')
            c.execute('INSERT INTO request_attempts VALUES(?,?,?,?)',(row['attempt_id'],row['state'],json.dumps(row['result']),json.dumps(row['metadata'])))
            c.execute("INSERT INTO validation_requests VALUES('completed')")
            c.execute('INSERT INTO planner_steps VALUES(?)',(json.dumps(step),))
            self.assertTrue(request_accounting(c)['requests_settled'])
            c.execute("UPDATE request_attempts SET state='unknown'")
            self.assertFalse(request_accounting(c)['requests_settled'])


class CompactPayloadTests(unittest.TestCase):
    def test_candidate_evidence_objections_and_weights_are_preserved(self):
        payload={'candidate':{'qualifiers':{'condition':'A AND (B OR C)'}},'evidence_catalog':[{'evidence_id':'e','quote':'complete source'}],
            'frozen_versions':{'version':'v','large_policy':{'data':'x'*15000}},
            'current_validation':{'proposal_digest':'p','consensus':{'eligible_voter_ids':['a'],'counter_evidence_ids':['e'],
                'unresolved_counterevidence':True,'duplicate_attempts':['x'*2000],
                'weight_snapshot':{'snapshot_id':'w','axes':{'source':{'scores':{'supported':.33},'available_mass':.33,'threshold':.7,
                    'seats':{'a':{'weight':.33,'effective_weight':.33,'reliability':.5,'adaptation':1,'usable':True}}}}}},
                'votes':[{'voter_id':'a','counter_evidence_ids':['e'],'checks':[{'status':'fail','note':'objection','evidence_ids':['e']}]}]},
            'history':[{'validation':{'proposal_digest':'p','votes':['x'*10000]}}]}
        original=deepcopy(payload);small=compact_planner_payload(payload)
        self.assertEqual(payload,original)
        for key in ('candidate','evidence_catalog'):self.assertEqual(small[key],payload[key])
        self.assertEqual(small['current_validation']['consensus']['counter_evidence_ids'],['e'])
        self.assertTrue(small['current_validation']['consensus']['unresolved_counterevidence'])
        self.assertEqual(small['current_validation']['consensus']['weight_snapshot']['axes']['source']['seats']['a']['effective_weight'],.33)
        self.assertEqual(small['current_validation']['votes'][0]['checks'][0]['note'],'objection')
        self.assertLess(small['input_size']['after_deduplication_bytes'],small['input_size']['before_deduplication_bytes']*.5)


class PlannerRuntimeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.PlannerRuntimeTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_budget_stop_records_completed_planner_receipt(self):
        config=deepcopy(self.f.config);config['planner'].update(durable_requests=True,compact_view=True)
        config['case_budget']={'tokens':10,'llm_calls':24}
        result=self.f.run_script([LLMResponse(fixtures.ontology(),input_tokens=9,output_tokens=8)],config=config)
        self.assertEqual(result['stop_reason'],'case_token_budget_exhausted')
        step=self.f.audit['planner_steps'][-1]
        self.assertEqual(step['status'],'interrupted')
        self.assertEqual(step['attempts'][0]['response_status'],'ok')
        self.assertTrue(step['attempts'][0]['attempt_id'])
        with self.f.repo.connect() as c:self.assertTrue(request_accounting(c)['requests_settled'])
        self.assertEqual(self.f.planner.calls[0]['planner_view_version'],'planner_compact_v2')


if __name__=='__main__':unittest.main()
