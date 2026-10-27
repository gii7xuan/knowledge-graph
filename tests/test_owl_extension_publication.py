"""Full RDF preservation, exact source/view publication, and OWL extension flow."""
import copy
import hashlib
import json
import os
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from rdflib import Graph,BNode,Literal,RDF,RDFS,OWL,SKOS,URIRef,XSD
from rdflib.compare import isomorphic

from ontologyBuild.rdf_graph import parse_turtle
from ontologyBuild.rdf_artifact import validate_owl_view
from ontologyBuild.providers import NeonImportProvider,OntologyImportRequest
from ontologyBuild.release_service import load_release,publish_release,validation_report
from ontologyBuild.release_catalog import ReleaseCatalog
from ontology_review.delta import apply_delta,check_structure,catalog
from ontology_review.owl_delta import compile_owl_delta,RETIRED_IRI
from ontology_review.publisher import OntologyPublisher,current_release_id

ROOT=Path(__file__).resolve().parents[1]
EX='https://example.org/owl-extension/'
SIMPLE='''@prefix ex: <https://example.org/owl-extension/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:Device a owl:Class; rdfs:label "device"@en, "设备"@zh; skos:altLabel "C";
 skos:definition "A controllable device."@en,"可控设备。"@zh; rdfs:comment "Keep provenance comment."@en .
ex:Peer a owl:Class; rdfs:label "peer device"@en; skos:definition "A peer controllable device."@en .
ex:Child a owl:Class; rdfs:label "child device"@en; rdfs:subClassOf ex:Device .
ex:connects a owl:ObjectProperty; rdfs:label "connects"@en; skos:definition "Connection between devices."@en; rdfs:domain ex:Device; rdfs:range ex:Device .
ex:links a owl:ObjectProperty; rdfs:label "links"@en; skos:definition "A second connection label."@en; rdfs:domain ex:Device; rdfs:range ex:Device .
ex:count a owl:DatatypeProperty; rdfs:label "count"@en; skos:definition "A device count."@en; rdfs:domain ex:Device; rdfs:range xsd:integer .
ex:example a ex:Peer; ex:count "001"^^xsd:integer; ex:links ex:other .
'''
RICH='''
ex:ontology a owl:Ontology; owl:imports <https://never-fetch.invalid/ontology> .
ex:Device rdfs:subClassOf [a owl:Restriction; owl:onProperty ex:connects; owl:someValuesFrom ex:Peer] .
ex:Device owl:equivalentClass [owl:unionOf (ex:Device ex:Peer)] .
ex:connects owl:propertyChainAxiom (ex:links ex:connects) .
[] a owl:Axiom; owl:annotatedSource ex:connects; owl:annotatedProperty rdfs:domain; owl:annotatedTarget ex:Device; rdfs:comment "Domain evidence."@en .
ex:note a owl:AnnotationProperty .
ex:example ex:note 001.00, +1E02, -0.0 .
'''


def anonymous_graph(graph):
    result=Graph()
    for triple in graph:
        if any(isinstance(term,BNode) for term in triple): result.add(triple)
    return result


class OwlDeltaTests(unittest.TestCase):
    def setUp(self):
        self.folder=ROOT/'runs/_test_owl_extension'/uuid4().hex; self.folder.mkdir(parents=True)
        self.addCleanup(shutil.rmtree,self.folder)
        self.source=self.folder/'base.ttl'; self.source.write_text(SIMPLE+RICH,encoding='utf-8')
        self.artifact=NeonImportProvider().build_or_import(OntologyImportRequest(self.source))
        self.ids=self.artifact.iri_id_map
        self.base=ReleaseCatalog(self.folder/'releases').publish(self.artifact,approval={'status':'approved','approved_by':'fixture','reason':'OWL preservation fixture'})

    def op(self,kind,target=None,**changes):
        return {'kind':kind,'target':self.ids[EX+target] if target else None,'changes':changes,'evidence_ids':['source_definition']}

    def compile(self,operations):
        delta=apply_delta(self.base,operations,{'source_definition'})
        compiled=compile_owl_delta(self.base,delta['operations'])
        self.assertEqual(delta['merged_schema'],compiled['project_schema'])
        self.assertEqual(delta['owl_patch'],compiled['patch'])
        return delta,compiled,parse_turtle(compiled['source_ttl'])

    def test_additive_patch_preserves_anonymous_axioms_annotations_imports_and_literal_terms(self):
        original=(self.base.path/'ontology.ttl').read_bytes()
        op=self.op('add_object_property',label='regulates',definition='Changes another device operating state.',domain=[self.ids[EX+'Device']],range=[self.ids[EX+'Device']])
        delta,compiled,graph=self.compile([op])
        before=parse_turtle(original.decode())
        self.assertTrue(all(triple in graph for triple in before if not any(isinstance(t,BNode) for t in triple)))
        self.assertTrue(isomorphic(anonymous_graph(before),anonymous_graph(graph)))
        self.assertIn((URIRef(EX+'example'),URIRef(EX+'count'),Literal('001',datatype=XSD.integer,normalize=False)),graph)
        for lexical,datatype in (('001.00',XSD.decimal),('+1E02',XSD.double),('-0.0',XSD.decimal)):
            self.assertIn((URIRef(EX+'example'),URIRef(EX+'note'),Literal(lexical,datatype=datatype,normalize=False)),graph)
        self.assertEqual((self.base.path/'ontology.ttl').read_bytes(),original)
        self.assertEqual(compiled['patch']['removed'],[])
        self.assertTrue(compiled['application_profile']['auto_review_blocked'])
        identity=delta['operations'][0]['target']
        self.assertIn(identity,compiled['application_profile']['blocked_relation_ids'])
        self.assertEqual(compiled['iri_id_map'][compiled['project_schema']['object_properties'][identity]['iri']],identity)
        self.assertEqual(compiled['source_ttl'],compile_owl_delta(self.base,delta['operations'])['source_ttl'])
        from ontologyBuild.owl_to_project_schema import convert_turtle
        self.assertEqual(convert_turtle(compiled['source_ttl'])['iri_id_map'],compiled['iri_id_map'])

    def test_add_class_data_property_and_alias_round_trip_with_original_ids(self):
        operations=[self.op('add_class',label='controller',definition='A kind of device.',parents=[self.ids[EX+'Device']],label_language='en'),
            self.op('add_data_property',label='mass',definition='A decimal mass.',domain=['controller'],datatype='decimal'),
            self.op('add_alias','connects',aliases=['相连'],alias_language='zh')]
        delta,compiled,graph=self.compile(operations)
        rows=catalog(compiled['project_schema'])
        new_class=delta['operations'][0]['target']; new_data=delta['operations'][1]['target']
        self.assertEqual(rows[new_data]['domain'],[new_class])
        self.assertEqual(rows[new_data]['datatype'],str(XSD.decimal))
        self.assertIn((URIRef(EX+'connects'),SKOS.altLabel,Literal('相连',lang='zh')),graph)
        for old_iri,old_id in self.ids.items(): self.assertEqual(compiled['iri_id_map'][old_iri],old_id)
        validate_owl_view(compiled['source_ttl'],compiled['project_schema'],compiled['application_profile'],compiled['iri_id_map'],compiled['mapping_report'])

    def test_definition_revision_requires_language_and_preserves_other_languages_and_comments(self):
        with self.assertRaisesRegex(ValueError,'definition_language'):
            apply_delta(self.base,[self.op('revise_definition','Device',definition='New definition.')],{'source_definition'})
        _,_,graph=self.compile([self.op('revise_definition','Device',definition='Updated controllable device.',definition_language='en')])
        self.assertIn((URIRef(EX+'Device'),SKOS.definition,Literal('可控设备。',lang='zh')),graph)
        self.assertIn((URIRef(EX+'Device'),RDFS.comment,Literal('Keep provenance comment.',lang='en')),graph)
        self.assertNotIn((URIRef(EX+'Device'),SKOS.definition,Literal('A controllable device.',lang='en')),graph)
        self.assertIn((URIRef(EX+'Device'),SKOS.definition,Literal('Updated controllable device.',lang='en')),graph)

    def test_named_constraint_revisions_keep_anonymous_restrictions_and_all_semantics(self):
        delta,compiled,graph=self.compile([self.op('revise_constraint','connects',domain=[self.ids[EX+'Device'],self.ids[EX+'Child']]),
            self.op('revise_constraint','Device',parents=[self.ids[EX+'Peer']]),self.op('revise_constraint','count',datatype='decimal')])
        before=parse_turtle(self.artifact.source_ttl)
        self.assertTrue(isomorphic(anonymous_graph(before),anonymous_graph(graph)))
        relation=catalog(compiled['project_schema'])[self.ids[EX+'connects']]
        self.assertEqual(set(relation['domain']),{self.ids[EX+'Device'],self.ids[EX+'Child']})
        self.assertEqual(relation['domain_semantics'],'all')
        self.assertEqual(compiled['project_schema']['classes'][self.ids[EX+'Device']]['parents'],[self.ids[EX+'Peer']])
        self.assertIn((URIRef(EX+'count'),RDFS.range,XSD.decimal),graph)

    def test_merge_rewrites_reviewed_iri_through_anonymous_axioms_and_abox_without_dropping_constraints(self):
        _,compiled,graph=self.compile([self.op('merge_terms','Peer',into=self.ids[EX+'Device'])])
        before=parse_turtle(self.artifact.source_ttl); transformed=Graph()
        for triple in anonymous_graph(before): transformed.add(tuple(URIRef(EX+'Device') if term==URIRef(EX+'Peer') else term for term in triple))
        self.assertTrue(isomorphic(transformed,anonymous_graph(graph)))
        self.assertIn((URIRef(EX+'example'),RDF.type,URIRef(EX+'Device')),graph)
        self.assertIn((URIRef(EX+'Device'),RETIRED_IRI,URIRef(EX+'Peer')),graph)
        self.assertEqual(compiled['project_schema']['retired_schema_ids'][self.ids[EX+'Peer']],self.ids[EX+'Device'])
        self.assertNotIn(EX+'Peer',compiled['iri_id_map'])
        self.assertIn((URIRef(EX+'Device'),SKOS.altLabel,Literal('peer device',lang='en')),graph)

    def test_property_merge_and_reimport_preserve_standard_retired_identity(self):
        _,compiled,graph=self.compile([self.op('merge_terms','links',into=self.ids[EX+'connects'])])
        self.assertIn((URIRef(EX+'example'),URIRef(EX+'connects'),URIRef(EX+'other')),graph)
        self.assertEqual(compiled['project_schema']['retired_schema_ids'][self.ids[EX+'links']],self.ids[EX+'connects'])
        self.assertTrue(compiled['patch']['removed'])

    def test_no_arbitrary_rdf_delete_or_mutation_of_retired_target(self):
        with self.assertRaises(ValueError): apply_delta(self.base,[self.op('delete_axiom','Device',pattern='*')],{'source_definition'})
        with self.assertRaisesRegex(ValueError,'retired'):
            apply_delta(self.base,[self.op('merge_terms','Peer',into=self.ids[EX+'Device']),self.op('add_alias','Peer',aliases=['retired edit'])],{'source_definition'})

    def test_source_view_or_profile_mismatch_cannot_publish(self):
        artifact=copy.deepcopy(self.artifact)
        artifact.project_schema['classes'][self.ids[EX+'Device']]['description']='View-only rewrite'
        self.assertFalse(validation_report(artifact)['project_validation']['valid'])
        artifact=copy.deepcopy(self.artifact); artifact.application_profile['auto_review_blocked']=False
        artifact.project_schema['application_profile']=artifact.application_profile
        self.assertFalse(validation_report(artifact)['project_validation']['valid'])

    def test_multiline_literals_and_bad_language_or_datatype_are_handled_explicitly(self):
        from ontologyBuild.rdf_artifact import canonical_turtle
        graph=parse_turtle(SIMPLE+'\nex:Device rdfs:comment """first line\nsecond line"""@en .')
        serialized=canonical_turtle(graph)
        self.assertIn('first line\\nsecond line',serialized)
        self.assertTrue(isomorphic(graph,parse_turtle(serialized)))
        with self.assertRaises(ValueError):
            self.compile([self.op('add_alias','connects',aliases=['alias'],alias_language='bad_tag')])
        with self.assertRaises(ValueError): self.compile([self.op('revise_constraint','count',datatype=[])])

    def test_complete_rdf_context_and_blank_node_delta_replay_are_verified(self):
        from ontologyBuild.rdf_artifact import validate_patch_bindings
        from review_agent.contracts import sha256_json
        _,compiled,_=self.compile([self.op('merge_terms','Peer',into=self.ids[EX+'Device'])])
        proof=compiled['patch']
        method={key:proof[key] for key in ('compiler_version','compiler_hash','base_source_sha256','source_sha256')}
        method.update(owl_patch_hash=proof['patch_hash'],source_graph_hash=proof['graph_hash'],delta_hash=sha256_json(proof['normalized_operations']))
        validate_patch_bindings(proof,compiled['source_ttl'],compiled['iri_id_map'],method)
        self.assertTrue(isomorphic(parse_turtle(proof['base_rdf']),parse_turtle(self.artifact.source_ttl)))
        altered=copy.deepcopy(proof); altered['removed']=[]
        altered['patch_hash']=sha256_json({k:v for k,v in altered.items() if k!='patch_hash'})
        with self.assertRaisesRegex(ValueError,'recorded RDF delta'):
            validate_patch_bindings(altered,compiled['source_ttl'],compiled['iri_id_map'],{**method,'owl_patch_hash':altered['patch_hash']})

    def test_relative_iris_use_recorded_source_base_and_no_import_network_calls(self):
        source=self.folder/'relative.ttl'
        source.write_text('@prefix owl: <http://www.w3.org/2002/07/owl#> . <Device> a owl:Class . <root> a owl:Ontology; owl:imports <https://never-fetch.invalid/ontology> .',encoding='utf-8')
        with patch('urllib.request.urlopen',side_effect=AssertionError('network not permitted')):
            artifact=NeonImportProvider().build_or_import(OntologyImportRequest(source))
        release=publish_release(artifact,self.folder/'relative_releases',approval={'status':'approved','approved_by':'fixture','reason':'Relative IRI fixture'})
        with patch('os.getcwd',return_value=str(self.folder/'different_working_directory')):
            self.assertEqual(load_release(release.path).ontology,release.ontology)


def owl_fixture(test,rich=False):
    from tests.test_ontology_extension_agent import OntologyExtensionTests
    class Fixture(OntologyExtensionTests):
        def artifact(self,schema):
            source=SIMPLE+(RICH if rich else '')
            path=self.folder/'initial.ttl'; path.write_text(source,encoding='utf-8')
            return NeonImportProvider().build_or_import(OntologyImportRequest(path))
        def source_candidate(self,ordinal=0):
            if not getattr(self,'owl_document_ready',False):
                from document.hierarchy_index import build_document_index,split_markdown_text,persist_document_index
                self.index=build_document_index(split_markdown_text("Regulation is a device changing another device's operating state. A regulates B.",self.folder.name,600,20))
                path=persist_document_index(self.index,self.folder/'document.json')
                manifest=self.repo.get_run('run'); manifest.update(document_path=str(path),source_index_hash=self.index['index_content_hash'])
                with self.repo.transaction(immediate=True) as connection:
                    connection.execute('UPDATE runs SET manifest_json=? WHERE run_id=?',(json.dumps(manifest),'run'))
                self.owl_document_ready=True
            return super().source_candidate(ordinal)
    fixture=Fixture()
    if getattr(test,'fixture_output',None): fixture.output_folder=test.fixture_output
    fixture.setUp(); test.addCleanup(fixture.doCleanups)
    return fixture


class OwlPublicationFlowTests(unittest.TestCase):
    def test_raw_source_lookup_and_anonymous_neighborhood_are_available_to_planner(self):
        h=owl_fixture(self,rich=True); h.review()
        result=h.runtime.tools.search_existing_schema('Domain evidence.')
        self.assertGreater(result['rdf_statements_scanned'],result['items_scanned'])
        self.assertTrue(any('Domain evidence.' in row['statement'] for row in result['rdf_matches']))
        device=next(i for i,row in catalog(h.base.ontology).items() if row.get('iri')==EX+'Device')
        neighborhood=h.runtime.tools.inspect_schema_neighborhood(device)
        self.assertFalse(neighborhood['rdf_context_truncated'])
        self.assertIn('someValuesFrom',neighborhood['rdf_neighborhood'])
        self.assertIn('complete_base_and_result_rdf',h.audit['proposals'][0]['source_patch']['review_context'])

    def test_release_validation_rejects_resealed_wrong_patch_proof(self):
        from review_agent.contracts import sha256_json
        h=owl_fixture(self); h.review()
        artifact=OntologyPublisher(h.repo,h.root).artifact(h.audit['proposals'][0],h.base)
        artifact.owl_patch['graph_hash']='0'*64
        artifact.owl_patch['patch_hash']=sha256_json({k:v for k,v in artifact.owl_patch.items() if k!='patch_hash'})
        artifact.method_manifest.update(source_graph_hash=artifact.owl_patch['graph_hash'],owl_patch_hash=artifact.owl_patch['patch_hash'])
        self.assertFalse(validation_report(artifact)['project_validation']['valid'])
    def test_reasoner_input_contains_exact_owl_and_rejects_json_only_or_stale_proof(self):
        h=owl_fixture(self); h.review(); h.runtime.options['reasoner']={'command':['fixture-reasoner']}
        def execute(*args,**kwargs):
            payload=json.loads(kwargs['input'])
            self.assertIn('ontology_ttl',payload)
            self.assertEqual(hashlib.sha256(payload['ontology_ttl'].encode()).hexdigest(),payload['source_sha256'])
            binding={k:payload[k] for k in ('base_release_id','delta_hash','ontology_version','profile_hash','owl_patch_hash','source_sha256','source_graph_hash')}
            return SimpleNamespace(returncode=0,stdout=json.dumps({**binding,'status':'pass','consistent':True,'executed':True,'engine':'MOCK adapter, not an actual reasoner'}))
        with patch('subprocess.run',side_effect=execute):
            self.assertEqual(h.runtime.tools.run_reasoner_checks()['status'],'pass')
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps({'status':'pass','consistent':True,'executed':True,'engine':'unbound'}))):
            self.assertEqual(h.runtime.tools.run_reasoner_checks()['status'],'unknown')

    def test_full_owl_review_publish_rebind_fresh_review_and_human_admission(self):
        from tests.test_ontology_extension_agent import action
        from review_agent.orchestrator import ReviewOrchestrator
        from review_agent.ontology_guard import OntologyGuard,load_snapshot
        from review_agent.llm_clients import MockLLMClient
        from review_agent.feedback import FeedbackService
        h=owl_fixture(self); old=(h.base.path/'ontology.ttl').read_bytes(); h.review()
        proposal=h.audit['proposals'][0]
        self.assertIn('source_patch',proposal)
        publisher=OntologyPublisher(h.repo,h.root,h.config); request=h.publication_request()
        result=publisher.publish(h.extension_id,request)
        release=load_release(h.root/result['ontology_release_id'])
        self.assertIn('ontology.ttl',release.manifest['artifacts']); self.assertIn('owl_patch.json',release.manifest['artifacts'])
        self.assertNotIn('source_ontology.json',release.manifest['artifacts'])
        self.assertEqual((h.base.path/'ontology.ttl').read_bytes(),old)
        self.assertEqual(publisher.publish(h.extension_id,request),result)
        current=h.repo.get_candidate(h.candidate['candidate_id'])
        self.assertEqual(current['revision'],2); self.assertEqual(current['workflow_state'],'reaudit_required')
        def plan(payload):
            if not payload['ontology_checked']: return action('inspect_ontology',relation_id=payload['candidate']['relation_id'],issue='Actual new OWL release')
            if not payload['current_validation']: return action('run_validation')
            return action('finish_review',stop_reason='Fresh OWL-bound review completed',unresolved_issues=[],evidence_ids=[])
        audited=ReviewOrchestrator(h.repo,OntologyGuard(load_snapshot(release.snapshot_path)),h.source_voters,config=h.config,planner_client=MockLLMClient(plan)).audit_candidate(current['candidate_id'],2,h.index)
        self.assertEqual(audited['source_support_result'],'supported',audited)
        fresh=h.repo.get_audit(current['candidate_id'],audited['audit_id'])
        self.assertTrue(all(v['object_revision']==2 for v in fresh['votes']))
        self.assertNotEqual(fresh['rounds'][0]['proposal_digest'],h.source_result['selected_proposal_digest'])
        current=h.repo.get_candidate(current['candidate_id'])
        FeedbackService(h.repo).submit({'action':'accept','candidate_id':current['candidate_id'],'revision':2,'expected_row_version':current['row_version'],
            'idempotency_key':uuid4().hex,'spans':[{'quote':'A regulates B.','role':'support','note':'Current source statement under the new OWL regulation property.'}]})
        self.assertEqual(len(h.repo.current_admitted_contributions(current['candidate_id'])['desired']),1)
        self.flow=(h,release)

    def test_complex_source_stays_blocked_and_profile_diff_requires_confirmation(self):
        h=owl_fixture(self,rich=True); h.review(); publisher=OntologyPublisher(h.repo,h.root,h.config)
        preview=publisher.preview(h.extension_id)
        self.assertTrue(preview['requires_difference_confirmation'])
        with self.assertRaisesRegex(ValueError,'profile/mapping'): publisher.publish(h.extension_id,h.publication_request())
        result=publisher.publish(h.extension_id,{**h.publication_request(),'confirm_profile_differences':True})
        release=load_release(h.root/result['ontology_release_id'])
        self.assertTrue(release.application_profile['auto_review_blocked'])
        self.assertEqual(release.application_profile['reasoner'],'not_run')
        before=parse_turtle((h.base.path/'ontology.ttl').read_text(encoding='utf-8'))
        after=parse_turtle((release.path/'ontology.ttl').read_text(encoding='utf-8'))
        self.assertTrue(isomorphic(anonymous_graph(before),anonymous_graph(after)))

    def test_edited_source_patch_or_view_cannot_use_previous_ontology_votes(self):
        h=owl_fixture(self); h.review(); proposal=copy.deepcopy(h.audit['proposals'][0])
        proposal['source_patch']['added'].append('<urn:fake> <urn:fake> <urn:fake> .')
        with self.assertRaisesRegex(ValueError,'OWL source patch'): OntologyPublisher(h.repo,h.root).artifact(proposal,h.base)
        proposal=copy.deepcopy(h.audit['proposals'][0]); proposal['merged_schema']['classes'][next(iter(proposal['merged_schema']['classes']))]['label']='Different view'
        with self.assertRaises(ValueError): OntologyPublisher(h.repo,h.root).artifact(proposal,h.base)


@unittest.skipUnless(os.environ.get('KG_TEST_NEO4J_URI'),'isolated real Neo4j URI required')
class OwlGraphFlowTests(unittest.TestCase):
    def test_only_admitted_owl_candidate_reaches_graph_and_abox_stays_in_source(self):
        from to_neo4j.fact_projection import Neo4jImporter
        from to_neo4j.reconciler import apply_outbox
        from to_neo4j.query_facts import query_facts
        case=OwlPublicationFlowTests(); self.addCleanup(case.doCleanups)
        case.test_full_owl_review_publish_rebind_fresh_review_and_human_admission()
        h,release=case.flow
        importer=Neo4jImporter(os.environ['KG_TEST_NEO4J_URI'],user='',repository=h.repo); self.addCleanup(importer.close)
        result=apply_outbox(h.repo,importer)
        self.assertEqual(result['failed'],0,result)
        candidate=h.repo.get_candidate(h.candidate['candidate_id'])
        facts=query_facts(importer.driver,entity_id=candidate['triple']['head_entity_id'])
        self.assertEqual(len(facts),1)
        self.assertEqual(facts[0]['fact']['release_ids'],[release.manifest['release_id']])
        with importer.driver.session() as session:
            self.assertEqual(session.run('MATCH(e:Entity) WHERE e.display_name=$example OR e.entity_key=$example RETURN count(e) AS n',example=EX+'example').single()['n'],0)
