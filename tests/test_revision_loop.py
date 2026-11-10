"""Revision meaning, separate child proofs and fresh-version review obligations."""
import copy
import json
import unittest
from review_agent.revision_loop import analyze,history
from review_agent.repository import ConflictError,ValidationError
from review_agent.revision_service import revision_prompt,response_errors
from review_agent.agent_state import AgentState
from review_agent.issue_ledger import IssueLedger
from tests import test_review_recovery_v4 as helpers


class RevisionDeltaTests(unittest.TestCase):
    def setUp(self):
        self.before={'candidate_id':'c','revision':1,'triple':{'head':'A','surface_relation':'总是影响','tail':'B',
            'qualifiers':{'polarity':'positive','condition':'高温时','quantifier':'总是','modality':'asserted'},
            'qualifier_status':{'polarity':'extracted','condition':'extracted','quantifier':'extracted','modality':'extracted'}}}
        self.after=copy.deepcopy(self.before);self.after['revision']=2;self.after['triple']['surface_relation']='影响'
        self.bundle={'items':[{'evidence_id':'e','anchor_valid':True,'quote':'高温时A总是影响B。'}]}

    def test_exact_duplicate_cleanup_has_a_preservation_path_but_still_needs_new_mapping(self):
        report=analyze(self.before,self.after,self.bundle)
        self.assertEqual(report['kind'],'lossless_duplicate_cleanup')
        self.assertEqual(report['source_validation_mode'],'bounded_preservation')
        self.assertTrue(report['requires_new_mapping_review']);self.assertFalse(report['old_votes_reusable'])

    def test_lost_condition_frequency_negation_or_modality_requires_full_review(self):
        for key,value in [('condition',None),('quantifier',None),('polarity','negative'),('modality','can')]:
            with self.subTest(key=key):
                after=copy.deepcopy(self.after);after['triple']['qualifiers'][key]=value
                self.assertEqual(analyze(self.before,after,self.bundle)['source_validation_mode'],'full_expression')

    def test_unanchored_cleanup_or_endpoint_change_is_not_simplified(self):
        self.bundle['items'][0]['anchor_valid']=False
        self.assertEqual(analyze(self.before,self.after,self.bundle)['kind'],'semantic_change')
        self.bundle['items'][0]['anchor_valid']=True;self.after['triple']['tail']='C'
        self.assertEqual(analyze(self.before,self.after,self.bundle)['kind'],'semantic_change')


class RevisionLoopIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.RevisionAndConsumerTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture;self.repo=f.repo;self.candidate=f.repo.get_revision(f.fixture.candidate['candidate_id'])
        self.bundle=f.repo.get_audit(self.candidate['candidate_id'],f.fixture.source_result['audit_id'])['rounds'][0]['evidence_bundle']
        self.eid=self.bundle['items'][0]['evidence_id'];self.service=f.service

    def split(self):
        return self.service.propose_split(self.candidate,self.repo.get_candidate(self.candidate['candidate_id'])['row_version'],
            [{'changes':{'tail':tail},'evidence_ids':[self.eid]} for tail in ('C','D')],'MOCK split; not a factual quality measurement',self.bundle,self.fixture.fixture.index,
            protected_ids=[self.eid])

    def test_revision_stores_before_after_and_has_no_old_active_decision(self):
        draft=self.fixture.draft();self.assertEqual(draft['change_analysis']['source_validation_mode'],'full_expression')
        self.fixture.approve(draft);self.service.apply(draft['draft_id'],draft['draft_hash'])
        current=self.repo.get_candidate(self.candidate['candidate_id'])
        self.assertEqual(current['revision_lineage']['parent_revision'],1)
        self.assertEqual(current['revision_lineage']['revision'],2)
        self.assertIsNone(self.repo.latest_decision(current['candidate_id']))
        with self.repo.connect() as c:chain=history(c,current['candidate_id'])
        self.assertEqual([r['revision'] for r in chain['revisions']],[1,2])
        self.assertFalse(chain['old_votes_reusable'])

    def test_human_partial_qualifier_patch_preserves_every_other_qualifier(self):
        current=self.repo.get_candidate(self.candidate['candidate_id'])
        old=copy.deepcopy(current['triple']['qualifiers'])
        result=self.repo.apply_human_feedback({'candidate_id':current['candidate_id'],'expected_row_version':current['row_version'],
            'revision':current['revision'],'action':'modify','modifications':{'qualifiers':{'modality':'can'}},
            'idempotency_key':'partial-patch','reviewer':'fixture','labels':{},'evidence_ids':[]})
        updated=self.repo.get_candidate(current['candidate_id'])
        self.assertEqual(updated['triple']['qualifiers'],{**old,'modality':'can'})
        self.assertEqual(updated['revision_lineage']['operation'],'human_revision')

    def test_split_has_independent_drafts_and_no_candidates_before_all_approvals(self):
        group=self.split();self.assertEqual(len(group['drafts']),2)
        self.assertNotEqual(group['drafts'][0]['draft_hash'],group['drafts'][1]['draft_hash'])
        self.assertIn('THIS child',revision_prompt(group['drafts'][0]))
        self.fixture.approve(group['drafts'][0])
        with self.assertRaises(ConflictError):self.service.apply_split(group['group_id'])
        with self.assertRaises(ConflictError):self.service.apply(group['draft_ids'][0],group['drafts'][0]['draft_hash'])
        with self.repo.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM candidates').fetchone()[0],1)

    def test_all_children_apply_atomically_and_get_their_own_fresh_audits(self):
        group=self.split()
        for d in group['drafts']:self.fixture.approve(d)
        result=self.service.apply_split(group['group_id'])
        self.assertEqual(self.repo.get_job(result['reaudit_job_id'])['payload']['candidate_ids'],group['child_candidate_ids'])
        for child_id in result['child_candidate_ids']:
            child=self.repo.get_revision(child_id);self.assertEqual(child['revision'],1)
            self.assertEqual(child['revision_lineage']['parent_candidate_id'],self.candidate['candidate_id'])
            self.assertIsNone(self.repo.latest_decision(child_id))
            state=AgentState(child,{},'fresh-child-audit');IssueLedger(state).inherit(self.repo)
            self.assertFalse(state.context['split_evidence_provenance']['parent_votes_reused'])
            self.assertIn(self.eid,state.protected_ids)
        self.assertEqual(self.repo.get_candidate(self.candidate['candidate_id'])['workflow_state'],'superseded_by_split')
        with self.repo.connect() as c:chain=history(c,self.candidate['candidate_id'])
        self.assertTrue(chain['revisions'][-1]['decision']['historical_inactive'])

    def test_parent_or_sibling_ballots_do_not_approve_another_child(self):
        group=self.split();first=self.fixture.approve(group['drafts'][0])
        second=group['drafts'][1]
        with self.repo.transaction(immediate=True) as c:
            c.execute("UPDATE revision_drafts SET status='approved',result_json=? WHERE draft_id=?",(json.dumps(first),second['draft_id']))
        with self.assertRaises(ConflictError):self.service.apply_split(group['group_id'])
        with self.repo.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM candidates').fetchone()[0],1)

    def test_stale_parent_and_invalid_child_sources_do_not_publish_partial_children(self):
        with self.assertRaises(ValidationError):
            self.service.propose_split(self.candidate,self.repo.get_candidate(self.candidate['candidate_id'])['row_version'],
                [{'changes':{'tail':'C'},'evidence_ids':[self.eid]},{'changes':{'tail':'D'},'evidence_ids':['foreign']}],
                'MOCK invalid child source',self.bundle,self.fixture.fixture.index)
        with self.repo.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM revision_drafts').fetchone()[0],0)
        group=self.split()
        for d in group['drafts']:self.fixture.approve(d)
        current=self.repo.get_candidate(self.candidate['candidate_id'])
        self.repo.revise_candidate(current['candidate_id'],current['row_version'],{'triple':{'tail':'changed'}})
        with self.assertRaises(ConflictError):self.service.apply_split(group['group_id'])
        with self.repo.connect() as c:chain=history(c,self.candidate['candidate_id'])
        self.assertEqual(chain['split_groups'][0]['status'],'stale')


if __name__=='__main__':unittest.main()
