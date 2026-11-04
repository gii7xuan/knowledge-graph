"""A changed semantic normalization needs its own draft; metadata cannot reroll votes."""
from copy import deepcopy
from unittest.mock import patch
import unittest
from review_agent import admission
from review_agent.repository import ConflictError
from tests import test_review_recovery_v4 as helpers


class RevisionNormalizationBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.RevisionAndConsumerTests(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def test_resolved_relation_change_creates_a_new_unreviewed_draft_without_reusing_votes(self):
        f=self.fixture;first=f.draft();f.approve(first);original=admission.canonicalize_revision
        old_votes=deepcopy(f.service.draft(first['draft_id'])['review_result']['votes'])
        calls=[len(v['client'].calls) for v in f.fixture.voters]
        def resolve(repo,candidate):
            result=original(repo,candidate);result['triple']['relation_id']='R_old';return result
        with patch.object(admission,'canonicalize_revision',side_effect=resolve):
            second=f.draft()
            with self.assertRaises(ConflictError):f.service.apply(first['draft_id'],first['draft_hash'])
        self.assertNotEqual(first['draft_id'],second['draft_id']);self.assertEqual(second['status'],'proposed')
        self.assertIn('relation_id',second['changed_fields'])
        self.assertEqual(second['ontology_context']['relevant_properties'][0]['id'],'R_old')
        self.assertEqual(f.service.draft(second['draft_id'])['review_result'],{})
        self.assertEqual([len(v['client'].calls) for v in f.fixture.voters],calls)
        self.assertEqual(f.service.draft(first['draft_id'])['review_result']['votes'],old_votes)
        self.assertEqual(f.repo.get_candidate(first['candidate_id'])['revision'],1)

    def test_derived_metadata_change_does_not_create_a_new_paid_review(self):
        f=self.fixture;first=f.draft();f.approve(first);original=admission.canonicalize_revision
        def metadata(repo,candidate):
            result=original(repo,candidate);result['triple']['mapping_status']='derived-metadata-update';return result
        with patch.object(admission,'canonicalize_revision',side_effect=metadata):second=f.draft()
        self.assertEqual(second['draft_id'],first['draft_id'])
        self.assertEqual(second['status'],'approved')

    def test_type_normalization_cannot_change_under_an_old_approved_draft(self):
        f=self.fixture;first=f.draft();f.approve(first);original=admission.canonicalize_revision
        def changed_type(repo,candidate):
            result=original(repo,candidate);result['triple']['head_type']=None;return result
        with patch.object(admission,'canonicalize_revision',side_effect=changed_type):
            with self.assertRaises(ConflictError):f.service.apply(first['draft_id'],first['draft_hash'])
            second=f.draft()
        self.assertNotEqual(first['draft_id'],second['draft_id'])
        self.assertEqual(f.repo.get_candidate(first['candidate_id'])['revision'],1)
