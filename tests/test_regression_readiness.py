"""Offline gate and physical-cost reconciliation; no model or domain fixtures."""
import json
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from evaluation.regression_gate import engineering_gate, request_accounting
from evaluation.request_meter import RequestMeter


class RegressionReadinessTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE request_attempts(state TEXT); CREATE TABLE validation_requests(status TEXT);')
        self.case={'outcome':{'execution_status':'completed','execution_reason':'semantic_disagreement'}}

    def gate(self,cases=None):
        return engineering_gate([self.case] if cases is None else cases,request_accounting(self.db))

    def test_disagreement_and_known_failure_do_not_require_unanimity(self):
        self.db.execute("INSERT INTO request_attempts VALUES('failed')")
        self.db.execute("INSERT INTO validation_requests VALUES('failed')")
        self.assertTrue(self.gate()['engineering_gate_passed'])

    def test_empty_population_cannot_pass_by_vacuous_truth(self):
        self.assertFalse(self.gate([])['engineering_gate_passed'])

    def test_unknown_or_running_original_request_blocks_expansion(self):
        for state in ('unknown','running','unrecognized_state'):
            self.db.execute('DELETE FROM request_attempts')
            self.db.execute('INSERT INTO request_attempts VALUES(?)',(state,))
            self.assertFalse(self.gate()['engineering_gate_passed'])

    def test_abandoned_reservation_without_attempt_also_blocks_expansion(self):
        self.db.execute("INSERT INTO validation_requests VALUES('running')")
        self.assertFalse(self.gate()['engineering_gate_passed'])

    def test_original_late_completion_releases_gate_without_another_request(self):
        self.db.execute("INSERT INTO request_attempts VALUES('unknown')")
        self.db.execute("INSERT INTO validation_requests VALUES('unknown')")
        self.assertFalse(self.gate()['engineering_gate_passed'])
        self.db.execute("UPDATE request_attempts SET state='completed'")
        self.db.execute("UPDATE validation_requests SET status='completed'")
        self.assertTrue(self.gate()['engineering_gate_passed'])

    def test_planner_unknown_blocks_expansion_even_when_every_committee_request_settled(self):
        self.db.execute('CREATE TABLE planner_steps(record_json TEXT)')
        for attempt in ({'response_status':'api_error','outcome_unknown':True},
                        {'response_status':'timeout'},{'response_status':'started'}):
            self.db.execute('DELETE FROM planner_steps')
            self.db.execute('INSERT INTO planner_steps VALUES(?)',(json.dumps({'attempts':[attempt]}),))
            gate=self.gate()
            self.assertFalse(gate['engineering_gate_passed'])
            self.assertEqual(gate['unresolved_attempts'],0)
            self.assertEqual(gate['unresolved_planner_requests'],1)

    def test_known_planner_format_failure_is_not_an_unknown_physical_request(self):
        self.db.execute('CREATE TABLE planner_steps(record_json TEXT)')
        self.db.execute('INSERT INTO planner_steps VALUES(?)',
            (json.dumps({'attempts':[{'response_status':'invalid_action'},{'response_status':'ok'}]}),))
        self.assertTrue(self.gate()['engineering_gate_passed'])


class RequestMeterReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.path=Path(__file__).resolve().parents[1]/'outputs'/('meter_test_'+uuid4().hex+'.jsonl')
        self.addCleanup(self.path.unlink,missing_ok=True)
        self.meter=RequestMeter(self.path,{'llm_calls':2,'tokens':100,'seconds':60})
        self.client=SimpleNamespace(model_id='model',model_family='family')

    def test_late_usage_is_charged_once_and_frozen_snapshot_is_preserved(self):
        request=self.meter.begin(self.client,{},'validator',['shared-a','shared-b'],{})
        frozen=self.meter.snapshot()
        self.meter.finish(request,SimpleNamespace(input_tokens=80,output_tokens=30),None,500)
        self.meter.finish(request,SimpleNamespace(input_tokens=80,output_tokens=30),None,500)
        self.assertEqual(frozen[0]['status'],'outcome_unknown')
        self.assertEqual(self.meter.records[request]['status'],'completed')
        self.assertTrue(self.meter.records[request]['late_response'])
        self.assertEqual(self.meter.usage['shared-a']['tokens'],110)
        self.assertEqual(self.meter.usage['shared-b']['tokens'],110)
        with self.assertRaisesRegex(RuntimeError,'budget exhausted'):
            self.meter.begin(self.client,{},'validator',['shared-a'],{})
        rows=[json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(rows),3)

    def test_gateway_timeout_is_unknown_and_snapshot_cannot_mutate_journal(self):
        request=self.meter.begin(self.client,{},'validator',['a'],{})
        self.meter.finish(request,None,RuntimeError('HTTP 504 gateway timeout'),10)
        snapshot=self.meter.snapshot()
        self.assertEqual(snapshot[0]['status'],'outcome_unknown')
        snapshot[0]['case_ids'].append('foreign')
        self.assertEqual(self.meter.records[request]['case_ids'],['a'])
