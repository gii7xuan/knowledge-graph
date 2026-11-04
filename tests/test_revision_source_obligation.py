"""Old insufficiency can close after a genuine, verified new expression recheck."""
import copy
import unittest
from review_agent.agent_state import AgentState
from review_agent.issue_ledger import IssueLedger
from tests import test_review_recovery_v4 as helpers


class RevisionSourceObligationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.RevisionAndConsumerTests(methodName='runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups);f=self.fixture
        audit=f.repo.get_audit(f.fixture.candidate['candidate_id'],f.fixture.source_result['audit_id'])
        self.bundle=audit['rounds'][0]['evidence_bundle'];ids=[e['evidence_id'] for e in self.bundle['items']]
        f.repo.update_audit_context(audit['audit_id'],{'issue_history':[{'issue_id':'old-insufficient','code':'EVIDENCE_INSUFFICIENT',
            'status':'open','critical':True,'requires_revision':False,'opened_revision':1,
            'opened_evidence_hash':self.bundle['evidence_hash'],'evidence_ids':ids}]})
        draft=f.draft();f.approve(draft);f.service.apply(draft['draft_id'],draft['draft_hash'])
        self.state=AgentState(f.repo.get_revision(draft['candidate_id']),{},'new-source-audit')
        self.ledger=IssueLedger(self.state);self.ledger.inherit(f.repo)
        self.issue=next(i for i in self.state.issues if i['code']=='EVIDENCE_INSUFFICIENT')
        self.proposal={'proposal_digest':'new-frozen-proposal','evidence_bundle':copy.deepcopy(self.bundle),'ontology_check':{'status':'unknown'}}
        self.votes=[{'voter_id':v,'source_support':'supported','reason_codes':[],'supporting_evidence_ids':[ids[0]],'counter_evidence_ids':[],
            'checks':[{'check':name,'status':'pass','evidence_ids':[ids[0]]} for name in ('triple_support','qualifiers','relation_direction','counterevidence')]} for v in 'abc']
        self.consensus={'eligible_voter_ids':list('abc'),'source_support_consensus':{'result':'supported','support_voter_ids':list('abc')}}

    def observe(self):self.ledger.observe(self.proposal,self.votes,self.consensus,3)

    def test_verified_changed_expression_can_resolve_old_insufficiency_with_same_evidence(self):
        self.observe()
        self.assertEqual(self.issue['status'],'resolved')
        self.assertEqual(self.issue['resolution_rule'],'independent_new_revision_source_recheck')
        self.assertEqual(self.proposal['evidence_bundle']['evidence_hash'],self.bundle['evidence_hash'])

    def test_revision_number_alone_without_verified_expression_change_is_insufficient(self):
        self.state.context['revision_evidence_provenance']['expression_changed']=False
        self.observe();self.assertEqual(self.issue['status'],'open')

    def test_current_source_objection_remains_open(self):
        for code in ('EVIDENCE_INSUFFICIENT','SOURCE_CONTRADICTS'):
            with self.subTest(code=code):
                self.votes[0]['reason_codes']=[code]
                self.observe();self.assertEqual(self.issue['status'],'open')

    def test_missing_grounded_qualifier_check_remains_open(self):
        self.votes[1]['checks'][1]['evidence_ids']=[]
        self.observe();self.assertEqual(self.issue['status'],'open')

    def test_ancestor_approval_does_not_close_later_revision_obligation(self):
        self.state.context['revision_evidence_provenance']['source_revision']=1
        self.observe();self.assertEqual(self.issue['status'],'open')
