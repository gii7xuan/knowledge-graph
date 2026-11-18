from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from review_agent.response_projection import apply,project,policy,validate,PROMPT_VERSION
from review_agent.contracts import sha256_json,proposal_digest
from review_agent.validator_contracts import validate_vote,prompt_for
from review_agent.validators import _judge_validator_once,validator_payload
from review_agent.llm_clients import LLMResponse
from review_agent import response_contract_v8 as v8
from tests import test_field_spec as fixtures

ROOT=Path(__file__).resolve().parents[1]


class ShapeProjectionTests(unittest.TestCase):
    def recorded(self,kind):
        value=json.loads((ROOT/'tests/fixtures/committee_reply_failures_20261117.json').read_text(encoding='utf-8'))
        return deepcopy(next(x['raw_response'] for x in value['responses'] if x['failure_kind']==kind))

    def test_recorded_sibling_ids_are_moved_without_any_semantic_change(self):
        raw=self.recorded('mapping_fields_at_wrong_level');before=deepcopy(raw)
        result,moves=apply(raw)
        self.assertEqual(raw,before);self.assertEqual(len(moves),2)
        self.assertEqual(v8.validate_response(result),[])
        restored=deepcopy(result)
        for key in ('relation_id','competing_relation_ids'):restored['dimensions'][key]=restored['dimensions']['mapping'].pop(key)
        self.assertEqual(restored,before)

    def test_ambiguous_existing_target_other_extras_and_wrong_types_are_not_projected(self):
        raw=self.recorded('mapping_fields_at_wrong_level')
        for mutate in (lambda r:r['dimensions']['mapping'].update(relation_id='different'),
                       lambda r:r['dimensions'].update(other_field='do not drop'),
                       lambda r:r['dimensions'].update(competing_relation_ids='wrong-type')):
            bad=deepcopy(raw);mutate(bad);result,moves=apply(bad)
            self.assertEqual(result,bad);self.assertFalse(moves)

    def test_semantic_conflicts_remain_invalid_and_unchanged(self):
        for kind in ('unresolved_finding_with_pass_dimension','counter_roles_with_pass_counter_check'):
            raw=self.recorded(kind);result,moves=apply(raw)
            self.assertEqual(result,raw);self.assertFalse(moves);self.assertTrue(v8.validate_response(result))


class ShapeProjectionIntegrationTests(unittest.TestCase):
    def setUp(self):
        h=fixtures.FieldSpecIntegrationTests('runTest');h.setUp();self.addCleanup(h.doCleanups)
        _,_,self.owner,audit,self.p=h.run_case(frequency=False)
        self.voter=self.owner.validators[0];self.raw=deepcopy(audit['votes'][0]['raw_response'])

    def misnested(self):
        raw=deepcopy(self.raw)
        for key in ('relation_id','competing_relation_ids'):raw['dimensions'][key]=raw['dimensions']['mapping'].pop(key)
        return raw

    def vote(self):
        raw=self.misnested()
        with patch.object(self.voter['client'],'complete_json',return_value=LLMResponse(raw,input_tokens=11,output_tokens=7)) as call:
            vote=_judge_validator_once(self.voter,self.p)
        self.assertEqual(call.call_count,1)
        self.assertEqual(vote['raw_response'],raw)
        self.assertEqual(vote['response_status'],'ok',vote)
        self.assertEqual(validate_vote(vote),[])
        return vote

    def test_real_invocation_keeps_raw_and_proof_without_extra_call(self):
        vote=self.vote()
        self.assertEqual(len(vote['response_shape_projection']['moves']),2)
        self.assertEqual(vote['input_tokens'],11)
        from review_agent.review_observability import assess_vote
        assessment=assess_vote(vote,self.p)
        self.assertTrue(assessment['whole_vote_eligible'],assessment)

    def test_tampered_or_stripped_projection_cannot_be_counted(self):
        vote=self.vote()
        for mutate in (lambda v:v['shape_projected_response']['dimensions']['mapping'].update(note='tampered projected opinion'),
                       lambda v:v['dimensions']['mapping'].update(note='tampered stored opinion'),
                       lambda v:v['response_shape_projection'].update(original_response_hash='changed'),
                       lambda v:(v.pop('response_shape_projection'),v.pop('shape_projected_response'))):
            bad=deepcopy(vote);mutate(bad)
            self.assertTrue(validate_vote(bad))
            from review_agent.review_observability import assess_vote
            self.assertFalse(assess_vote(bad,self.p)['global_trustworthy'])

    def test_cached_projection_retains_original_digest_and_evidence_binding(self):
        vote=self.vote();original=vote['proposal_digest']
        vote.update(proposal_digest='new-round',cache_source={'proposal_digest':original})
        self.assertEqual(validate(vote),[])
        vote['evidence_hash']='different-evidence';self.assertTrue(validate(vote))

    def test_old_v5_prompt_suffix_policy_and_no_projection_are_preserved(self):
        from review_agent.response_consistency import policy as consistency_policy,LEGACY_VERSION
        old=(ROOT/'prompts/triple_validator_v8_citations_v5.txt').read_text(encoding='utf-8')
        p=deepcopy(self.p);p.update(prompt_version='triple_validator_v8_citations_v5',prompt_hash=hashlib.sha256(old.encode()).hexdigest())
        p['response_consistency_policy']=consistency_policy(LEGACY_VERSION);p['response_consistency_policy_hash']=sha256_json(p['response_consistency_policy'])
        for k in ('response_projection_policy','response_projection_policy_hash','response_projection_policy_version'):p.pop(k,None)
        p['proposal_digest']=proposal_digest(p)
        self.assertEqual(prompt_for(p),old)
        self.assertEqual(validator_payload(p,self.voter)['output_instruction'],old.split('\nFINAL_RESPONSE_INSTRUCTION\n')[1].strip())
        with patch.object(self.voter['client'],'complete_json',return_value=LLMResponse(self.misnested())):
            vote=_judge_validator_once(self.voter,p)
        self.assertEqual(vote['response_status'],'invalid_contract')
        self.assertNotIn('response_shape_projection',vote)

    def test_unresolved_finding_is_classified_for_nonvoting_clarification(self):
        from review_agent.response_consistency import issues
        from review_agent.semantic_clarification import pending
        raw=ShapeProjectionTests().recorded('unresolved_finding_with_pass_dimension')
        self.assertIn('FINDING_STATUS_CONFLICT',{x['code'] for x in issues(raw)})
        result=pending({'prompt_version':PROMPT_VERSION,'raw_response':raw,'voter_id':'x'})
        self.assertIsNotNone(result);self.assertFalse(result['counts_as_vote'])

    def test_finding_status_clarification_explains_but_never_replaces_the_vote(self):
        from review_agent.response_corrections import policy_for
        from review_agent.validators import judge_validator
        p=deepcopy(self.p);cp=policy_for({'semantic_clarification':{'enabled':True}})
        p.update(correction_policy=cp,correction_policy_hash=sha256_json(cp),correction_policy_version=cp['version'])
        p['proposal_digest']=proposal_digest(p)
        raw=deepcopy(self.raw);raw['dimensions']['qualifiers']['status']='pass'
        recorded=ShapeProjectionTests().recorded('unresolved_finding_with_pass_dimension')
        raw['expression_findings']=deepcopy(recorded['expression_findings'])
        answer={'explanation':'The original qualifier pass conflicts with its unresolved modality finding.',
            'positive_support':{'status':'unresolved','evidence_ids':[]},
            'counterevidence':{'status':'unresolved','evidence_ids':[]},
            'unresolved_questions':['The original modality still requires source review.']}
        with patch.object(self.voter['client'],'complete_json',side_effect=[LLMResponse(raw),LLMResponse(answer)]) as call:
            vote=judge_validator(self.voter,p,max_retries=1)
        self.assertEqual(call.call_count,2)
        self.assertEqual(vote['raw_response'],raw)
        self.assertNotEqual(vote['response_status'],'ok')
        self.assertEqual(vote['semantic_clarification']['status'],'explanation_recorded')
        self.assertFalse(vote['semantic_clarification']['counts_as_vote'])
        self.assertEqual(call.call_args_list[1].args[0]['task_type'],'review_contradiction_clarification_v3')


if __name__=='__main__':unittest.main()
