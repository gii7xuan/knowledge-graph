"""Untouched cases can proceed; unresolved originals remain fenced and unchanged."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
from unittest.mock import MagicMock,patch
import unittest
from uuid import uuid4

from evaluation import continue_regression as runner


class UntouchedSelectionTests(unittest.TestCase):
    def setUp(self):self.bindings=[{'candidate_id':'candidate-'+str(i),'sample_id':'sample-'+str(i)} for i in range(5)]

    def test_previously_started_or_directly_requested_cases_are_excluded(self):
        selected=runner.pending_bindings(self.bindings,{'candidate-0'},{'candidate-1'})
        self.assertEqual([b['candidate_id'] for b in selected],['candidate-2','candidate-3','candidate-4'])

    def test_empty_continuation_does_not_fall_back_to_repeating_cases(self):
        with self.assertRaises(ValueError):runner.pending_bindings(self.bindings,{b['candidate_id'] for b in self.bindings},set())

    def test_duplicate_bindings_cannot_enter_dispatch(self):
        with self.assertRaises(ValueError):runner.pending_bindings(self.bindings+[self.bindings[0]],set(),set())


class MemoryRepository:
    database_path=Path('synthetic-review.sqlite3')
    def __init__(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.executescript('CREATE TABLE audit_runs(audit_id TEXT,candidate_id TEXT);'
            'CREATE TABLE request_attempts(attempt_id TEXT,request_key TEXT,state TEXT,metadata_json TEXT,result_json TEXT);'
            'CREATE TABLE validation_requests(request_key TEXT,status TEXT);'
            'CREATE TABLE planner_steps(audit_id TEXT,step_no INTEGER,record_json TEXT);')
    @contextmanager
    def connect(self):yield self.db


class ManualHoldTests(unittest.TestCase):
    def setUp(self):
        self.repo=MemoryRepository();self.addCleanup(self.repo.db.close)
        self.repo.db.execute('INSERT INTO audit_runs VALUES(?,?)',('audit','candidate'))
        self.bindings=[{'candidate_id':'candidate','sample_id':'sample'}]

    def test_manual_hold_never_clears_unknown_or_generates_an_approval(self):
        binding={'task_id':'candidate','audit_id':'audit','voter_id':'judge'}
        self.repo.db.execute('INSERT INTO request_attempts VALUES(?,?,?,?,?)',
            ('attempt','request','unknown',json.dumps({'binding':binding}),json.dumps({'http_status':504,'outcome_unknown':True})))
        self.repo.db.execute('INSERT INTO planner_steps VALUES(?,?,?)',
            ('audit',3,json.dumps({'attempts':[{'attempt_no':1,'response_status':'api_error','outcome_unknown':True,'http_status':504}]})))
        before=list(self.repo.db.iterdump())
        holds=runner.collect_manual_holds(self.repo,self.bindings)
        self.assertEqual(len(holds),2)
        self.assertEqual(before,list(self.repo.db.iterdump()))
        for hold in holds:
            self.assertEqual(hold['disposition'],'incomplete_requires_human_review')
            self.assertEqual(hold['request_outcome'],'unknown')
            self.assertFalse(hold['not_executed_confirmed'])
            self.assertFalse(hold['retry_authorized']);self.assertFalse(hold['approval_vote'])

    def test_unbound_abandoned_reservation_is_retained_as_a_manual_hold(self):
        self.repo.db.execute("INSERT INTO validation_requests VALUES('abandoned','unknown')")
        holds=runner.collect_manual_holds(self.repo,self.bindings)
        self.assertEqual(holds[0]['kind'],'unbound_request_reservation')
        self.assertIsNone(holds[0]['candidate_id'])

    def test_budget_counts_unknown_attempts_but_excludes_known_not_sent(self):
        self.repo.db.executemany('INSERT INTO request_attempts VALUES(?,?,?,?,?)',[
            ('unknown','u','unknown','{}','{"outcome_unknown":true}'),
            ('not-sent','n','not_sent','{}','{"actual_calls":0}')])
        self.repo.db.execute('INSERT INTO planner_steps VALUES(?,?,?)',
            ('audit',1,json.dumps({'attempts':[{'response_status':'api_error','outcome_unknown':True},
                                            {'response_status':'ok','cached':True}]})))
        self.assertEqual(runner.physical_calls(self.repo),2)


class ContinuationExecutionTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[1]/'outputs'/('continue_test_'+uuid4().hex)
        self.root.mkdir();self.addCleanup(shutil.rmtree,self.root)
        self.source=self.root/'source';self.source.mkdir()
        self.target=self.root/'target';self.target.mkdir()
        self.bindings=[{'candidate_id':'remaining-'+str(i),'sample_id':'sample-'+str(i)} for i in range(23)]
        self.frozen={'source_run':str(self.source),'freeze_hash':'test-freeze','manual_hold_count':3}

    def run_continuation(self):
        c=MagicMock()
        def execute(sql):
            row=MagicMock();row.fetchone.return_value=(23,) if sql.startswith('SELECT COUNT') else None
            return row
        c.execute.side_effect=execute
        repository=MagicMock();repository.connect.return_value.__enter__.return_value=c
        repository.get_revision.return_value={'run_id':'run'}
        with patch.object(runner,'verify',return_value=(self.frozen,self.bindings)),\
             patch.object(runner,'ReviewRepository',return_value=repository),\
             patch.object(runner,'settings_for',return_value={}),\
             patch.object(runner,'read',return_value={}),\
             patch.object(runner,'summarize',return_value={'engineering_gate_passed':False}),\
             patch.object(runner,'collect_manual_holds',return_value=[{'request_outcome':'unknown'}]),\
             patch.object(runner,'request_accounting',return_value={'requests_settled':False}),\
             patch.object(runner,'run_review_batch',return_value={}) as batch:
            # Keep status reads real while code fingerprint reads use an empty archive fixture.
            runner.execute(self.target)
            return batch.call_args.kwargs['candidate_ids']

    def test_dispatch_only_the_23_untouched_cases_and_keep_uncertainty(self):
        ids=self.run_continuation()
        self.assertEqual(ids,[b['candidate_id'] for b in self.bindings])
        status=json.loads((self.target/'status.json').read_text(encoding='utf-8'))
        self.assertEqual(status['status'],'processing_completed_with_manual_review')
        self.assertFalse(status['requests_settled']);self.assertFalse(status['engineering_gate_passed'])
        self.assertFalse(status['production_written'])

    def test_same_source_cannot_dispatch_a_second_fresh_copy_of_the_remaining_cases(self):
        self.run_continuation()
        self.target=self.root/'another-target';self.target.mkdir()
        with self.assertRaises(FileExistsError):self.run_continuation()
