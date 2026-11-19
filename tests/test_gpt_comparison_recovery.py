"""Regression against settled original receipts; no provider calls are permitted."""
from pathlib import Path
import tempfile
import unittest

from evaluation.gpt_five_group import ROOT,settings,backup,read
from evaluation.gpt_comparison_recovery import restore_initial,branch_window
from review_agent.repository import ReviewRepository
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.runtime import AgentRuntime
from review_agent.cli import _configured_validators
from review_agent.fixed_candidate_workflow import next_action
from document.hierarchy_index import load_document_index
from ontologyBuild.release_service import load_release

PRIOR=ROOT/'outputs/gpt40_five_group_20261119_frozen'


@unittest.skipUnless((PRIOR/'interruption_receipt.json').exists(),'requires settled real-prefix regression material')
class RecordedRecoveryTests(unittest.TestCase):
    def runtime(self,folder,ordinal):
        samples=read(PRIOR/'model_inputs.json');sample=samples[ordinal-1];candidate=sample['candidate']
        destination=folder/'review.sqlite3'
        backup(PRIOR/('case_'+str(ordinal).zfill(2))/'common.sqlite3',destination)
        repo=ReviewRepository(destination);repo.initialize();config=settings(folder)
        voters=_configured_validators(config)
        def forbid():raise AssertionError('offline recovery attempted provider dispatch')
        for v in voters:v['client'].dispatch_guard=forbid
        release=load_release(candidate['ontology_release_path'])
        owner=ReviewOrchestrator(repo,OntologyGuard(load_snapshot(release.snapshot_path)),voters,config=config)
        runtime=AgentRuntime(owner)
        index=load_document_index(sample['document_index_path'],candidate['source_index_hash'],require_verified=True)
        restore_initial(runtime,candidate,index);branch_window(runtime,folder)
        return runtime

    def test_two_supplements_leave_the_single_focused_review_available(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime=self.runtime(Path(temp),1)
            for _ in range(2):
                action=next_action(runtime.state)
                self.assertEqual(action['action'],'search_evidence')
                runtime.state.step_no+=1;runtime.execute(action)
                self.assertIn(runtime.state.observations[-1]['status'],{'ok','empty'},runtime.state.observations[-1])
            focus=next_action(runtime.state)
            self.assertEqual(focus['action'],'review_dispute')
            runtime.policy.validate_request(focus,runtime.state)
            with runtime.repository.connect() as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM request_attempts').fetchone()[0],3)

    def test_late_receipts_restore_all_seats_without_repeating_initial_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime=self.runtime(Path(temp),7)
            current=runtime.state.current_validation()
            self.assertIsNotNone(current)
            self.assertEqual({v['voter_id'] for v in current['votes']},{'judge_a','judge_b','judge_c'})
            self.assertEqual(runtime.budget.usage['llm_calls'],3)
            self.assertEqual(runtime.budget.usage['input_tokens']+runtime.budget.usage['output_tokens'],55474)
            with runtime.repository.connect() as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM request_attempts').fetchone()[0],3)


if __name__=='__main__':unittest.main()
