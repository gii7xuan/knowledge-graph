"""Six quality metrics require exact packets, honest denominators and human truth."""
import copy
import unittest
from evaluation.review_quality import bind_packet,annotation_template,quality_report,repeat_consistency


class QualityTests(unittest.TestCase):
    def packet(self,name='a',**system):
        return bind_packet({'candidate_id':name,'revision':1,'triple':{'head':'A','surface_relation':'relates','tail':'B'}},
            {'proposal_digest':'proposal','ontology_context_hash':'ontology','field_spec_hash':'fields'},
            {'evidence_hash':'evidence','items':[{'evidence_id':'e','quote':'A relates B.'}]},
            {'version':'task'},system={'prediction_observed':True,**system})

    def gold(self,p,eligible=True):
        return {**annotation_template(p),'status':'resolved','reviewer':'human-test','annotated_at':'2026-11-09',
            'full_packet_reviewed':True,'saw_model_conclusions':False,'admission_eligible':eligible,
            'four_axes':{'source':'supported','expression':'standard','mapping':'valid' if eligible else 'conflict','scope':'include'},
            'evidence_ids':['e'],'note':'Human checked the complete current packet.'}

    def metrics(self,packets,labels=(),**kwargs):
        return {m['id']:m for m in quality_report(packets,labels,**kwargs)['metrics']}

    def test_zero_auto_admissions_is_unknown_precision_and_zero_usable_coverage(self):
        p=self.packet();m=self.metrics([p],[self.gold(p)])
        self.assertIsNone(m['auto_admission_precision']['value'])
        self.assertEqual(m['usable_auto_admission_rate']['value'],0)
        self.assertIsNone(m['revision_success_rate']['value'])

    def test_all_manual_and_missing_labels_cannot_be_called_success(self):
        m=self.metrics([self.packet()])
        self.assertTrue(all(x['value'] is None for x in m.values()))

    def test_actual_bad_auto_release_and_missed_good_candidate_are_counted(self):
        bad=self.packet('bad',auto_admitted=True,mapping_completed=True,revision_requested=True)
        good=self.packet('good',revision_requested=True)
        m=self.metrics([bad,good],[self.gold(bad,False),self.gold(good)])
        self.assertEqual(m['auto_admission_precision']['value'],0)
        self.assertEqual(m['false_admission_rate']['value'],1)
        self.assertEqual(m['usable_auto_admission_rate']['value'],0)
        self.assertEqual(m['mapping_accuracy']['value'],0)
        self.assertEqual(m['unnecessary_revision_rate']['value'],1)

    def test_unknown_auto_truth_keeps_the_full_denominator(self):
        a=self.packet('a',auto_admitted=True);b=self.packet('b',auto_admitted=True)
        result=self.metrics([a,b],[self.gold(a)])['auto_admission_precision']
        self.assertEqual(result['denominator'],2);self.assertEqual(result['unknown_count'],1)
        self.assertIsNone(result['value'])

    def test_stale_evidence_candidate_policy_or_mapping_labels_are_rejected(self):
        p=self.packet()
        for field in ('candidate_content_hash','evidence_hash','ontology_context_hash','scope_policy_hash','field_spec_hash'):
            gold=self.gold(p);gold['binding'][field]='old'
            with self.subTest(field=field),self.assertRaises(ValueError):self.metrics([p],[gold])

    def test_model_labels_and_unsupported_admission_are_not_gold(self):
        p=self.packet()
        for change in ({'annotated_by':'model'},{'full_packet_reviewed':False},{'reviewer':None}):
            with self.assertRaises(ValueError):self.metrics([p],[{**self.gold(p),**change}])
        gold=self.gold(p);gold['four_axes']['mapping']='unassigned'
        with self.assertRaises(ValueError):self.metrics([p],[gold])

    def test_changed_packet_content_cannot_reuse_its_old_identity(self):
        p=self.packet();label=self.gold(p);p['evidence_bundle']['items'][0]['quote']='Different source'
        with self.assertRaises(ValueError):self.metrics([p],[label])

    def test_applied_split_needs_every_child_truth_and_original_problem_review(self):
        parent=self.packet('parent');a=self.packet('a');b=self.packet('b')
        operation={'revision_id':'split','parent_packet_id':parent['packet_id'],'child_packet_ids':[a['packet_id'],b['packet_id']]}
        review={**operation,'annotated_by':'human','reviewer':'reviewer','annotated_at':'2026-11-09',
            'original_issue_resolved':True,'introduced_new_problem':False}
        packets=[parent,a,b]
        unknown=self.metrics(packets,revision_operations=[operation])['revision_success_rate']
        self.assertIsNone(unknown['value']);self.assertEqual(unknown['denominator'],1)
        with self.assertRaises(ValueError):self.metrics(packets,[self.gold(a)],revision_operations=[operation],revision_assessments=[review])
        result=self.metrics(packets,[self.gold(a),self.gold(b)],revision_operations=[operation],revision_assessments=[review])
        self.assertEqual(result['revision_success_rate']['value'],1)
        review['introduced_new_problem']=True
        self.assertEqual(self.metrics(packets,[self.gold(a),self.gold(b)],revision_operations=[operation],revision_assessments=[review])['revision_success_rate']['value'],0)

    def test_repeat_consistency_rejects_changed_input_and_separates_failed_votes(self):
        vote={'voter_id':'a','response_status':'ok','dimensions':{'triple_support':{'status':'supported'}}}
        a={'input_fingerprint':'fixed','votes':[vote]};b=copy.deepcopy(a)
        b['votes'][0]['dimensions']['triple_support']['status']='contradicted'
        report=repeat_consistency([a,b])
        self.assertTrue(report['voters'][0]['changed'])
        b['votes'][0]['response_status']='invalid_contract'
        self.assertIsNone(repeat_consistency([a,b])['voters'][0]['changed'])
        b['input_fingerprint']='changed'
        with self.assertRaises(ValueError):repeat_consistency([a,b])

    def test_repeat_compares_meaning_effect_and_axes_not_just_dimension_status(self):
        vote={'voter_id':'a','response_status':'ok','dimensions':{'qualifiers':{'status':'fail'}},
            'review_axes':{'expression':{'status':'lossless_normalization'}},
            'expression_findings':[{'check':'qualifiers','kind':'field_placement','rule_id':'FREQ-001','meaning_effect':'preserved','note':'first wording'}]}
        a={'input_fingerprint':'fixed','votes':[vote]};b=copy.deepcopy(a)
        b['votes'][0]['expression_findings'][0]['note']='different wording, same judgment'
        self.assertFalse(repeat_consistency([a,b])['voters'][0]['changed'])
        b['votes'][0]['expression_findings'][0].update(kind='semantic_error',meaning_effect='changed')
        b['votes'][0]['review_axes']['expression']['status']='semantic_revision'
        result=repeat_consistency([a,b])['voters'][0]
        self.assertTrue(result['changed']);self.assertTrue(result['findings_changed']);self.assertEqual(result['changed_axes'],['expression'])


if __name__=='__main__':unittest.main()
