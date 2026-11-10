"""Explicit action bindings and queued independent split-child reviews."""
import unittest
from unittest.mock import patch
from review_agent.service import ReviewService
from review_agent.review_actions import propose_split,submit,execute
from review_agent.repository import ConflictError
from tests import test_review_recovery_v4 as helpers


class ReviewActionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.RevisionAndConsumerTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture;self.repo=f.repo;self.c=f.repo.get_candidate(f.fixture.candidate['candidate_id'])
        audit=f.repo.get_audit(self.c['candidate_id'],f.fixture.source_result['audit_id']);self.p=audit['rounds'][0]
        self.request={'revision':self.c['revision'],'expected_row_version':self.c['row_version'],'audit_id':audit['audit_id'],
            'proposal_digest':self.p['proposal_digest'],'evidence_hash':self.p['evidence_bundle']['evidence_hash'],
            'children':[{'changes':{'tail':t},'evidence_ids':[self.p['evidence_bundle']['items'][0]['evidence_id']]} for t in ('C','D')],
            'reason':'MOCK split of explicit children'}
        self.service=ReviewService(f.repo,config={'consensus':{'committee_size':3,'required_votes':3,'minimum_distinct_families':3},
            'budget':{'total_timeout_seconds':30,'max_llm_calls_per_candidate':12}})

    def test_split_proposal_requires_exact_current_packet_and_stale_actions_fail(self):
        bad={**self.request,'expected_row_version':0}
        with self.assertRaises(ConflictError):propose_split(self.service,self.c['candidate_id'],bad)
        group=propose_split(self.service,self.c['candidate_id'],self.request)
        self.assertEqual(group['status'],'proposed');self.assertEqual(len(group['drafts']),2)

    def test_queued_split_review_runs_separate_requests_for_each_child(self):
        group=propose_split(self.service,self.c['candidate_id'],self.request)
        def respond(p):
            eid=p['evidence_ids'][0]
            return {'decision':'approve','field_reviews':[{'field':f,'status':'supported','evidence_ids':[eid],'note':'MOCK child-specific review'} for f in p['changed_fields']],
                'supporting_evidence_ids':[eid],'counter_evidence_ids':[],'counterevidence_reviewed_ids':p['protected_evidence_ids'],'rationale_summary':'MOCK child-specific source'}
        voters=self.fixture.fixture.voters
        for voter in voters:voter['client']._callable=respond
        job=submit(self.service,{'review_action':'review_split','group_id':group['group_id']})
        self.assertEqual(job['status'],'queued')
        before=[len(v['client'].calls) for v in voters]
        with patch('review_agent.cli._configured_validators',return_value=voters):result=execute(self.service,job['payload'])
        self.assertEqual([len(v['client'].calls)-n for v,n in zip(voters,before)],[2,2,2])
        self.assertTrue(all(r['status']=='approved' for r in result['results']))
        self.assertTrue(result['candidate_unchanged'])


if __name__=='__main__':unittest.main()
