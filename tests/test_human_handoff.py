from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from evaluation.human_handoff import normalize,outcome
from evaluation.metrics import fixed_candidate_metrics
from evaluation.gpt_five_group import Fence


class HumanHandoffTests(unittest.TestCase):
    def test_timeout_and_invalid_contract_are_part_of_manual_review(self):
        rows=[{'status':'completed','predicted_decision':'accept'},
              {'status':'completed','predicted_decision':'reject'},
              {'status':'completed','predicted_decision':'defer'},
              {'status':'timeout','predicted_decision':'accept'},
              {'status':'technical_incomplete','predicted_decision':'defer'},
              {'status':'not_started','predicted_decision':'defer'}]
        r=fixed_candidate_metrics(rows)
        self.assertEqual(r['outcomes'],{'accept':1,'reject':1,'manual':3,'pending':1})
        self.assertEqual(r['technical_failure_handoff_count'],2)
        self.assertEqual(r['manual_rate']['numerator'],3)
        self.assertEqual(r['technical_incompletion_rate']['numerator'],2)
        self.assertEqual(sum(r['outcomes'].values()),6)
        row=normalize(rows[3])
        self.assertEqual(row['execution_status'],'timeout')
        self.assertEqual(row['predicted_decision'],'defer')
        self.assertTrue(row['human_review_required'])
        blocked=normalize({'status':'not_started','predicted_decision':'defer'},technical=True,reason='initial_committee_unavailable')
        self.assertEqual(outcome(blocked),'manual')
        self.assertEqual(blocked['status'],'human_handoff')

    def test_unknown_arm_does_not_stop_another_arm_or_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'agent.sqlite3'
            metadata={'task_type':'planner_action','proposal':{'candidate_id':'c'}}
            with closing(sqlite3.connect(path)) as c,c:
                c.execute('CREATE TABLE request_attempts(attempt_id,state,result_json,metadata_json)')
                c.execute('INSERT INTO request_attempts VALUES(?,?,?,?)',('a','unknown','{}',json.dumps(metadata)))
            fence=Fence({'llm_calls':10,'known_tokens':1000,'seconds':60});fence.register(path)
            self.assertFalse(fence.allow_request({}, {'candidate_id':'c'},'candidate_dispute_review','agent'))
            self.assertTrue(fence.allow_request({}, {'candidate_id':'c'},'candidate_dispute_review','b3_fixed_workflow'))
            self.assertTrue(fence.allow_request({}, {'candidate_id':'next'},'triple_review','common'))
            self.assertIsNone(fence.snapshot()['stop_reason'])
            self.assertEqual(fence.snapshot()['unknown_requests'],1)

    def test_valid_result_is_not_changed_by_another_methods_failure(self):
        result=normalize({'status':'completed','predicted_decision':'reject','axis_labels':{'source_support':'contradicted'}})
        self.assertEqual(outcome(result),'reject')
        self.assertFalse(result['human_review_required'])


if __name__=='__main__':unittest.main()
