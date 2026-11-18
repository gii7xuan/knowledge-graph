from copy import deepcopy
import unittest
from evaluation.pilot_annotations import raw_record,COLUMNS
from review_agent.annotation_contract import DIMENSIONS


class RawAnnotationTests(unittest.TestCase):
    def setUp(self):
        self.sample={'candidate':{'triple':{'head':'A','surface_relation':'is','tail':'B'}},
            'evidence_bundle':{'items':[{'evidence_id':'e1'}]}}
        self.template={'sample_id':'PILOT-001','raw_annotation':{'labels':{d:None for d in DIMENSIONS}},
            'adjudication':{'adjudication_status':'pending'}}
        self.row=['PILOT-001','A → is → B']+[None]*(len(COLUMNS)-2)

    def set(self,key,value):self.row[next(i for i,(_,f) in enumerate(COLUMNS) if f==key)]=value

    def test_blanks_are_not_unknown_labels_or_zero_time(self):
        result=raw_record(self.row,self.template,self.sample)
        self.assertTrue(all(v is None for v in result['raw_annotation']['labels'].values()))
        self.assertIsNone(result['raw_annotation']['duration_seconds'])
        self.assertEqual(result['adjudication']['adjudication_status'],'pending')

    def test_filled_claims_and_real_zero_remain_raw_not_adjudicated(self):
        self.set('source_support','supported');self.set('supporting_evidence_ids','e1;e1')
        self.set('duration_seconds',0);self.set('full_bundle_reviewed','是');self.set('saw_model_conclusions','否')
        result=raw_record(self.row,self.template,self.sample)
        self.assertEqual(result['raw_annotation']['duration_seconds'],0)
        self.assertEqual(result['raw_annotation']['supporting_evidence_ids'],['e1'])
        self.assertTrue(result['raw_annotation']['full_bundle_reviewed'])
        self.assertFalse(result['raw_annotation']['saw_model_conclusions'])
        self.assertEqual(result['adjudication']['adjudication_status'],'pending')

    def test_changed_statement_bad_enum_foreign_quote_and_negative_time_are_rejected(self):
        for field,value in (('statement','A → is → C'),('source_support','accept'),
                            ('supporting_evidence_ids','foreign'),('duration_seconds',-1),('full_bundle_reviewed',0)):
            with self.subTest(field=field):
                self.set(field,value)
                with self.assertRaises(ValueError):raw_record(self.row,self.template,self.sample)
                self.set(field,'A → is → B' if field=='statement' else None)


if __name__=='__main__':unittest.main()
