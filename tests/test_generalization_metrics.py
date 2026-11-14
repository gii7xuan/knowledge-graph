import unittest
from evaluation.review_quality import quality_report,validate_annotation
from tests import test_review_quality as quality_helpers


class GeneralizationMetricTests(unittest.TestCase):
    def setUp(self):self.fixture=quality_helpers.QualityTests('runTest')

    def test_missing_truth_is_coverage_and_never_an_accuracy_score(self):
        packet=self.fixture.packet(recommendation='manual_review',source_support_result='unresolved')
        result=quality_report([packet])['generalization_metrics']
        self.assertTrue(all(m['value'] is None for m in result['metrics']))
        self.assertEqual(result['source_judgment_coverage']['determinate_predictions'],0)
        self.assertFalse(result['independent_generalization_established'])

    def test_human_disposition_and_source_labels_have_separate_denominators(self):
        packets=[self.fixture.packet('a',recommendation='wait_mapping',source_support_result='supported'),
            self.fixture.packet('b',recommendation='manual_review',source_support_result='contradicted'),
            self.fixture.packet('c',recommendation='revise',source_support_result='supported')]
        labels=[self.fixture.gold(p) for p in packets]
        labels[0]['avoidable_manual_review']=False;labels[1]['avoidable_manual_review']=True
        labels[2]['needs_revision']=True
        metrics={m['id']:m for m in quality_report(packets,labels)['generalization_metrics']['metrics']}
        self.assertEqual(metrics['source_support_judgment_accuracy']['value'],2/3)
        self.assertEqual(metrics['reasonable_manual_recommendation_rate']['value'],.5)
        self.assertEqual(metrics['avoidable_manual_recommendation_rate']['denominator'],2)
        self.assertEqual(metrics['necessary_revision_recommendation_recall']['value'],1)

    def test_disposition_labels_require_boolean_and_evidence_even_without_axis_labels(self):
        packet=self.fixture.packet();label=self.fixture.gold(packet)
        label.update(four_axes={k:None for k in label['four_axes']},admission_eligible=None,
            avoidable_manual_review=True,evidence_ids=[],note=None)
        with self.assertRaises(ValueError):validate_annotation(label,packet)

    def test_known_unnecessary_revision_is_not_counted_as_missing_truth(self):
        packet=self.fixture.packet(recommendation='manual_review',source_support_result='supported')
        label=self.fixture.gold(packet);label['needs_revision']=False
        result=quality_report([packet],[label])['generalization_metrics']['metrics'][1]
        self.assertEqual(result['denominator'],0)
        self.assertEqual(result['unknown_or_missing'],0)
        self.assertEqual(result['not_applicable'],1)
        label.update(evidence_ids=['e'],note='Independent reviewer checked disposition.',avoidable_manual_review='yes')
        with self.assertRaises(ValueError):validate_annotation(label,packet)
