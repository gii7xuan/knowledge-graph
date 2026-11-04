"""Published bindings remain authoritative when a run also carries a JSON snapshot."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import unittest
from review_agent.admission import load_admission_artifacts
from tests import test_ontology_extension_agent as helpers


class BoundReleaseLoadingTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.OntologyExtensionTests(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def test_bound_candidate_or_run_package_is_used_with_a_separate_compiled_snapshot(self):
        f=self.fixture;candidate=deepcopy(f.candidate)
        snapshot=f.folder/'compiled-only'/'ontology_snapshot.json';snapshot.parent.mkdir()
        snapshot.write_bytes(Path(f.base.snapshot_path).read_bytes())
        candidate['ontology_path']=str(snapshot)
        release,_=load_admission_artifacts(f.repo,candidate)
        self.assertEqual(release.manifest['release_id'],candidate['ontology_release_id'])
        manifest=f.repo.get_run(candidate['run_id']);manifest.update(ontology_path=str(snapshot),ontology_release_path=str(f.base.path))
        candidate.pop('ontology_release_path')
        with patch.object(f.repo,'get_run',return_value=manifest):
            release,_=load_admission_artifacts(f.repo,candidate)
        self.assertEqual(release.manifest['release_id'],candidate['ontology_release_id'])

    def test_wrong_binding_is_rejected_before_identity_or_admission_can_use_it(self):
        f=self.fixture;candidate=deepcopy(f.candidate);candidate['ontology_release_id']='foreign-release'
        with self.assertRaisesRegex(ValueError,'ontology release binding mismatch'):
            load_admission_artifacts(f.repo,candidate)

    def test_missing_explicit_package_does_not_fall_back_to_another_published_release(self):
        f=self.fixture;candidate=deepcopy(f.candidate);candidate['ontology_release_path']=str(f.folder/'missing-release')
        with self.assertRaises(FileNotFoundError):load_admission_artifacts(f.repo,candidate)
