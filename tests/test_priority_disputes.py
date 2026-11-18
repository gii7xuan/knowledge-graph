from threading import Lock
import time
import unittest
from review_agent.agent_state import AgentBudget
from review_agent.consensus import Committee
from tests import test_review_recovery_v4 as fixtures


class RevisionSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.RevisionAndConsumerTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)

    def answer(self,p):
        eid=p['evidence_ids'][0]
        return {'decision':'approve','field_reviews':[{'field':f,'status':'supported','evidence_ids':[eid],'note':'source checked'} for f in p['changed_fields']],
            'supporting_evidence_ids':[eid],'counter_evidence_ids':[],
            'counterevidence_reviewed_ids':p['protected_evidence_ids'],'rationale_summary':'source checked'}

    def test_serial_revision_seats_respect_the_frozen_parallel_limit(self):
        active=peak=0;lock=Lock()
        def answer(p):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active)
            try:time.sleep(.025);return self.answer(p)
            finally:
                with lock:active-=1
        for v in self.f.fixture.voters:v['client']._callable=answer
        draft=self.f.draft()
        result=self.f.service.review(draft['draft_id'],self.f.fixture.voters,Committee(['a','b','c']),
            AgentBudget({'total_timeout_seconds':30,'max_parallel_validators':1}))
        self.assertEqual(result['status'],'approved',result);self.assertEqual(peak,1)

    def test_unknown_result_stops_queued_seats_without_counting_them_as_requests(self):
        voters=self.f.fixture.voters
        before=[len(v['client'].calls) for v in voters]
        for v in voters:v['client']._callable=self.answer
        def unknown(p):raise TimeoutError('unknown synthetic outcome')
        voters[0]['client']._callable=unknown
        draft=self.f.draft()
        result=self.f.service.review(draft['draft_id'],voters,Committee(['a','b','c']),
            AgentBudget({'total_timeout_seconds':30,'max_parallel_validators':1}))
        self.assertEqual([len(v['client'].calls)-n for v,n in zip(voters,before)],[1,0,0])
        self.assertEqual(result['status'],'review_incomplete')
        self.assertTrue(result['votes'][0]['outcome_unknown'])
        self.assertTrue(all(v['not_sent'] and v['actual_calls']==0 for v in result['votes'][1:]))


if __name__=='__main__':unittest.main()
