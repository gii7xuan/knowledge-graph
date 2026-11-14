"""Source isolation and duplicate-fact checks use synthetic, unseen examples."""
import unittest
from evaluation.split_integrity import leakage_groups,verify_split


def candidate(identity,document,version,head,relation,tail,start=0,end=30):
    return {'candidate_id':identity,'revision':1,'document_id':document,'document_version':version,
        'triple':{'head':head,'surface_relation':relation,'tail':tail},
        'evidence_items':[{'offset_basis':'canonical_document','start':start,'end':end}]}


class SplitIntegrityTests(unittest.TestCase):
    def verify(self,a,b):
        return verify_split([a,b],{(a['candidate_id'],1):'development',(b['candidate_id'],1):'independent_test'})

    def test_disjoint_facts_from_one_source_document_cannot_cross_partitions(self):
        a=candidate('a','manual','v1','pump','moves','water')
        b=candidate('b','manual','v2','valve','controls','flow',100,150)
        with self.assertRaises(ValueError):self.verify(a,b)

    def test_missing_document_identity_does_not_make_unrelated_offsets_overlap(self):
        a=candidate('a',None,None,'pump','moves','water')
        b=candidate('b',None,None,'valve','controls','flow')
        self.assertEqual(len(leakage_groups([a,b])),2)
        with self.assertRaises(ValueError):self.verify(a,b)

    def test_renaming_a_document_does_not_make_its_text_independent(self):
        a=candidate('a','manual','same-text-hash','pump','moves','water')
        b=candidate('b','renamed-manual','same-text-hash','valve','controls','flow',100,150)
        with self.assertRaises(ValueError):self.verify(a,b)

    def test_case_and_spacing_cannot_hide_duplicate_facts_in_another_source(self):
        a=candidate('a','manual','v1','Motor','drives','rotor')
        b=candidate('b','report','v3',' motor ',' DRIVES ','ROTOR')
        with self.assertRaises(ValueError):self.verify(a,b)

    def test_independent_sources_with_distinct_facts_are_allowed(self):
        a=candidate('a','manual','v1','pump','moves','water')
        b=candidate('b','report','v3','valve','controls','flow')
        self.assertEqual(len(self.verify(a,b)['groups']),2)
