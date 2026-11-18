import time
import unittest
from unittest.mock import patch
from review_agent.llm_clients import MockLLMClient
from review_agent import validators
from review_agent.targeted_validation import judge_targeted


class ValidatorDispatchLimitTests(unittest.TestCase):
    def test_targeted_v8_respects_shorter_caller_cap(self):
        observed=[]
        voter={'id':'a','client':MockLLMClient([]),'request_timeout_seconds':2,'http_timeout_seconds':2}
        def call(v,p,f,timeout):
            observed.append(timeout);return {'response_status':'ok','actual_calls':1}
        with patch.object(validators,'vote_binding',side_effect=lambda v,p:{'voter_id':v['id']}),patch.object(validators,'_bounded_attempt',side_effect=call):
            judge_targeted(voter,{'correction_policy':{'limits':{'max_initial_attempts_per_seat':1}}},
                max_retries=0,timeout=.05,repository=None,deadline=time.time()+10,owner=None,key=None)
        self.assertEqual(observed,[.05])

    def test_legacy_validator_respects_shorter_caller_cap(self):
        observed=[]
        voter={'id':'a','client':MockLLMClient([]),'request_timeout_seconds':2}
        def call(v,p,f,timeout):
            observed.append(timeout);return {'response_status':'ok','actual_calls':1}
        with patch.object(validators,'vote_binding',side_effect=lambda v,p:{'voter_id':v['id']}),patch.object(validators,'_bounded_attempt',side_effect=call):
            validators.judge_validator(voter,{},per_call_timeout_seconds=.05,deadline_epoch=time.time()+10)
        self.assertEqual(observed,[.05])

    def test_unknown_result_prevents_queued_seats_from_being_sent(self):
        calls=[];voters=[{'id':x} for x in 'abc']
        def call(v,p,**kwargs):
            calls.append(v['id']);return {'voter_id':v['id'],'response_status':'timeout','outcome_unknown':True,'actual_calls':1}
        with patch.object(validators,'vote_binding',side_effect=lambda v,p:{'voter_id':v['id']}),patch.object(validators,'judge_validator',side_effect=call):
            votes=validators.judge_independently(voters,{},max_workers=1)
        self.assertEqual(calls,['a'])
        self.assertTrue(all(v['not_sent'] and v['actual_calls']==0 for v in votes[1:]))

    def test_valid_rejection_does_not_stop_other_independent_seats(self):
        calls=[];voters=[{'id':x} for x in 'abc']
        def call(v,p,**kwargs):
            calls.append(v['id']);return {'voter_id':v['id'],'response_status':'ok','verdict':'invalid','actual_calls':1}
        with patch.object(validators,'judge_validator',side_effect=call):
            votes=validators.judge_independently(voters,{},max_workers=1)
        self.assertEqual(calls,['a','b','c'])
        self.assertEqual(len(votes),3)


if __name__=='__main__':unittest.main()
