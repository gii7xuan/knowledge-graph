from copy import deepcopy
import unittest

from extract_triplets.qualifier_wire import project,policy,validate
from extract_triplets.candidate_contract import normalize_candidate
from evaluation.preannotation_followup import wire_plan,triage
from review_agent.contracts import sha256_json


class QualifierWireTests(unittest.TestCase):
    def raw(self):
        return {'head':'A','surface_relation':'can represent','tail':'B','evidence_quotes':['A can represent B.'],
            'qualifiers':{'polarity':'positive','modality':'can','condition':'not_stated','time_scope':None},
            'qualifier_status':{'polarity':'extracted','modality':'extracted','condition':'not_stated','time_scope':'unknown'}}

    def test_only_explicit_absence_pair_changes_and_source_judgments_are_immutable(self):
        raw=self.raw();before=deepcopy(raw);after,trace=project(raw,policy())
        self.assertEqual(raw,before);self.assertIsNone(after['qualifiers']['condition'])
        self.assertEqual(after['qualifier_status'],raw['qualifier_status'])
        self.assertEqual(after['qualifiers']['modality'],'can')
        self.assertEqual(after['evidence_quotes'],raw['evidence_quotes'])
        self.assertEqual(validate(raw,after,trace),[])

    def test_uncertainty_real_text_and_invalid_modality_are_not_reinterpreted(self):
        raw=self.raw();raw['qualifiers'].update(condition='if stated',time_scope='not_stated',modality='would')
        raw['qualifier_status']['modality']='extracted'
        after,trace=project(raw,policy())
        self.assertEqual(after,raw);self.assertIsNone(trace)

    def test_tampering_or_unfrozen_policy_is_rejected(self):
        raw=self.raw();after,trace=project(raw,policy());after['qualifiers']['modality']='asserted'
        self.assertTrue(validate(raw,after,trace))
        with self.assertRaises(ValueError):project(raw,{'version':'unknown'})

    def candidate(self,raw,wire=False):
        from document.snapshot import make_snapshot
        from document.hierarchy_index import split_markdown_text,build_document_index
        from extract_triplets.pipeline_v4 import source_scope
        text='A can represent B.';snap=make_snapshot(text,'doc')
        index=build_document_index(split_markdown_text(text,document_id='doc',snapshot=snap))
        return normalize_candidate(raw,index=index,scope=source_scope(index,index['chunks'][0]),ontology={},
            release_binding={'ontology_version':'test'},run_id='test-run',ordinal=0,prompt_hash='test',
            **({'qualifier_wire_policy':policy()} if wire else {}))

    def test_legacy_normalization_is_preserved_and_new_opt_in_records_proof(self):
        raw=self.raw();old=self.candidate(raw);new=self.candidate(raw,wire=True)
        self.assertEqual(old['triple']['qualifiers']['condition'],'not_stated')
        self.assertEqual(old['triple']['qualifier_status']['condition'],'unknown')
        self.assertIsNone(new['triple']['qualifiers']['condition'])
        self.assertEqual(new['triple']['qualifier_status']['condition'],'not_stated')
        self.assertNotIn('extraction_format_normalization',old)
        self.assertEqual(new['extraction_format_normalization']['original_raw_hash'],sha256_json(raw))
        self.assertEqual(new['triple']['qualifiers']['modality'],'can')

    def test_wire_plan_never_copies_ai_revisions_or_changes_other_fields(self):
        raw=self.raw();candidate=self.candidate(raw);plan=wire_plan(candidate,raw)
        self.assertEqual(plan['changes'],{'qualifiers':{'condition':None},'qualifier_status':{'condition':'not_stated'}})
        self.assertFalse(plan['ai_labels_used_to_choose_values']);self.assertFalse(plan['automatically_applied'])
        candidate['triple']['qualifiers']['modality']='asserted'
        with self.assertRaises(ValueError):wire_plan(candidate,raw)


class PreannotationTriageTests(unittest.TestCase):
    def test_include_with_unassigned_mapping_never_produces_accept_or_gold(self):
        row={'raw_annotation':{'labels':{'source_support':'supported','expression_status':'faithful',
            'mapping_status':'unassigned','schema_coverage':'not_covered','knowledge_scope':'general',
            'admission_preference':'include'},'supporting_evidence_ids':['e'],'duration_seconds':None}}
        result=triage(row,{'sample_id':'PILOT-003','binding':{}})
        self.assertIn('include_preference_is_not_admission',result['questions'])
        self.assertIsNone(result['automatic_research_operation']);self.assertFalse(result['gold_created'])


if __name__=='__main__':unittest.main()
