"""Measured labor stays unknown without logs; label reuse checks actual axis material."""
from copy import deepcopy
import unittest
from evaluation.review_quality import bind_packet,quality_report
from evaluation.resolution_metrics import report,sampling_plan,weighted_audit_estimate
from evaluation.annotation_reuse import reuse


class ResolutionQualityTests(unittest.TestCase):
    def setUp(self):
        from tests.test_review_quality import QualityTests
        self.fixture=QualityTests('runTest')
        self.packet=self.fixture.packet()
        self.gold=self.fixture.gold(self.packet)

    def test_pure_run_identifier_change_reuses_verified_human_axes_without_claiming_a_new_review(self):
        old=self.packet;candidate=deepcopy(old['candidate']);candidate['run_id']='different-run'
        current=bind_packet(candidate,{'proposal_digest':'another-execution','ontology_context_hash':'ontology','field_spec_hash':'fields'},
            old['evidence_bundle'],old['scope_policy'])
        inherited=reuse(old,self.gold,current)
        self.assertEqual(inherited['four_axes'],self.gold['four_axes'])
        self.assertFalse(inherited['full_packet_reviewed'])
        self.assertFalse(inherited['reuse_proof']['new_human_review_performed'])
        self.assertEqual(quality_report([current],[inherited])['human_resolved'],1)
        altered=deepcopy(inherited);altered['four_axes']['source']='contradicted'
        with self.assertRaises(ValueError):quality_report([current],[altered])

    def test_definition_change_invalidates_mapping_truth_but_preserves_unchanged_source_axis(self):
        old=self.packet
        current=bind_packet(old['candidate'],{'proposal_digest':'p','ontology_context':{'properties':[{'id':'r','definition':'changed meaning'}]}},
            old['evidence_bundle'],old['scope_policy'])
        inherited=reuse(old,self.gold,current)
        self.assertEqual(inherited['four_axes']['source'],'supported')
        self.assertIsNone(inherited['four_axes']['mapping']);self.assertIsNone(inherited['admission_eligible'])
        self.assertIsNone(inherited['mapping_correct'])

    def test_missing_human_work_is_not_zero_and_shared_work_is_counted_once(self):
        rows=[(self.packet,None)]
        self.assertIsNone(report(rows)['human_work']['actual_minutes'])
        entry={'work_id':'shared-r','reviewer':'tester','recorded_at':'2026-11-14','kind':'shared_definition','minutes':12,'candidate_ids':['a']}
        self.assertEqual(report(rows,work_logs=[entry])['human_work']['actual_minutes'],12)
        with self.assertRaises(ValueError):report(rows,work_logs=[entry,entry])

    def test_sampling_includes_exclusions_revisions_and_declares_weights(self):
        packets=[self.fixture.packet(str(n),recommendation=r) for n,r in enumerate(['admit','admit','exclude','revise'])]
        result=sampling_plan(packets,fraction=.25)
        selected={r['packet_id'] for r in result['samples']}
        self.assertIn(packets[2]['packet_id'],selected);self.assertIn(packets[3]['packet_id'],selected)
        self.assertTrue(all(r['weight']==1/r['inclusion_probability'] for r in result['samples']))

    def test_weighted_quality_requires_all_selected_named_judgments(self):
        plan={'samples':[{'packet_id':'one','weight':3},{'packet_id':'two','weight':1}]}
        self.assertIsNone(weighted_audit_estimate(plan,{'one':True})['value'])
        self.assertEqual(weighted_audit_estimate(plan,{'one':True,'two':False})['value'],.75)
        with self.assertRaises(ValueError):weighted_audit_estimate(plan,{'outside':True})

    def test_partial_labels_do_not_establish_population_recall(self):
        metrics=report([(self.packet,self.gold),(self.packet,None)])['metrics']
        recall=next(m for m in metrics if m['id']=='admissible_candidate_identification_recall')
        self.assertEqual(recall['unknown'],1);self.assertIsNone(recall['value'])
