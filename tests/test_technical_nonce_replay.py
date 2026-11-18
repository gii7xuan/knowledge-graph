import unittest
from copy import deepcopy
from evaluation.technical_smoke_20261118 import assess_nonce_replay


class TechnicalNonceReplayTests(unittest.TestCase):
    def response(self):
        return {'attempt_id':'a','response_status':'invalid_contract','finish_reason':'stop',
            'raw_response':'{"probe_token":"frozen-token"}','validation_errors':['unsupported local schema keyword']}

    def test_paid_matching_reply_can_be_replayed_without_changing_original(self):
        original=self.response();before=deepcopy(original)
        result=assess_nonce_replay(original,'frozen-token')
        self.assertEqual(original,before);self.assertEqual(result['new_provider_calls'],0)
        self.assertTrue(result['derived_local_assessment']);self.assertFalse(result['counts_as_review_vote'])

    def test_wrong_token_or_unknown_result_cannot_become_success(self):
        with self.assertRaises(ValueError):assess_nonce_replay(self.response(),'different-token')
        r=self.response();r['outcome_unknown']=True
        with self.assertRaises(ValueError):assess_nonce_replay(r,'frozen-token')


if __name__=='__main__':unittest.main()
