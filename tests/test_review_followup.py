"""Regression checks for saved receipts, lossless budgets and immutable labels."""
from copy import deepcopy
from types import SimpleNamespace
import json
from pathlib import Path
import unittest

from review_agent.correction_context import record_table, expand_table, intern_strings, expand_strings
from review_agent.mapping_trace import process
from review_agent import response_corrections as repairs, semantic_clarification as clarification
from review_agent.validators import _parse_content
from evaluation.review_followup import read, selected, window_simulation, revision_case, contract_diagnosis
from evaluation.review_samples import ReadOnlyRepository


class LosslessContextTests(unittest.TestCase):
    def test_record_table_preserves_null_absence_and_every_definition(self):
        original=[{'id':'a','definition':None,'direction':'head_to_tail'},
                  {'id':'b','direction':'head_to_tail','conditions':['仅在指定范围内']}]
        self.assertEqual(expand_table(record_table(original)),original)
        self.assertEqual(record_table([]),[])

    def test_string_pool_preserves_ids_uris_and_literal_marker_collisions(self):
        values=['C_01234567890123456789']*3+['<urn:kg:test:class:abc>','<urn:kg:test:class:def>',
            '@s0','@p0:tail','@literal:unchanged',None,False]
        self.assertEqual(expand_strings(intern_strings(values)),values)

    def test_no_preflight_is_not_a_confirmed_ontology_gap(self):
        trace=process({})
        self.assertIn('mapping_not_executed',[b['reason'] for b in trace['blockers']])
        self.assertFalse(trace['global_gap_confirmed'])
        self.assertIsNone(trace['milestones']['formally_registered'])

    def test_definition_absence_and_constraint_conflict_remain_distinct(self):
        p={'mapping_preflight':{'blocking_information':[{'kind':'relation','fields':['definition_missing_in_bound_release']}],
            'formal_mapping':{'assigned_relation_id':'R1'}},'ontology_check':{'status':'unknown','structural_status':'pass'}}
        trace=process(p)
        self.assertEqual([b['reason'] for b in trace['blockers']],['definition_missing'])
        self.assertTrue(trace['milestones']['candidate_id_assigned'])
        self.assertTrue(trace['milestones']['structural_constraints_passed'])
        self.assertFalse(trace['milestones']['full_guard_passed'])
        p['ontology_check']['status']='fail'
        self.assertIn('constraint_conflict',[b['reason'] for b in process(p)['blockers']])


SOURCE=Path(__file__).resolve().parents[1]/'outputs/dev30_v8_full_20261111'


@unittest.skipUnless((SOURCE/'review.sqlite3').exists(),'saved development receipts not installed')
class SavedReceiptRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo=ReadOnlyRepository(SOURCE/'review.sqlite3')
        cls.cases=read(SOURCE/'smoke_report.json')['cases']
        cls.packets={p['sample_id']:p for p in read(SOURCE/'review_packets.json')['samples']}
        cls.proposals={};cls.votes={}
        for c in cls.cases:cls.proposals[c['sample_id']],cls.votes[c['sample_id']]=selected(cls.repo,c)
        with cls.repo.connect() as c:
            cls.attempts={r['attempt_id']:{**dict(r),'metadata':json.loads(r['metadata_json']),
                'result':json.loads(r['result_json'] or '{}')} for r in c.execute('SELECT * FROM request_attempts')}

    def raw(self,sid):
        vote=next(v for v in self.votes[sid] if v['voter_id']=='judge_b')
        return _parse_content(vote.get('corrected_response') or vote['raw_response'])

    def test_historical_patches_and_prompts_still_validate(self):
        result=window_simulation(self.cases,self.proposals,self.votes,self.attempts)
        self.assertEqual([r['complete_proposals'] for r in result['windows']],[25,26,27,27])
        self.assertFalse(result['historical_decisions_changed'])
        late=next(r for r in result['receipts'] if r['sample_id']=='DEV-028')
        self.assertLess(late['settlement_elapsed_seconds'],150)
        self.assertGreater(late['settlement_elapsed_seconds'],149.8)

    def test_compacted_patch_keeps_all_eighteen_competitors_and_constraints(self):
        p=self.proposals['DEV-024'];raw=self.raw('DEV-024');policy=repairs.policy_for({})
        plan,errors=repairs.make_plan(raw,p,policy);self.assertFalse(errors)
        payload=repairs.repair_payload(raw,plan,p)
        context=expand_strings(payload['ontology_context'])
        props=expand_table(context['properties']);classes=expand_table(context['classes'])
        legacy=deepcopy(plan);legacy['policy']['version']='targeted_review_repairs_v1'
        original_context=repairs.repair_payload(raw,legacy,p)['ontology_context']
        self.assertEqual(props,original_context['properties'])
        self.assertEqual(classes,original_context['classes'])
        self.assertEqual(len(props),18)
        self.assertEqual(payload['original_response'],raw)

    def test_budget_fix_preserves_all_counterevidence_and_never_makes_a_vote(self):
        p=self.proposals['DEV-022'];raw=self.raw('DEV-022');policy=repairs.policy_for({})
        plan=clarification.make_plan(raw,p,policy,repairs._conflicts(raw))
        payload=clarification.payload(raw,plan,p)
        self.assertEqual(expand_table(payload['evidence']),p['evidence_bundle']['items'])
        self.assertEqual(payload['candidate'],p['candidate']['triple'])
        self.assertEqual(payload['original_response'],raw)
        self.assertTrue(repairs._conflicts(raw))
        self.assertFalse(clarification.policy()['admission_authorized'])

    def test_real_budget_and_source_plan_failure_are_explainable(self):
        rows={r['sample_id']:r for r in contract_diagnosis(self.proposals,self.votes)}
        self.assertEqual(rows['DEV-019']['source_bound_plan_ids'],[])
        self.assertEqual(rows['DEV-019']['next_attempt_kind'],'manual_review')
        for sid in ('DEV-022','DEV-024'):
            self.assertTrue(rows[sid]['new_payload_fits_input_limit'],rows[sid])

    def test_missing_mapping_fields_are_not_deterministically_guessed(self):
        raw=self.raw('DEV-024');before=deepcopy(raw);p=self.proposals['DEV-024']
        plan,_=repairs.make_plan(raw,p,repairs.policy_for({}))
        self.assertEqual({t['path'] for t in plan['targets']},
            {'/dimensions/mapping/relation_id','/dimensions/mapping/competing_relation_ids'})
        self.assertNotIn('relation_id',raw['dimensions']['mapping'])
        self.assertEqual(raw,before)

    def test_revision_preview_has_new_version_and_no_inherited_votes(self):
        packet=self.packets['DEV-020'];before=deepcopy(packet)
        row=revision_case(packet,self.proposals['DEV-020'],self.votes['DEV-020'])
        self.assertEqual(packet,before)
        self.assertEqual(row['verification_status'],'verification_incomplete')
        preview=row['suggestions'][0]
        self.assertEqual(preview['after']['revision'],packet['candidate']['revision']+1)
        self.assertFalse(preview['old_votes_reusable'])
        self.assertIsNone(row['human_correct'])

    def clarify_with(self,response):
        p=deepcopy(self.proposals['DEV-022']);raw=self.raw('DEV-022')
        p['correction_policy']=repairs.policy_for({'semantic_clarification':{'enabled':True}})
        plan=clarification.make_plan(raw,p,p['correction_policy'],repairs._conflicts(raw))
        original=deepcopy(next(v for v in self.votes['DEV-022'] if v['voter_id']=='judge_b'))
        feedback={'original_response':raw,'repair_plan':plan,'original_vote':original}
        voter={'client':SimpleNamespace(complete_json=lambda *a,**k:response)}
        return clarification.judge_once(voter,p,feedback),original

    def test_empty_clarification_records_actual_usage_and_preserves_original_ballot(self):
        from review_agent.llm_clients import LLMResponse
        result,original=self.clarify_with(LLMResponse('',finish_reason='stop',input_tokens=10,
            output_tokens=1536,latency_ms=1234,response_id='new-clarification'))
        self.assertEqual(result['semantic_clarification']['status'],'empty_response')
        self.assertEqual(result['response_id'],'new-clarification')
        self.assertEqual(result['latency_ms'],1234)
        self.assertEqual(result['raw_response'],original['raw_response'])
        self.assertFalse(result['semantic_clarification']['counts_as_vote'])

    def test_provider_truncated_clarification_is_never_accepted_as_an_explanation(self):
        from review_agent.llm_clients import LLMResponse
        result,_=self.clarify_with(LLMResponse('{}',finish_reason='length'))
        self.assertEqual(result['semantic_clarification']['status'],'output_incomplete')
        self.assertEqual(result['finish_reason'],'length')


if __name__=='__main__':unittest.main()
