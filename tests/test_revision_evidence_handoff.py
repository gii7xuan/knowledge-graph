"""Fresh revisions retain independently reviewed supplemental source material."""
import copy
import json
import unittest
from evidence.hierarchical_retriever import _evidence
from evidence.verification import make_evidence_bundle
from review_agent.agent_state import AgentState
from review_agent.issue_ledger import IssueLedger
from review_agent.repository import ConflictError
from tests import test_review_recovery_v4 as helpers


class RevisionEvidenceHandoffTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.RevisionAndConsumerTests(methodName='runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def applied_supplement(self):
        f=self.fixture;candidate=f.repo.get_revision(f.fixture.candidate['candidate_id'])
        old=f.repo.get_audit(candidate['candidate_id'],f.fixture.source_result['audit_id'])
        bundle=old['rounds'][0]['evidence_bundle'];chunk=f.fixture.index['chunks'][0]
        start=chunk['content'].index('changing');end=chunk['content'].index('state.')+len('state.')
        extra=_evidence(chunk,start,end,'current_chunk')
        self.assertNotIn(extra['evidence_id'],[e['evidence_id'] for e in bundle['items']])
        draft=f.service.propose(candidate,f.repo.get_candidate(candidate['candidate_id'])['row_version'],
            {'qualifiers':{'modality':'can'}},[extra['evidence_id']],'source-bound supplemental quote',
            make_evidence_bundle([*bundle['items'],extra],['verified_supplement']),f.fixture.index)
        self.assertEqual(f.approve(draft)['status'],'approved');f.service.apply(draft['draft_id'],draft['draft_hash'])
        return draft,extra,copy.deepcopy(old)

    def test_fresh_review_inherits_supplement_and_protects_its_source_id(self):
        draft,extra,old=self.applied_supplement();f=self.fixture
        state=AgentState(f.repo.get_revision(draft['candidate_id']),{},'fresh-revision-audit')
        IssueLedger(state).inherit(f.repo)
        self.assertIn(extra['evidence_id'],[e['evidence_id'] for e in state.archive])
        self.assertIn(extra['evidence_id'],state.protected_ids)
        self.assertEqual(f.repo.get_audit(draft['candidate_id'],old['audit_id'])['rounds'],old['rounds'])
        self.assertEqual(state.context['revision_evidence_provenance']['draft_hash'],draft['draft_hash'])

    def test_corrupted_applied_draft_cannot_supply_new_audit_evidence(self):
        draft,extra,_=self.applied_supplement();f=self.fixture
        with f.repo.transaction(immediate=True) as connection:
            row=connection.execute('SELECT draft_json FROM revision_drafts WHERE draft_id=?',(draft['draft_id'],)).fetchone()
            value=json.loads(row[0]);value['evidence_bundle']['items'][0]['quote']='tampered'
            connection.execute('UPDATE revision_drafts SET draft_json=? WHERE draft_id=?',(json.dumps(value),draft['draft_id']))
        state=AgentState(f.repo.get_revision(draft['candidate_id']),{},'fresh-revision-audit')
        with self.assertRaises(ConflictError):IssueLedger(state).inherit(f.repo)

    def test_later_revision_can_retain_ancestor_source_without_reusing_approval(self):
        draft,extra,_=self.applied_supplement();f=self.fixture
        current=f.repo.get_candidate(draft['candidate_id'])
        f.repo.revise_candidate(draft['candidate_id'],current['row_version'],{'triple':{'tail':'C'}})
        state=AgentState(f.repo.get_revision(draft['candidate_id']),{},'later-revision-audit')
        IssueLedger(state).inherit(f.repo)
        self.assertIn(extra['evidence_id'],state.protected_ids)
        self.assertFalse(state.context['revision_evidence_provenance']['prior_approval_reused_for_current_revision'])
        self.assertEqual(state.context['revision_evidence_provenance']['source_revision'],2)
