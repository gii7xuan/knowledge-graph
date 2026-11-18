"""Checks for the source-bound shared extension candidate and its repair cohort."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from evaluation.freeze_shared_ontology import ROOT, read
from evaluation.shared_expression_repairs import changes_for
from evaluation.shared_extension_model import identity, PROPERTY_CASES
from evaluation.shared_extension_version import verify_candidate_bundle
from evaluation.shared_coverage_review import file_hash
from review_agent.contracts import sha256_json
from review_agent.revision_patch import merge_triple_patch, expression_diagnostics

OUT=ROOT/'outputs/shared_extension_20261118'


class SharedCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (OUT/'verification.json').exists():
            raise unittest.SkipTest('version-bound candidate artifact not prepared in this checkout')
        cls.samples={int(r['sample_id'][-3:]):r['candidate'] for r in read(ROOT/'outputs/development_pool_20261117/pilot/review_samples.json')['samples']}
        cls.drafts={int(r['sample_id'][-3:]):r for r in read(OUT/'expression_drafts.json')}
        cls.bundle=OUT/'ontology_candidate'

    def updated(self,n):
        return merge_triple_patch(self.samples[n]['triple'],changes_for(n,self.samples[n]),strict=True)

    def test_minimal_covolume_repair_keeps_referent_and_negation(self):
        before=self.samples[1]['triple'];after=self.updated(1)
        for field in ('head','surface_relation','tail','qualifiers'):
            self.assertEqual(after[field],before[field])
        self.assertEqual(after['qualifiers']['polarity'],'negative')
        self.assertEqual(after['semantic_context']['origin_mentions'],before['semantic_context']['origin_mentions'])

    def test_removed_time_quantity_and_reason_roles_do_not_survive_as_stale_attachments(self):
        a=self.updated(2)
        self.assertIsNone(a['qualifiers']['time_scope'])
        self.assertFalse(any(x['qualifier_kind']=='time' and 'earlier sections' in x['value'] for x in a['semantic_context']['qualifier_attachments']))
        a=self.updated(23)
        self.assertIsNone(a['qualifiers']['quantifier'])
        self.assertFalse(any(x['qualifier_kind']=='quantity' and x['value']=='simply' for x in a['semantic_context']['qualifier_attachments']))
        a=self.updated(36)
        self.assertIsNone(a['qualifiers']['condition'])
        self.assertFalse(any(x['qualifier_kind']=='condition' for x in a['semantic_context']['qualifier_attachments']))

    def test_normative_and_tendency_meanings_are_not_normalized_into_asserted(self):
        for n in (23,31,36):
            after=self.updated(n)
            self.assertEqual(after['qualifiers']['modality'],'unknown')
            self.assertEqual(after['qualifier_status']['modality'],'unknown')
            self.assertFalse(expression_diagnostics({'contract_version':'extraction_candidate_v4','triple':after})['expression_complete'])
        after=self.updated(36)
        self.assertEqual(after['surface_relation'],'would require')
        self.assertEqual(after['tail'],self.samples[36]['triple']['tail'])
        self.assertIn("δ''",after['tail'])

    def test_conjunction_and_comparison_do_not_become_distributed_causal_or_universal_claims(self):
        self.assertIn('且（流体为正压性的，或流体不可压缩）',self.updated(27)['qualifiers']['condition'])
        self.assertEqual(self.updated(24)['qualifiers']['quantifier'],'one inlet and one outlet')
        self.assertEqual(self.updated(15)['tail_kind'],'proposition')
        self.assertEqual(self.updated(15)['tail'],self.samples[15]['extraction_quote'])
        self.assertNotIn('cause',self.updated(15)['surface_relation'])

    def test_old_schema_objects_remain_identical_and_ambiguous_classes_are_not_merged(self):
        from ontologyBuild.release_service import load_release
        from ontologyBuild.ontology_adapter import iter_classes,iter_object_properties,iter_datatype_properties
        from ontologyBuild.rdf_graph import parse_turtle
        from rdflib import OWL,RDFS,URIRef
        base=load_release(read(OUT/'protocol.json')['base_release']['ontology_release_path'])
        current=read(self.bundle/'ontology_snapshot.json')
        for iterator in (iter_classes,iter_object_properties,iter_datatype_properties):
            before={r['id']:r for r in iterator(base.ontology)};after={r['id']:r for r in iterator(current)}
            self.assertTrue(all(after[k]==v for k,v in before.items()))
        graph=parse_turtle((self.bundle/'ontology.ttl').read_text(encoding='utf-8'))
        node=URIRef(identity('StateEquation')[0])
        self.assertEqual(len(list(graph.objects(node,RDFS.seeAlso))),2)
        self.assertFalse(list(graph.objects(node,OWL.equivalentClass)))
        self.assertFalse(list(graph.objects(node,OWL.sameAs)))
        self.assertNotIn(identity('StateEquation')[1],{'C_eaa767de9c851cd73be7','C_b944bb2feba8240d5c86'})

    def test_property_proof_cases_are_specific_to_each_role(self):
        delta=read(self.bundle/'schema_delta.json')
        props={p['key']:p for p in delta['new_properties']}
        for key,expected in PROPERTY_CASES.items():
            self.assertEqual(props[key]['source_sample_ids'],expected)
        self.assertNotIn('causesInContext',props)
        self.assertEqual(props['hasCauseInContext']['domain_key'],'Quantity')
        self.assertEqual(props['hasCauseInContext']['range_key'],'Event')
        proofs=read(self.bundle/'model_source_proofs.json')['nodes']
        self.assertEqual({p['schema_id'] for p in proofs},{p['id'] for p in [*delta['new_classes'],*delta['new_properties']]})
        self.assertTrue(all(p['source_proofs'] and p['semantic_approval']=='pending' for p in proofs))

    def test_drafts_are_new_evidence_bound_not_applied_or_voted(self):
        import sqlite3
        self.assertEqual(len(self.drafts),21)
        for n,row in self.drafts.items():
            draft=row['draft']
            self.assertEqual(draft['status'],'proposed')
            self.assertIsNone(draft['after']['triple']['relation_id'])
            self.assertNotEqual(row['reanchored_candidate']['run_id'],self.samples[n]['run_id'])
            self.assertEqual(set(draft['protected_evidence_ids']),{x['evidence_id'] for x in draft['evidence_bundle']['items']})
        with sqlite3.connect((OUT/'review.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM request_attempts').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM revision_votes').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT MAX(current_revision) FROM candidates').fetchone()[0],1)
        self.assertTrue(self.drafts[11]['pdf_visual_review_required'])
        self.assertTrue(self.drafts[11]['pdf_sidecar_is_not_registered_text_evidence'])

    def test_bundle_roundtrip_and_approval_boundary(self):
        from ontologyBuild.release_service import load_release
        manifest,check=verify_candidate_bundle(self.bundle)
        self.assertTrue(check['valid'])
        self.assertFalse(manifest['eligible_for_formal_experiment'])
        with self.assertRaises((ValueError,FileNotFoundError)):
            load_release(self.bundle)

    def test_artifact_tampering_and_hash_consistent_fake_approval_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'candidate';shutil.copytree(self.bundle,target)
            source=target/'ontology.ttl';source.write_text(source.read_text(encoding='utf-8')+'\n# changed\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'artifact changed'):
                verify_candidate_bundle(target)
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'candidate';shutil.copytree(self.bundle,target)
            approval=read(target/'approval.json');approval['status']='approved'
            (target/'approval.json').write_text(json.dumps(approval),encoding='utf-8')
            m=read(target/'candidate_manifest.json');m['artifacts']['approval.json']=file_hash(target/'approval.json')
            m['candidate_id']='ontology_candidate_'+sha256_json({k:v for k,v in m.items() if k!='candidate_id'})
            (target/'candidate_manifest.json').write_text(json.dumps(m),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'approval status'):
                verify_candidate_bundle(target)


if __name__=='__main__':unittest.main()
