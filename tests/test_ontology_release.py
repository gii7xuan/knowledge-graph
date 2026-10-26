"""Publication, profile confirmation, frozen drafts and cross-process head CAS."""

import json
import os
import shutil
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from ontologyBuild.application_profile import quarantine_legacy_artifact
from ontologyBuild.import_service import create_draft, publish_draft
from ontologyBuild.providers import LegacyOntologyProvider, NeonImportProvider, OntologyImportRequest
from ontologyBuild.release_catalog import ReleaseCatalog, ReleaseConflictError, select_release
from ontologyBuild.release_service import digest, load_release, publish_release, validation_report
from ontologyBuild.owl_to_project_schema import convert_turtle
from review_agent.ontology_guard import OntologyGuard, load_snapshot

ROOT = Path(__file__).resolve().parents[1]
APPROVAL = {"status": "approved", "approved_by": "test_operator", "reason": "test only"}


class OntologyReleaseTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "runs" / "_test_ontology_release" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.source = self.root / "schema.json"
        self.source.write_text(json.dumps({"classes": {"A": {"label": "A"}}, "object_properties": {
            "R": {"id": "R", "label": "rel", "domain": ["A"], "range": ["A"]}}}, indent=4), encoding="utf-8")
        self.release_root = self.root / "releases"
        self.artifact = LegacyOntologyProvider().build_or_import(OntologyImportRequest(self.source))

    def catalog(self):
        return ReleaseCatalog(self.release_root)

    def draft(self, source=None, quarantine=False):
        return create_draft(OntologyImportRequest(source or self.source), quarantine=quarantine,
                            draft_root=self.root / "imports", release_root=self.release_root)

    def publish_preview(self, preview, **overrides):
        args = dict(expected_artifact_digest=preview["artifact_digest"],
                    expected_base_release_id=preview["base_release_id"], approved_by="operator",
                    reason="fixture", confirm_differences=False, release_root=self.release_root)
        args.update(overrides)
        return publish_draft(preview["draft_path"], **args)

    def test_actual_source_bytes_survive_release_and_match_provenance(self):
        original = self.source.read_bytes()
        release = self.catalog().publish(self.artifact, approval=APPROVAL)
        self.assertEqual((release.path / "source_ontology.json").read_bytes(), original)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(select_release("current", root=self.release_root).manifest, release.manifest)

    def test_second_publication_requires_the_current_base(self):
        first = self.catalog().publish(self.artifact, approval=APPROVAL)
        self.artifact.project_schema["classes"]["A"]["description"] = "updated"
        with self.assertRaises(ReleaseConflictError):
            self.catalog().publish(self.artifact, approval=APPROVAL)
        second = self.catalog().publish(self.artifact, approval=APPROVAL,
                                        expected_base_release_id=first.manifest["release_id"])
        self.assertNotEqual(first.manifest["release_id"], second.manifest["release_id"])
        self.assertEqual(len(self.catalog().history()), 2)
        self.assertEqual(self.catalog().history()[-1]["base_release_id"], first.manifest["release_id"])
        self.assertFalse(self.catalog().history()[-1]["candidate_rebind_applied"])
        self.assertEqual(load_release(first.path).ontology, first.ontology)

    def test_retry_of_identical_publication_does_not_create_another_event(self):
        first = self.catalog().publish(self.artifact, approval=APPROVAL)
        retried = self.catalog().publish(self.artifact, approval=APPROVAL)
        self.assertEqual(first.manifest, retried.manifest)
        self.assertEqual(len(self.catalog().history()), 1)

    def test_independent_processes_cannot_both_publish_against_the_same_base(self):
        # Actual SQLite transactions from two Python processes, not a mocked lock.
        code = "from pathlib import Path; import sys; from ontologyBuild.providers import *; from ontologyBuild.release_catalog import *; a=LegacyOntologyProvider().build_or_import(OntologyImportRequest(Path(sys.argv[1]))); ReleaseCatalog(sys.argv[2]).publish(a,approval={'status':'approved','approved_by':'test','reason':sys.argv[3]})"
        env = {**os.environ, "PYTHONPATH": str(ROOT / ".runtime-packages") + os.pathsep + str(ROOT)}
        def run(reason):
            return subprocess.run([sys.executable, "-c", code, str(self.source), str(self.release_root), reason],
                                  env=env, cwd=ROOT, capture_output=True, text=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, ["one", "two"]))
        self.assertEqual(sorted(result.returncode for result in results), [0, 1])
        self.assertIn("ReleaseConflictError", next(result.stderr for result in results if result.returncode))
        self.assertEqual(len(self.catalog().history()), 1)
        self.assertIsNotNone(self.catalog().current())

    def test_invalid_publication_does_not_move_the_head(self):
        first = self.catalog().publish(self.artifact, approval=APPROVAL)
        self.artifact.project_schema["object_properties"]["R"]["range"] = ["missing"]
        with self.assertRaises(ValueError):
            self.catalog().publish(self.artifact, approval=APPROVAL,
                                    expected_base_release_id=first.manifest["release_id"])
        self.assertEqual(self.catalog().current().manifest, first.manifest)
        self.assertEqual(len(self.catalog().history()), 1)

    def test_release_directory_identity_cannot_be_reused_for_other_content(self):
        release = self.catalog().publish(self.artifact, approval=APPROVAL)
        wrong_path = release.path.parent / ("release_" + "0" * 64)
        shutil.copytree(release.path, wrong_path)
        with self.assertRaisesRegex(ValueError, "directory identity"):
            load_release(wrong_path)

    def test_frozen_preview_survives_source_changes(self):
        preview = self.draft()
        self.source.write_text('{"classes":{}}', encoding="utf-8")
        release = self.publish_preview(preview)
        self.assertIn("A", release.ontology["classes"])
        self.assertEqual(release.manifest["ontology_version"], preview["ontology_version"])

    def test_tampered_draft_cannot_use_the_old_preview_digest(self):
        preview = self.draft()
        path = Path(preview["draft_path"]) / "import_artifact.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["project_schema"]["classes"]["A"]["description"] = "changed"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed since preview"):
            self.publish_preview(preview)
        self.assertIsNone(self.catalog().current())

    def test_old_preview_does_not_overwrite_a_new_head(self):
        first = self.draft()
        changed = json.loads(self.source.read_text(encoding="utf-8"))
        changed["classes"]["A"]["description"] = "different proposal against the same base"
        self.source.write_text(json.dumps(changed), encoding="utf-8")
        second = self.draft()
        self.publish_preview(first)
        with self.assertRaises(ReleaseConflictError):
            self.publish_preview(second)

    def test_quarantine_preserves_conflicts_and_never_adds_placeholder_classes(self):
        raw = {"classes": {"A": {"label": "A", "subclasses": ["missing"], "object_properties": [
            {"id": "bad", "domain": ["A"], "range": ["missing"]}]}},
            "object_properties": {"bad": {"id": "bad", "domain": ["A"], "range": ["A"]},
                                  "ok": {"id": "ok", "domain": ["A"], "range": ["A"]}},
            "instances": [{"example": "not a fact"}]}
        self.source.write_text(json.dumps(raw), encoding="utf-8")
        preview = self.draft(quarantine=True)
        self.assertTrue(preview["validation"]["project_validation"]["valid"])
        self.assertFalse(preview["validation"]["auto_review_allowed"])
        with self.assertRaisesRegex(ValueError, "confirmation"):
            self.publish_preview(preview)
        release = self.publish_preview(preview, confirm_differences=True)
        self.assertNotIn("bad", release.ontology["object_properties"])
        self.assertIn("ok", release.ontology["object_properties"])
        self.assertNotIn("missing", release.ontology["classes"])
        self.assertEqual(json.loads((release.path / "source_ontology.json").read_text()), raw)
        self.assertNotIn("instances", release.ontology)
        guard = OntologyGuard(load_snapshot(release.ontology))
        self.assertEqual(guard.check({"relation_id": "ok", "head_type": "A", "tail_type": "A"})["status"], "unknown")

    def test_profile_confirmation_is_invalidated_by_a_diff_change(self):
        artifact = quarantine_legacy_artifact(self.artifact)
        approval = {**APPROVAL, "confirmed_profile_hash": digest(artifact.application_profile),
                    "confirmed_mapping_report_hash": digest(artifact.mapping_report)}
        artifact.application_profile["differences"].append({"kind": "changed"})
        with self.assertRaisesRegex(ValueError, "confirmation"):
            publish_release(artifact, self.release_root, approval=approval)

    def test_legacy_unexecuted_constraints_and_examples_are_not_silently_executed(self):
        schema = json.loads(self.source.read_text())
        schema["classes"]["A"]["data_properties"] = [{"field": "level", "datatype": "integer", "enum": [1, 2]}]
        schema["instances"] = [{"name": "example"}]
        self.source.write_text(json.dumps(schema), encoding="utf-8")
        artifact = LegacyOntologyProvider().build_or_import(OntologyImportRequest(self.source))
        self.assertTrue(artifact.application_profile["auto_review_blocked"])
        self.assertNotIn("instances", artifact.project_schema)
        self.assertTrue(artifact.mapping_report["unsupported_axioms"])
        self.assertEqual(validation_report(artifact)["competency_checks"]["status"], "not_run")

    def test_legacy_constraint_reports_are_stable_across_python_hash_seeds(self):
        schema = json.loads(self.source.read_text(encoding="utf-8"))
        schema["classes"]["A"]["data_properties"] = [{"field": "level", "datatype": "integer",
            "enum": [1, 2], "minimum": 1, "maximum": 2, "required": True}]
        self.source.write_text(json.dumps(schema), encoding="utf-8")
        code = "from pathlib import Path; import sys; from dataclasses import asdict; from ontologyBuild.providers import *; from ontologyBuild.release_service import digest; print(digest(asdict(LegacyOntologyProvider().build_or_import(OntologyImportRequest(Path(sys.argv[1]))))))"
        hashes = []
        for seed in ("1", "2", "3"):
            env = {**os.environ, "PYTHONHASHSEED": seed,
                   "PYTHONPATH": str(ROOT / ".runtime-packages") + os.pathsep + str(ROOT)}
            result = subprocess.run([sys.executable, "-c", code, str(self.source)], env=env,
                                    cwd=ROOT, capture_output=True, text=True, check=True)
            hashes.append(result.stdout.strip())
        self.assertEqual(len(set(hashes)), 1)

    def test_approved_class_aliases_are_resolved_and_ambiguous_relations_are_not(self):
        schema = {"classes": {"A": {"label": "A", "aliases": ["AliasA"]}}, "object_properties": {
            "R1": {"label": "same", "domain": ["A"], "range": ["A"]},
            "R2": {"label": "same", "domain": ["A"], "range": ["A"]}}}
        guard = OntologyGuard(load_snapshot(schema))
        result = guard.check({"relation": "same", "head_type": "AliasA", "tail_type": "A"})
        self.assertTrue(result["mapping_ambiguous"])
        self.assertEqual(guard.check({"relation_id": "R1", "head_type": "AliasA", "tail_type": "A"})["status"], "pass")

    def test_skos_definition_and_aliases_are_preserved(self):
        converted = convert_turtle('@prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix skos: <http://www.w3.org/2004/02/skos/core#> . <https://example.org/A> a owl:Class ; skos:prefLabel "A" ; skos:altLabel "Alias" ; skos:definition "Defined" .')
        cls = next(iter(converted["project_schema"]["classes"].values()))
        self.assertEqual(cls["description"], "Defined")
        self.assertIn("Alias", cls["aliases"])
        self.assertFalse(converted["application_profile"]["auto_review_blocked"])

    def test_workbench_import_publish_and_conflict_http_routes(self):
        import workbench_app as workbench
        handler = workbench.WorkbenchHandler.__new__(workbench.WorkbenchHandler)
        handler.path = "/api/ontology/releases"
        handler.send_json = Mock()
        handler.read_payload = Mock(return_value={})
        with patch.object(workbench, "publish_ontology_import", side_effect=ReleaseConflictError("stale")):
            handler.do_POST()
        self.assertEqual(handler.send_json.call_args.args[1], 409)
        with patch.object(workbench, "ontology_releases", return_value={"current": None, "history": []}):
            handler.do_GET()
        self.assertEqual(handler.send_json.call_args.args[0]["history"], [])

    def test_workbench_functions_publish_the_frozen_preview(self):
        import workbench_app as workbench
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench.ontology, "ROOT", self.root):
            preview = workbench.inspect_ontology_import({"source_path": "schema.json"})
            published = workbench.publish_ontology_import({
                "draft_path": preview["draft_path"], "artifact_digest": preview["artifact_digest"],
                "expected_base_release_id": preview["base_release_id"], "approved_by": "operator", "reason": "fixture"})
            self.assertEqual(workbench.ontology_releases()["current"]["ontology_release_id"], published["ontology_release_id"])
            with self.assertRaises(ValueError):
                workbench.publish_ontology_import({"draft_path": str(self.root)})

    def test_cli_publication_returns_the_release_id_and_updates_import_receipt(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from scripts import import_ontology
        draft_path = self.root / "cli_draft"
        output = StringIO()
        with redirect_stdout(output):
            code = import_ontology.main(["--source", str(self.source), "--draft-output", str(draft_path),
                                        "--release-root", str(self.release_root), "--publish", "--activate",
                                        "--approved-by", "operator", "--approval-reason", "fixture"])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["release_id"], result["ontology_release_id"])
        self.assertTrue(result["release_id"])
        receipt = json.loads((draft_path / "import_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "published")


if __name__ == "__main__":
    unittest.main()
