"""Original-candidate role construction, immutable failures and frozen prompt compatibility."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from review_agent.contracts import sha256_json,proposal_digest
from review_agent.response_consistency import policy,frozen,issues,PROMPT_VERSION
from review_agent import response_contract_v8 as v8
from review_agent.validator_contracts import prompt_for
from review_agent.validators import validator_payload,_judge_validator_once
from review_agent.llm_clients import LLMResponse
from review_agent.request_cache import validation_request_key
from tests import test_field_spec as fixtures

ROOT=Path(__file__).resolve().parents[1]


class ConsistencyDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.recorded=json.loads((ROOT/'tests/fixtures/mapping_semantic_mismatch_20261116.json').read_text(encoding='utf-8'))

    def raw(self,voter):return deepcopy(next(r['raw_response'] for r in self.recorded['responses'] if r['voter_id']==voter))

    def test_all_recorded_failures_are_explained_without_rewriting(self):
        for voter,code in [('judge_a','CONTRADICTION_WITHOUT_COUNTER'),('judge_b','COUNTER_DISMISSED_OVERLAP'),
                           ('judge_c','EXPRESSION_CHECK_OUTSIDE_CONTRACT')]:
            with self.subTest(voter=voter):
                raw=self.raw(voter);before=sha256_json(raw)
                diagnostics=issues(raw)
                self.assertIn(code,{i['code'] for i in diagnostics})
                self.assertTrue(all(i['automatic_repair'] is False for i in diagnostics))
                self.assertEqual(before,sha256_json(raw));self.assertTrue(v8.validate_response(raw))

    def test_a_consistent_independent_negative_is_valid_and_remains_negative(self):
        # Authored alternative judgment for this engineering example, never a production repair.
        raw=self.raw('judge_a')
        for assessment in raw['evidence_assessments']:
            if assessment['role']=='support':assessment['role']='counter'
        raw['primary_evidence_ids']=[]
        raw['dimensions']['counterevidence'].update(status='fail',note='原文直接否定当前候选谓词。',issue_code='SOURCE_CONTRADICTS')
        self.assertEqual(issues(raw),[])
        self.assertEqual(v8.validate_response(raw),[])
        normalized=v8.normalize(raw)
        self.assertEqual(normalized['source_support'],'contradicted')
        self.assertEqual(normalized['review_axes']['source']['status'],'contradicted')
        self.assertEqual(normalized['review_axes']['expression']['status'],'semantic_revision')
        self.assertTrue(normalized['counter_evidence_ids'])
        self.assertEqual(normalized['dimensions']['mapping']['status'],'candidate_mapping_error')

    def test_inspected_material_does_not_mean_dismissed_or_positive(self):
        raw=self.raw('judge_a')
        raw['evidence_assessments']=[{'evidence_id':'counter','role':'counter','note':'contradicts current candidate'}]
        raw['primary_evidence_ids']=[];raw['counterevidence_reviewed_ids']=[]
        raw['dimensions']['counterevidence'].update(status='fail',evidence_ids=['counter'])
        self.assertEqual(issues(raw),[])
        raw['counterevidence_reviewed_ids']=['counter']
        self.assertIn('COUNTER_DISMISSED_OVERLAP',{i['code'] for i in issues(raw)})

    def test_malformed_structures_do_not_break_diagnostics(self):
        for value in (None,[],{}, {'dimensions':[],'evidence_assessments':[{},None],'expression_findings':[[],{}]}):
            self.assertIsInstance(issues(value),list)


class ConsistencyPromptTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.FieldSpecIntegrationTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        _,_,owner,audit,self.proposal=f.run_case(frequency=False)
        self.voter=owner.validators[0];self.raw=deepcopy(audit['votes'][0]['raw_response'])

    def test_new_policy_is_sent_and_derived_from_actual_allowed_checks(self):
        p=self.proposal;payload=validator_payload(p,self.voter)
        self.assertEqual(p['prompt_version'],PROMPT_VERSION)
        self.assertEqual(payload['response_consistency_policy'],policy())
        self.assertEqual(policy()['expression_checks'],v8.RESPONSE_SCHEMA['properties']['expression_findings']['items']['properties']['check']['enum'])
        self.assertIn('exact_original_candidate',policy()['evidence_role_target'])
        self.assertEqual(v8.validate_response(self.raw),[])

    def test_tampered_policy_is_rejected_before_provider_dispatch(self):
        p=deepcopy(self.proposal);p['response_consistency_policy']['evidence_role_target']='corrected_sentence'
        p['response_consistency_policy_hash']=sha256_json(p['response_consistency_policy'])
        p['proposal_digest']=proposal_digest(p)
        with patch.object(self.voter['client'],'complete_json') as call:result=_judge_validator_once(self.voter,p)
        self.assertEqual(result['error_code'],'OUTPUT_DELIVERY_PREFLIGHT_FAILED');call.assert_not_called()

    def test_policy_is_bound_into_new_proposal_digest_without_rewriting_earlier_tickets(self):
        p=deepcopy(self.proposal);old=proposal_digest(p)
        p['response_consistency_policy_hash']='tampered'
        self.assertNotEqual(proposal_digest(p),old)
        legacy=deepcopy(self.proposal);legacy.pop('response_consistency_binding_version')
        first_smoke_digest=proposal_digest(legacy)
        self.assertEqual(first_smoke_digest,proposal_digest(deepcopy(legacy)))
        self.assertEqual(frozen(legacy),policy())

    def test_historical_v4_prompt_and_wire_remain_separate(self):
        text=(ROOT/'prompts/triple_validator_v8_citations_v4.txt').read_text(encoding='utf-8')
        p=deepcopy(self.proposal);p.update(prompt_version='triple_validator_v8_citations_v4',prompt_hash=hashlib.sha256(text.encode()).hexdigest())
        p.pop('response_consistency_policy');p.pop('response_consistency_policy_hash');p['proposal_digest']=proposal_digest(p)
        self.assertEqual(prompt_for(p),text);self.assertIsNone(frozen(p))
        self.assertNotIn('response_consistency_policy',validator_payload(p,self.voter))
        self.assertNotEqual(validation_request_key(self.voter,p),validation_request_key(self.voter,self.proposal))

    def test_original_invalid_reply_is_retained_with_structured_diagnostics(self):
        recorded=json.loads((ROOT/'tests/fixtures/mapping_semantic_mismatch_20261116.json').read_text(encoding='utf-8'))
        raw=recorded['responses'][0]['raw_response'];before=sha256_json(raw)
        with patch.object(self.voter['client'],'complete_json',return_value=LLMResponse(raw,input_tokens=7,output_tokens=5)):
            vote=_judge_validator_once(self.voter,self.proposal)
        self.assertEqual(vote['response_status'],'invalid_contract')
        self.assertIn('CONTRADICTION_WITHOUT_COUNTER',{i['code'] for i in vote['consistency_issues']})
        self.assertEqual(sha256_json(vote['raw_response']),before);self.assertEqual(vote['input_tokens'],7)


if __name__=='__main__':unittest.main()
