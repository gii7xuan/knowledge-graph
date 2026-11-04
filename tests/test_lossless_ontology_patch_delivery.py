from copy import deepcopy
import unittest
from ontologyBuild.rdf_graph import parse_turtle
from ontologyBuild.rdf_artifact import graph_hash
from ontology_review.review_payload import validator_payload


class LosslessPatchDeliveryTests(unittest.TestCase):
    def proposal(self):
        before='@prefix ex: <urn:test:> . @prefix owl: <http://www.w3.org/2002/07/owl#> . ex:C a owl:Class ; ex:restriction [ ex:value "01"^^<http://www.w3.org/2001/XMLSchema#integer> ] ; ex:label "原文"@zh .'
        added='<urn:test:R> a <http://www.w3.org/2002/07/owl#ObjectProperty> .'
        merged=before+'\n'+added
        schema={'classes':{'C':{'id':'C','label':'concept'}},'object_properties':{}}
        return {'versions':{'payload_version':'ontology_validator_payload_v2'},'base_schema':schema,'merged_schema':deepcopy(schema),
            'source_patch':{'base_rdf':before,'merged_rdf':merged,'added':[added],'removed':[],
                'base_graph_hash':graph_hash(parse_turtle(before)),'graph_hash':graph_hash(parse_turtle(merged))}}

    def test_named_delta_preserves_the_entire_graph_and_frozen_input(self):
        proposal=self.proposal();frozen=deepcopy(proposal)
        payload=validator_payload(proposal,delivery_version='ontology_validator_payload_v3');patch=payload['source_patch']
        rebuilt=parse_turtle(patch['base_rdf'])-parse_turtle('\n'.join(patch['removed']))+parse_turtle('\n'.join(patch['added']))
        self.assertEqual(graph_hash(rebuilt),proposal['source_patch']['graph_hash'])
        self.assertIsInstance(patch['merged_rdf'],dict);self.assertEqual(proposal,frozen)
        self.assertIn('原文',patch['base_rdf']);self.assertIn('01',patch['base_rdf'])

    def test_corrupted_patch_does_not_silently_fall_back_or_drop_axioms(self):
        proposal=self.proposal();proposal['source_patch']['added']=[]
        with self.assertRaisesRegex(ValueError,'does not reproduce'):validator_payload(proposal,delivery_version='ontology_validator_payload_v3')

    def test_anonymous_patch_keeps_both_complete_graphs(self):
        proposal=self.proposal();proposal['source_patch']['added']=['<urn:test:R> <urn:test:p> _:changed .']
        payload=validator_payload(proposal,delivery_version='ontology_validator_payload_v3')
        self.assertIsInstance(payload['source_patch']['merged_rdf'],str)
        self.assertIn('anonymous_nodes',payload['source_patch']['patch_delivery_fallback'])

    def test_existing_frozen_deliveries_remain_unchanged(self):
        proposal=self.proposal()
        self.assertIsInstance(validator_payload(proposal)['source_patch']['merged_rdf'],str)

    def test_transport_change_does_not_bypass_an_unknown_semantic_request(self):
        from review_agent.request_identity import semantic_request_key
        voter={'model_id':'Gemma','model_family':'gemma','ontology_delivery_version':'ontology_validator_payload_v2'}
        original=semantic_request_key('ontology_review',voter,self.proposal(),'same-semantic-policy')
        voter['ontology_delivery_version']='ontology_validator_payload_v3'
        self.assertEqual(original,semantic_request_key('ontology_review',voter,self.proposal(),'same-semantic-policy'))
