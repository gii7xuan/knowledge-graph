"""A known failed seat receives its error feedback without weakening request fences."""
from copy import deepcopy
import unittest
from review_agent.validators import judge_validator
from tests import test_review_recovery_v4 as helpers


class RecoveryFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.RequestRecoveryTests(methodName='runTest')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.repo=self.fixture.repo;self.voter=self.fixture.voter

    def judge(self,feedback=None):
        return judge_validator(self.voter,helpers.proposal(),repository=self.repo,
            per_call_timeout_seconds=2,max_retries=0,_initial_feedback=feedback)

    def test_known_contract_failure_gets_feedback_on_exact_frozen_request(self):
        bad=helpers.response();bad['checks']=[]
        self.fixture.client._callable=lambda payload:bad
        first=self.judge();self.assertEqual(first['response_status'],'invalid_contract')
        before=deepcopy(self.fixture.client.calls[0])
        feedback={'error':first['error'],'validation_errors':first.get('validation_errors',[]),
                  'instruction':'Repair the same original contract, without changing the verdict policy.'}
        self.fixture.client._callable=lambda payload:helpers.response()
        second=self.judge(feedback)
        self.assertEqual(second['response_status'],'ok')
        self.assertEqual(first['request_key'],second['request_key'])
        submitted=deepcopy(self.fixture.client.calls[1])
        self.assertEqual(submitted.pop('correction_feedback'),feedback)
        self.assertEqual(submitted,before)
        self.assertEqual(len(self.repo.request_history(first['request_key'])),2)
        self.assertEqual(self.repo.request_history(first['request_key'])[1]['metadata']['feedback'],feedback)

    def test_feedback_cannot_reroll_a_valid_vote(self):
        self.fixture.client._callable=lambda payload:helpers.response()
        first=self.judge();second=self.judge({'instruction':'different feedback'})
        self.assertEqual(second['request_key'],first['request_key'])
        self.assertTrue(second['cached']);self.assertEqual(len(self.fixture.client.calls),1)

    def test_feedback_cannot_escape_an_unknown_request(self):
        from review_agent.request_cache import validation_request_key
        key=validation_request_key(self.voter,helpers.proposal())
        self.repo.claim_validation_request(key,'unknown-original-owner')
        result=self.judge({'error':'hypothetical contract error'})
        self.assertTrue(result['outcome_unknown'])
        self.assertEqual(len(self.fixture.client.calls),0)


if __name__=='__main__':unittest.main()
