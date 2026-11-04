"""Disk failure after voting does not require repeating an original committee."""
from copy import deepcopy
import sqlite3
import unittest
from unittest.mock import patch
from review_agent.completed_audit_recovery import restore_completed_audit
from review_agent.llm_clients import MockLLMClient
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.repository import ConflictError
from tests import test_ontology_extension_agent as fixtures


class CompletedAuditRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.OntologyExtensionTests(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture;self.candidate=deepcopy(f.candidate);self.candidate['candidate_id']='disk-failure-complete-review'
        f.repo.add_candidate(self.candidate);self.audit_id='audit-failed-commit-fixture'
        def plan(payload):
            if not payload['ontology_checked']:return fixtures.action('inspect_ontology',relation_id=None,surface_relation='regulates',issue='full lookup')
            if not payload['current_validation']:return fixtures.action('run_validation')
            return fixtures.action('finish_review',stop_reason='complete frozen fixture review')
        self.orchestrator=ReviewOrchestrator(f.repo,OntologyGuard(load_snapshot(f.base.ontology)),f.source_voters,
            config=f.config,planner_client=MockLLMClient(plan))
        with patch.object(f.repo,'commit_audit_if_current',side_effect=sqlite3.OperationalError('disk I/O error')):
            with self.assertRaises(sqlite3.OperationalError):
                self.orchestrator.audit_candidate(self.candidate['candidate_id'],1,f.index,context={'audit_id':self.audit_id})
        f.repo.finish_incomplete_audit(self.audit_id,'failed',{'error':'disk I/O error'})

    def restore(self):return restore_completed_audit(self.fixture.repo,self.orchestrator,self.candidate['candidate_id'],self.audit_id,reason='Restore complete original review after simulated local disk failure.')

    def test_complete_original_committee_restores_authority_without_a_model_call(self):
        f=self.fixture;before=f.repo.get_audit(self.candidate['candidate_id'],self.audit_id)
        calls=[len(v['client'].calls) for v in f.source_voters]
        result=self.restore()
        self.assertTrue(result['committed']);self.assertTrue(result['independent_review_completed']);self.assertEqual(result['model_calls'],0)
        self.assertEqual([len(v['client'].calls) for v in f.source_voters],calls)
        after=f.repo.get_audit(self.candidate['candidate_id'],self.audit_id)
        self.assertEqual(before['votes'],after['votes'])
        self.assertEqual(f.repo.latest_decision(self.candidate['candidate_id'])['audit_id'],self.audit_id)

    def test_incomplete_original_committee_cannot_be_filled_or_declared_complete(self):
        f=self.fixture
        with f.repo.transaction(immediate=True) as connection:
            connection.execute('DELETE FROM validator_votes WHERE audit_id=? AND voter_id=?',(self.audit_id,'c'))
        calls=[len(v['client'].calls) for v in f.source_voters]
        with self.assertRaises(ValueError):self.restore()
        self.assertEqual([len(v['client'].calls) for v in f.source_voters],calls)
        self.assertIsNone(f.repo.latest_decision(self.candidate['candidate_id']))

    def test_newer_audit_prevents_historical_decision_replacement(self):
        f=self.fixture
        f.repo.start_audit(self.candidate['candidate_id'],1,{'audit_id':'newer-unfinished-audit','mode':'shadow'})
        with self.assertRaises(ConflictError):self.restore()

    def test_checkpoint_failure_after_commit_does_not_downgrade_the_completed_audit(self):
        f=self.fixture;calls=[len(v['client'].calls) for v in f.source_voters]
        with patch.object(f.repo,'save_agent_checkpoint',side_effect=sqlite3.OperationalError('post-commit disk I/O error')):
            with self.assertRaisesRegex(sqlite3.OperationalError,'post-commit'):self.restore()
        after=f.repo.get_audit(self.candidate['candidate_id'],self.audit_id)
        self.assertEqual(after['status'],'completed')
        self.assertEqual(f.repo.latest_decision(self.candidate['candidate_id'])['audit_id'],self.audit_id)
        self.assertEqual([len(v['client'].calls) for v in f.source_voters],calls)
