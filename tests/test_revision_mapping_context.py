"""Mapping revisions carry real formal definitions and never create quote IDs for them."""
import unittest
from review_agent.repository import ValidationError
from tests import test_review_recovery_v4 as helpers


class RevisionMappingContextTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.RevisionAndConsumerTests(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def test_mapping_change_carries_published_class_definitions_bound_to_original_release(self):
        f=self.fixture;candidate=f.repo.get_revision(f.fixture.candidate['candidate_id'])
        current=f.repo.get_candidate(candidate['candidate_id']);audit=f.repo.get_audit(candidate['candidate_id'],f.fixture.source_result['audit_id'])
        bundle=audit['rounds'][0]['evidence_bundle'];ids=[bundle['items'][0]['evidence_id']]
        draft=f.service.propose(candidate,current['row_version'],{'head_type':None},ids,'Review removal of the source typing.',bundle,f.fixture.index)
        context=draft['ontology_context']
        self.assertEqual(context['release_id'],candidate['ontology_release_id'])
        self.assertEqual(context['ontology_version'],candidate['ontology_version'])
        self.assertEqual(context['relevant_classes'][0]['label'],'device')
        self.assertEqual(draft['evidence_ids'],ids)
        self.assertNotIn('C',[item['evidence_id'] for item in draft['evidence_bundle']['items']])
        self.assertEqual(f.repo.get_audit(candidate['candidate_id'],audit['audit_id'])['votes'],audit['votes'])

    def test_unchanged_patch_cannot_manufacture_a_new_revision_review(self):
        f=self.fixture;candidate=f.repo.get_revision(f.fixture.candidate['candidate_id']);current=f.repo.get_candidate(candidate['candidate_id'])
        bundle=f.repo.get_audit(candidate['candidate_id'],f.fixture.source_result['audit_id'])['rounds'][0]['evidence_bundle']
        with self.assertRaisesRegex(ValidationError,'substantive'):
            f.service.propose(candidate,current['row_version'],{'qualifiers':{'modality':'asserted'}},[bundle['items'][0]['evidence_id']],
                'Unchanged expression is not a new review.',bundle,f.fixture.index)
        with f.repo.connect() as connection:self.assertEqual(connection.execute('SELECT COUNT(*) FROM revision_drafts').fetchone()[0],0)
