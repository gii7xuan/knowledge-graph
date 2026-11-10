"""Field correction provenance must compare every field of the frozen model schema."""
import copy
import unittest
from review_agent import response_corrections as repairs
from review_agent.contracts import sha256_json
from review_agent.validator_contracts import contract_for,validate_vote
from tests import test_validator_v6_dimensions as fixtures


class VersionedCorrectionTraceTests(unittest.TestCase):
    def vote(self,version):
        fixture=fixtures.ValidatorV6DimensionTests(methodName='runTest');fixture.setUp()
        original=fixture.response();eid=fixture.f.good['evidence_id']
        original['dimensions']['counterevidence']['evidence_ids']=[]
        if version in {'validator_response_v7','validator_response_v8'}:original['expression_findings']=[]
        policy=repairs.policy_for({})
        plan={'version':repairs.POLICY_VERSION,'original_response_hash':sha256_json(original),'proposal_digest':'p',
            'evidence_hash':'e','policy':policy,'valid_reference_ids':[eid],
            'targets':[{'key':'field_0','parts':['dimensions','counterevidence','evidence_ids'],
                'schema':{'type':'array','minItems':1,'items':{'type':'string','enum':[eid]}}}]}
        plan['plan_hash']=sha256_json(plan)
        patch={'repairs':{'field_0':[eid]}}
        merged=repairs.apply_repairs(original,plan,patch)
        contract=contract_for(version)
        vote=contract.normalize(merged)
        vote.update(response_contract_version=version,proposal_digest='p',evidence_hash='e',
            correction_policy_hash=sha256_json(policy),response_origin='field_patch_merge',
            raw_response=original,corrected_response=merged,raw_correction_response=patch,
            correction_trace={'plan':plan,'protected_judgments_unchanged':True})
        return vote

    def test_citation_patch_preserves_v6_v7_and_v8_model_fields(self):
        for version in ('validator_response_v6_2','validator_response_v6_3','validator_response_v7','validator_response_v8'):
            with self.subTest(version=version):self.assertEqual(validate_vote(self.vote(version)),[])

    def test_extended_expression_findings_cannot_be_changed_after_patch(self):
        for version in ('validator_response_v7','validator_response_v8'):
            vote=self.vote(version);vote['expression_findings']=[{'note':'tampered finding'}]
            self.assertIn('field correction differs from stored model fields',contract_for(version).validate_correction_trace(vote))

    def test_original_judgment_or_evidence_binding_cannot_be_rewritten(self):
        for field in ('proposal_digest','evidence_hash'):
            vote=self.vote('validator_response_v8');vote[field]='different'
            self.assertIn('field correction source binding mismatch',contract_for('validator_response_v8').validate_correction_trace(vote))
        vote=self.vote('validator_response_v8');vote['corrected_response']['dimensions']['triple_support']['status']='contradicted'
        self.assertIn('field correction merged response mismatch',contract_for('validator_response_v8').validate_correction_trace(vote))

    def test_cached_vote_keeps_original_patch_provenance(self):
        vote=self.vote('validator_response_v8');vote['cache_source']={'proposal_digest':'p'};vote['proposal_digest']='new-audit-proposal'
        self.assertEqual(validate_vote(vote),[])
        vote['cache_source']['proposal_digest']='foreign-source'
        self.assertIn('field correction source binding mismatch',contract_for('validator_response_v8').validate_correction_trace(vote))

    def test_rejected_raw_only_envelope_has_trace_proof_but_no_eligible_model_view(self):
        vote=self.vote('validator_response_v8');contract=contract_for(vote['response_contract_version'])
        for name in contract.RESPONSE_SCHEMA['properties']:vote.pop(name,None)
        vote.update(response_status='invalid_contract',error='semantic contract rejected before normalization')
        self.assertEqual(contract.validate_correction_trace(vote),[])
        self.assertTrue(validate_vote(vote),'raw-only rejection must not be upgraded to a full vote')
        vote['response_status']='ok'
        self.assertIn('field correction differs from stored model fields',contract.validate_correction_trace(vote))

    def test_rejected_envelope_cannot_hide_merge_tampering_or_partial_model_fields(self):
        vote=self.vote('validator_response_v8');contract=contract_for(vote['response_contract_version'])
        for name in contract.RESPONSE_SCHEMA['properties']:vote.pop(name,None)
        vote['response_status']='invalid_contract'
        changed=copy.deepcopy(vote);changed['corrected_response']['dimensions']['triple_support']['status']='contradicted'
        self.assertIn('field correction merged response mismatch',contract.validate_correction_trace(changed))
        vote['expression_findings']=[{'note':'tampered partial projection'}]
        self.assertIn('field correction differs from stored model fields',contract.validate_correction_trace(vote))


if __name__=='__main__':unittest.main()
