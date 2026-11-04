"""A modeling assumption keeps its modality, scope and independent-review boundary."""
from copy import deepcopy
import unittest
from extract_triplets.candidate_contract import normalize_qualifiers
from review_agent.revision_patch import merge_triple_patch
from review_agent.repository import ValidationError
from review_agent.planner_contracts import argument_schemas
from tests import test_review_recovery_v4 as helpers


class AssumptionModalityTests(unittest.TestCase):
    def test_normalization_distinguishes_an_assumption_from_an_asserted_fact(self):
        values,statuses,issues=normalize_qualifiers({'qualifiers':{'modality':'assumed','condition':'within the model'},
            'qualifier_status':{'modality':'extracted','condition':'extracted'}})
        self.assertEqual(values['modality'],'assumed');self.assertEqual(statuses['modality'],'extracted')
        self.assertNotIn('INVALID_MODALITY',issues);self.assertEqual(values['condition'],'within the model')

    def test_planner_schema_allows_assumed_but_not_arbitrary_values(self):
        schema=argument_schemas()['propose_revision']['properties']['changes']['properties']['qualifiers']['properties']['modality']
        self.assertIn('assumed',schema['enum']);self.assertNotIn('anything',schema['enum'])

    def test_reviewed_assumption_creates_a_new_revision_without_reusing_old_authority(self):
        fixture=helpers.RevisionAndConsumerTests(methodName='runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        f=fixture;identity=f.fixture.candidate['candidate_id'];before=f.repo.get_revision(identity)
        current=f.repo.get_candidate(identity)
        bundle=f.repo.get_audit(identity,f.fixture.source_result['audit_id'])['rounds'][0]['evidence_bundle']
        old_votes=deepcopy(f.repo.get_audit(identity,f.fixture.source_result['audit_id'])['votes'])
        draft=f.service.propose(before,current['row_version'],{'qualifiers':{'modality':'assumed'}},
            [bundle['items'][0]['evidence_id']],'fixture tests modality preservation, not source semantics',bundle,f.fixture.index)
        self.assertEqual(f.repo.get_revision(identity)['triple']['qualifiers'],before['triple']['qualifiers'])
        self.assertEqual(f.approve(draft)['status'],'approved')
        result=f.service.apply(draft['draft_id'],draft['draft_hash']);after=f.repo.get_revision(identity)
        self.assertEqual(after['triple']['qualifiers']['modality'],'assumed')
        for key in ('condition','time_scope','applicability','quantifier','polarity'):
            self.assertEqual(after['triple']['qualifiers'][key],before['triple']['qualifiers'][key])
        self.assertNotEqual(after['fact_key'],before['fact_key'])
        self.assertIsNone(f.repo.latest_decision(identity));self.assertFalse(result['old_votes_reusable'])
        self.assertEqual(f.repo.get_job(result['reaudit_job_id'])['status'],'queued')
        self.assertEqual(f.repo.get_audit(identity,f.fixture.source_result['audit_id'])['votes'],old_votes)
        with self.assertRaises(ValidationError):merge_triple_patch(after['triple'],{'qualifiers':{'modality':'anything'}},strict=True)
