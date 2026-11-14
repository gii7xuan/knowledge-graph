import unittest
from review_agent import response_contract as legacy
from review_agent.llm_clients import MockLLMClient
from review_agent.validators import judge_validator
from review_agent.review_observability import assess_vote
from tests.test_review_v2 import proposal,response


class LegacyVoteProjectionTests(unittest.TestCase):
    def test_stored_legacy_vote_does_not_require_new_contract_methods_or_invent_axes(self):
        item=proposal();item['response_contract_version']=legacy.CONTRACT_VERSION
        item['committee_members']=[{'id':'a','model_id':'legacy-model','model_family':'legacy-family'}]
        voter={'id':'a','client':MockLLMClient(lambda _:response(),model_id='legacy-model',model_family='legacy-family')}
        vote=judge_validator(voter,item)
        self.assertEqual(vote['response_status'],'ok')
        self.assertNotIn('raw_response',vote)
        result=assess_vote(vote,item)
        self.assertTrue(result['whole_vote_eligible'],result['errors'])
        self.assertTrue(all(not value['usable'] for value in result['axis_observations'].values()))
