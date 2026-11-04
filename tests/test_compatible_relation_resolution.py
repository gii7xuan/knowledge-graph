"""A unique label is insufficient evidence to force an incompatible formal mapping."""
from copy import deepcopy
import unittest
from review_agent.admission import canonicalize_revision
from tests import test_ontology_extension_agent as helpers


class CompatibleRelationResolutionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.OntologyExtensionTests(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.candidate=deepcopy(self.fixture.candidate);self.candidate['triple'].update(surface_relation='connects',relation_id=None)

    def test_unique_label_with_verified_compatible_types_can_resolve(self):
        result=canonicalize_revision(self.fixture.repo,self.candidate)
        self.assertEqual(result['triple']['relation_id'],'R_old')
        self.assertEqual(result['fact_key_state'],'canonical')

    def test_unknown_source_type_is_not_inferred_from_the_only_property_domain(self):
        self.candidate['triple']['head_type']=None
        result=canonicalize_revision(self.fixture.repo,self.candidate)
        self.assertIsNone(result['triple']['head_type']);self.assertIsNone(result['triple']['relation_id'])
        self.assertEqual(result['fact_key_state'],'provisional')

    def test_explicit_mapping_remains_visible_for_audit_instead_of_being_silently_replaced(self):
        self.candidate['triple'].update(relation_id='R_old',head_type=None)
        result=canonicalize_revision(self.fixture.repo,self.candidate)
        self.assertEqual(result['triple']['relation_id'],'R_old');self.assertIsNone(result['triple']['head_type'])

    def test_withdrawn_mapping_cannot_keep_a_previous_canonical_identity(self):
        self.candidate['fact_key_state']='canonical'
        self.candidate['triple']['head_type']=None
        result=canonicalize_revision(self.fixture.repo,self.candidate)
        self.assertIsNone(result['triple']['relation_id'])
        self.assertEqual(result['fact_key_state'],'provisional')
        self.assertTrue(result['fact_key'].startswith('provisional:'))
