import unittest
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import tempfile

from ontology_review.definition_audit import aggregate,binding,errors,judge_once,packets,passed
from review_agent.contracts import sha256_json


class DefinitionAuditTests(unittest.TestCase):
    def setUp(self):
        self.members=[{'id':f'v{i}','model_id':f'm{i}','model_identity':f'm{i}','model_family':f'f{i}'} for i in range(3)]
        self.proposal={'task_id':'audit','materials_hash':'frozen','items':[
            {'relation_id':'r1','label':'测试关系1','evidence':[{'evidence_id':'e1'}]},
            {'relation_id':'r2','label':'测试关系2','evidence':[{'evidence_id':'e2'}]}]}
        self.proposal['proposal_digest']=sha256_json(self.proposal)
        self.raw={'assessments':[{'relation_id':f'r{i}','definition_fidelity':'pass','direction':'pass',
            'endpoint_signature':'pass','task_scope':'in_scope','evidence_ids':[f'e{i}'],
            'issue_codes':[],'reason':'原文支持所述方向和端点。'} for i in (1,2)]}

    def votes(self):
        return [{**binding(m,self.proposal),**deepcopy(self.raw),'response_status':'ok','returned_model_id':m['model_id']} for m in self.members]

    def test_complete_independent_committee(self):
        rows=aggregate(self.proposal,self.votes(),self.members)
        self.assertTrue(all(r['status']=='machine_approved' and not r['human_confirmed'] for r in rows))

    def test_anchor_exists_but_semantic_error_quarantines(self):
        votes=self.votes();row=votes[1]['assessments'][0]
        row.update(definition_fidelity='revise',endpoint_signature='fail',issue_codes=['INTEGRAL_ENDPOINT_IS_NOT_PART'])
        result=aggregate(self.proposal,votes,self.members)
        self.assertEqual(['quarantined','machine_approved'],[r['status'] for r in result])
        self.assertEqual(['e1'],result[0]['assessments'][1]['evidence_ids'])

    def test_unknown_or_missing_never_approves(self):
        votes=self.votes();votes[2]['assessments'][1]['direction']='unknown'
        self.assertEqual('quarantined',aggregate(self.proposal,votes,self.members)[1]['status'])
        self.assertTrue(all(r['status']=='quarantined' for r in aggregate(self.proposal,votes[:2],self.members)))

    def test_wrong_binding_or_family_collision_blocks(self):
        votes=self.votes();votes[1]['materials_hash']='other'
        self.assertFalse(any(r['status']=='machine_approved' for r in aggregate(self.proposal,votes,self.members)))
        votes=self.votes();votes[1]['returned_model_id']='m0'
        self.assertFalse(any(r['status']=='machine_approved' for r in aggregate(self.proposal,votes,self.members)))

    def test_cross_item_citation_and_duplicate_rejected(self):
        raw=deepcopy(self.raw);raw['assessments'][0]['evidence_ids']=['e2']
        self.assertTrue(errors(raw,self.proposal))
        raw=deepcopy(self.raw);raw['assessments'][1]=deepcopy(raw['assessments'][0])
        self.assertTrue(errors(raw,self.proposal))

    def test_no_evidence_or_scope_pass(self):
        row=deepcopy(self.raw['assessments'][0]);row['evidence_ids']=[]
        self.assertFalse(passed(row))
        row=deepcopy(self.raw['assessments'][0]);row['task_scope']='unclear'
        self.assertFalse(passed(row))

    def test_tampered_packet_never_sent(self):
        class Client:
            def complete_json(self,*args,**kwargs):raise AssertionError('should never send')
        voter={**self.members[0],'client':Client()};proposal=deepcopy(self.proposal)
        proposal['items'][0]['relation_id']='tampered'
        self.assertTrue(judge_once(voter,proposal)['not_sent'])

    def test_transport_metadata_and_truncation_survive(self):
        from review_agent.llm_clients import MockLLMClient,LLMResponse
        voter={**self.members[0],'client':MockLLMClient([LLMResponse(self.raw,input_tokens=7,output_tokens=3,
            finish_reason='length',gateway_request_id='gateway',transport_metrics={'client_request_id':'local'})])}
        result=judge_once(voter,self.proposal)
        self.assertEqual('invalid_contract',result['response_status'])
        self.assertEqual('output_incomplete',result['error'])
        self.assertEqual('gateway',result['gateway_request_id']);self.assertEqual(7,result['input_tokens'])


class FreezeArtifactTests(unittest.TestCase):
    def test_unknown_original_without_late_receipt_cannot_be_resent(self):
        from unittest.mock import patch
        from evaluation.finalize_ontology_freeze import recover_last_batch
        from evaluation.freeze_shared_ontology import write
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)
            write(output/'definition_batch_09.json',{'proposal':{'committee_members':[{'id':'a'}]},'votes':[
                {'attempt_id':'old','voter_id':'a','outcome_unknown':True}]})
            fake=SimpleNamespace(request_attempt=lambda _: {'state':'unknown','result':{'outcome_unknown':True}})
            with patch('review_agent.repository.ReviewRepository',return_value=fake):
                with self.assertRaisesRegex(ValueError,'never resend'):recover_last_batch(output)
            self.assertFalse((output/'last_batch_recovery_started.json').exists())

    def test_source_blocker_can_only_quarantine_and_requires_own_quote(self):
        from evaluation.finalize_ontology_freeze import apply_blockers
        rows=[{'relation_id':'r','status':'machine_approved'},{'relation_id':'s','status':'quarantined'}]
        proposals=[{'items':[{'relation_id':'r','evidence':[{'evidence_id':'e','quote':'A is an integration bound.'}]}]}]
        blockers={'findings':[{'relation_id':'r','blocking':True,'reason':'Bound is not part-whole evidence.',
            'human_confirmed':False,'evidence_ids':['e'],'source_quotes':['A is an integration bound.']}]}
        final=apply_blockers(rows,proposals,blockers)
        self.assertEqual(['quarantined','quarantined'],[r['status'] for r in final])
        self.assertEqual('machine_approved',rows[0]['status'])
        blockers['findings'][0]['source_quotes']=['invented quote']
        with self.assertRaises(ValueError):apply_blockers(rows,proposals,blockers)

    def test_release_roundtrip_quarantine_identity_and_binding(self):
        from ontologyBuild.providers.base import OntologyArtifact
        from ontologyBuild.release_service import publish_release,load_release
        from ontologyBuild.ontology_adapter import iter_classes,iter_object_properties
        from ontologyBuild.owl_to_project_schema import convert_turtle
        from evaluation.freeze_shared_ontology import write,freeze
        ttl='''@prefix : <urn:fixture:> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        :A a owl:Class; rdfs:label "A" . :B a owl:Class; rdfs:label "B" .
        :kept a owl:ObjectProperty; rdfs:label "links"; rdfs:domain :A; rdfs:range :B .
        :removed a owl:ObjectProperty; rdfs:label "badlinks"; rdfs:domain :A; rdfs:range :B .'''
        converted=convert_turtle(ttl,base_iri='urn:fixture:')
        schema=converted['project_schema'];props=list(iter_object_properties(schema))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            artifact=OntologyArtifact(project_schema=schema,method_manifest={'source_base_iri':'urn:fixture:'},
                validation_report={},mapping_report=converted['mapping_report'],application_profile=converted['application_profile'],
                iri_id_map=converted['iri_id_map'],source_ttl=ttl)
            base=publish_release(artifact,root/'base',approval={'status':'approved','approved_by':'unit_test','reason':'synthetic fixture only'})
            before={p.name:p.read_bytes() for p in base.path.iterdir() if p.is_file()}
            rows=[{'relation_id':p['id'],'status':'machine_approved' if p['label']=='links' else 'quarantined'} for p in props]
            materials={'definitions':[{'relation_id':p['id'],'iri':p['iri'],'definition':'A links B.'} for p in props]}
            output=root/'output';output.mkdir()
            write(output/'definition_audit.json',{'rows':rows,'materials_hash':sha256_json(materials),'base_release_id':base.manifest['release_id']})
            release=freeze(output,base,materials,rows)
            loaded=load_release(release.path)
            self.assertEqual({p['id'] for p in props if p['label']=='links'},{p['id'] for p in iter_object_properties(loaded.ontology)})
            self.assertEqual({c['id']:c for c in iter_classes(schema)},{c['id']:c for c in iter_classes(loaded.ontology)})
            self.assertEqual(before,{p.name:p.read_bytes() for p in base.path.iterdir() if p.is_file()})
            with self.assertRaises(ValueError):freeze(output,base,materials,rows+[{'relation_id':'other','status':'machine_approved'}])
            (release.path/'ontology.ttl').write_text('tampered',encoding='utf-8')
            with self.assertRaises(ValueError):load_release(release.path)


class MappingAcceptanceTests(unittest.TestCase):
    def test_generic_abstention_does_not_count_as_semantic_detection(self):
        from evaluation.mapping_acceptance import acceptance
        votes=[{'voter_id':str(i),'model_family':str(i),'response_status':'ok','proposal_digest':'p',
                'dimensions':{'mapping':{'status':'unknown'},'relation_semantics':{'status':'unknown'}}} for i in range(3)]
        audit={'rounds':[{'proposal_digest':'p','mapping_preflight':{'judgment_ready':True}}],'votes':votes}
        result={'research_operation':'manual_review','review_outcome':{'dimension_results':{'mapping':{'status':'context_incomplete'}}}}
        self.assertFalse(acceptance('semantic_mismatch',result,audit,{})['passed'])
        for vote in votes[:2]:vote['dimensions']['relation_semantics']['status']='fail'
        self.assertTrue(acceptance('semantic_mismatch',result,audit,{})['passed'])

    def test_missing_material_requires_fresh_complete_committee(self):
        from evaluation.mapping_acceptance import acceptance
        votes=[{'voter_id':str(i),'model_family':str(i),'response_status':'ok','proposal_digest':'p','dimensions':{}} for i in range(3)]
        audit={'rounds':[{'proposal_digest':'p','mapping_preflight':{'judgment_ready':False}}],'votes':votes}
        result={'review_outcome':{'dimension_results':{'mapping':{'status':'context_incomplete'}}}}
        candidate={'triple':{'relation_id':None}}
        self.assertTrue(acceptance('missing_material',result,audit,candidate)['passed'])
        audit['votes']=votes[:2]
        self.assertFalse(acceptance('missing_material',result,audit,candidate)['passed'])


class RecordedMismatchTests(unittest.TestCase):
    def check_recorded(self,voter,expected):
        import json
        from review_agent.validator_contracts import contract_for
        fixture=json.loads((Path(__file__).parent/'fixtures/mapping_semantic_mismatch_20261116.json').read_text(encoding='utf-8'))
        raw=next(r['raw_response'] for r in fixture['responses'] if r['voter_id']==voter)
        before=sha256_json(raw)
        issues=contract_for(fixture['response_contract_version']).validate_response(raw)
        self.assertTrue(any(expected in e for e in issues));self.assertEqual(before,sha256_json(raw))

    def test_contradiction_without_counterevidence_remains_invalid(self):
        self.check_recorded('judge_a','contradicted requires counter_evidence_ids')

    def test_material_counterevidence_cannot_also_be_dismissed(self):
        self.check_recorded('judge_b','different roles')

    def test_mapping_cannot_be_an_expression_finding_check(self):
        self.check_recorded('judge_c','value outside enum')


if __name__=='__main__':unittest.main()
