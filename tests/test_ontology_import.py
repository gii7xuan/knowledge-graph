"""Offline import, publication, integrity and extraction-boundary regressions."""

import json
import os
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

from ontologyBuild.providers import LegacyOntologyProvider, NeonImportProvider, OntologyImportRequest
from ontologyBuild.owl_to_project_schema import convert_turtle
from ontologyBuild.release_service import digest, load_release, publish_release, validation_report
from review_agent.ontology_guard import OntologyGuard, load_snapshot, validate_ontology
from schema.subontology_retriever import SubOntologyRetriever
from scripts import import_ontology, run_ontology_then_extract as chain
from scripts.freeze_ontology import freeze_ontology

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "ontology_import.ttl"
APPROVAL = {"status": "approved", "approved_by": "offline_test", "reason": "fixture only"}
EX = "https://example.org/ontology/"


class OntologyImportTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "runs" / "_test_ontology_import" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.artifact = NeonImportProvider().build_or_import(OntologyImportRequest(FIXTURE))
        self.ids = self.artifact.iri_id_map

    def guard(self, artifact=None):
        return OntologyGuard(load_snapshot((artifact or self.artifact).project_schema))

    def triple(self, head="Sensor"):
        return {"head_type": self.ids[EX + head], "relation_id": self.ids[EX + "measures"],
                "tail_type": self.ids[EX + "Quantity"]}

    def publish(self, artifact=None):
        return publish_release(artifact or self.artifact, self.root / "releases", approval=APPROVAL)

    def test_multiple_domains_are_conjunctive_and_all_parents_survive(self):
        self.assertEqual(self.guard().check(self.triple())["status"], "pass")
        self.assertEqual(self.guard().check(self.triple("Device"))["domain_status"], "fail")
        parents = self.artifact.project_schema["classes"][self.ids[EX + "Sensor"]]["parents"]
        self.assertEqual(set(parents), {self.ids[EX + "Device"], self.ids[EX + "Calibrated"]})
        view = SubOntologyRetriever(self.artifact.project_schema).retrieve("", "", top_k_classes=20)
        self.assertEqual(sum(row["child"] == self.ids[EX + "Sensor"] for row in view["class_hierarchy"]), 2)

    def test_xsd_range_is_a_datatype_and_lexical_values_are_checked(self):
        self.assertTrue(validate_ontology(self.artifact.project_schema)["valid"])
        triple = {"relation_id": self.ids[EX + "count"], "head_type": self.ids[EX + "Sensor"],
                  "tail_kind": "literal", "datatype": "integer", "value": "12"}
        self.assertEqual(self.guard().check(triple)["status"], "pass")
        self.assertEqual(self.guard().check({**triple, "value": "12.5"})["status"], "fail")
        self.assertEqual(self.guard().check({**triple, "datatype": "string"})["status"], "fail")
        self.assertEqual(self.guard().check({**triple, "datatype": None})["status"], "unknown")
        self.assertEqual(self.guard().check({**triple, "tail_kind": "entity"})["status"], "fail")

    def test_example_instances_never_become_project_classes_or_facts(self):
        self.assertNotIn(EX + "example", self.ids)
        self.assertNotIn("instances", self.artifact.project_schema)
        self.assertEqual(self.artifact.mapping_report["example_assertion_count"], 2)
        self.assertFalse(self.artifact.mapping_report["example_instances_imported_as_facts"])

    def test_unexecuted_restriction_is_preserved_and_blocks_auto_review(self):
        ttl = FIXTURE.read_text(encoding="utf-8") + '\nex:Sensor rdfs:subClassOf [ a owl:Restriction ; owl:onProperty ex:measures ; owl:someValuesFrom ex:Quantity ] .'
        source = self.root / "restricted.ttl"
        source.write_text(ttl, encoding="utf-8")
        artifact = NeonImportProvider().build_or_import(OntologyImportRequest(source))
        self.assertTrue(artifact.application_profile["auto_review_blocked"])
        self.assertTrue(artifact.mapping_report["unsupported_axioms"])
        self.assertEqual(self.guard(artifact).check(self.triple())["status"], "unknown")
        self.assertIn("owl:Restriction", artifact.source_ttl)
        # Canonical blank-node names make repeated imports reproducible.
        again = NeonImportProvider().build_or_import(OntologyImportRequest(source))
        self.assertEqual(digest(artifact.mapping_report), digest(again.mapping_report))

    def test_imports_are_reported_without_network_resolution(self):
        ttl = FIXTURE.read_text(encoding="utf-8") + '\nex:ontology a owl:Ontology ; owl:imports <https://unavailable.invalid/test> .'
        result = convert_turtle(ttl)
        self.assertTrue(result["application_profile"]["auto_review_blocked"])
        self.assertTrue(any(row["predicate"].endswith("#imports") for row in result["mapping_report"]["unsupported_axioms"]))

    def test_iris_with_same_local_name_have_distinct_stable_ids(self):
        first = convert_turtle('@prefix owl: <http://www.w3.org/2002/07/owl#> . <https://a.test/Thing> a owl:Class . <https://b.test/Thing> a owl:Class .')
        self.assertEqual(len(set(first["iri_id_map"].values())), 2)
        self.assertEqual(self.ids, convert_turtle(FIXTURE.read_text(encoding="utf-8"))["iri_id_map"])

    def test_import_alone_has_no_release_and_tools_are_not_claimed_run(self):
        draft = self.root / "draft"
        self.assertEqual(import_ontology.main(["--source", str(FIXTURE), "--draft-output", str(draft)]), 0)
        self.assertFalse((draft / "release_manifest.json").exists())
        self.assertEqual(json.loads((draft / "approval.json").read_text())["status"], "pending")
        self.assertEqual(self.artifact.validation_report["owl_reasoner"]["status"], "not_run")
        with self.assertRaises(FileNotFoundError):
            load_release(draft)

    def test_release_requires_explicit_approval_and_binds_all_artifacts(self):
        with self.assertRaises(ValueError):
            publish_release(self.artifact, self.root / "releases", approval={"status": "pending"})
        release = self.publish()
        self.assertEqual(release.manifest["ontology_version"], digest(self.artifact.project_schema))
        self.assertEqual(release.manifest["profile_hash"], digest(self.artifact.application_profile))
        self.assertIn("ontology.ttl", release.manifest["artifacts"])
        self.assertEqual(load_release(release.path / "release_manifest.json").binding(), release.binding())

    def test_repeat_publish_does_not_modify_existing_files(self):
        release = self.publish()
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in release.path.iterdir()}
        self.assertEqual(self.publish().path, release.path)
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in release.path.iterdir()})

    def test_concurrent_identical_publications_converge_on_one_complete_bundle(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            releases = list(pool.map(lambda _: self.publish(), range(3)))
        self.assertEqual(len({release.path for release in releases}), 1)
        self.assertEqual(load_release(releases[0].path).manifest, releases[0].manifest)
        self.assertEqual(list((self.root / "releases").glob(".staging_*")), [])

    def test_stale_approval_cannot_publish_a_different_snapshot(self):
        with self.assertRaisesRegex(ValueError, "different ontology"):
            publish_release(self.artifact, self.root / "releases",
                            approval={**APPROVAL, "ontology_version": "old_version"})

    def test_unsupported_axioms_cannot_be_made_automatic_by_clearing_a_profile_flag(self):
        self.artifact.mapping_report["unsupported_axioms"] = [{"blocks_auto_review": True}]
        with self.assertRaises(ValueError):
            self.publish()

    def test_change_creates_a_new_release_and_keeps_old_release(self):
        first = self.publish()
        source=self.root/'revised.ttl'
        source.write_text(self.artifact.source_ttl+'\nex:Sensor rdfs:comment "A revised definition" .\n',encoding='utf-8')
        self.artifact=NeonImportProvider().build_or_import(OntologyImportRequest(source))
        second = self.publish()
        self.assertNotEqual(first.path, second.path)
        self.assertNotEqual(first.manifest["ontology_version"], second.manifest["ontology_version"])
        self.assertEqual(load_release(first.path).ontology, first.ontology)

    def test_tampered_artifacts_and_manifest_are_rejected(self):
        release = self.publish()
        report = release.path / "validation_report.json"
        original = report.read_bytes()
        report.write_text('{}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            load_release(release.path)
        report.write_bytes(original)
        manifest_path = release.path / "release_manifest.json"
        payload = json.loads(manifest_path.read_text())
        payload["profile_hash"] = "tampered"
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest"):
            load_release(release.path)

    def test_conflicting_raw_definitions_cannot_be_silently_merged(self):
        source = self.root / "conflict.json"
        source.write_text(json.dumps({"classes": {"A": {"object_properties": [
            {"id": "R", "domain": ["A"], "range": ["A"]}]}},
            "object_properties": {"R": {"domain": ["A"], "range": ["B"]}}}), encoding="utf-8")
        artifact = LegacyOntologyProvider().build_or_import(OntologyImportRequest(source))
        self.assertTrue(artifact.mapping_report["errors"])
        conflicts = [row for row in validation_report(artifact)["project_validation"]["errors"]
                     if row["code"] == "PROPERTY_DEFINITION_CONFLICT"]
        self.assertEqual(len(conflicts), 1)  # Raw/conversion and project checks describe the same conflict.
        self.assertNotEqual(conflicts[0]["previous"], conflicts[0]["observed"])
        with self.assertRaises(ValueError):
            self.publish(artifact)

    def test_cyclic_or_dangling_parents_block_publication(self):
        sensor = self.artifact.project_schema["classes"][self.ids[EX + "Sensor"]]
        sensor["parents"] = ["missing"]
        self.assertFalse(validation_report(self.artifact)["project_validation"]["valid"])
        with self.assertRaises(ValueError):
            self.publish()
        sensor["parents"] = [sensor["id"]]
        with self.assertRaises(ValueError):
            self.publish()

    def test_failed_external_validation_blocks_publication(self):
        self.artifact.validation_report["external_validation"] = {"status": "failed"}
        with self.assertRaises(ValueError):
            self.publish()

    def test_freeze_legacy_json_publishes_a_full_bundle(self):
        source = self.root / "schema.json"
        source.write_text(json.dumps({"classes": {"A": {"label": "A"}},
                                     "object_properties": {"R": {"domain": ["A"], "range": ["A"]}}}), encoding="utf-8")
        result = freeze_ontology(source, self.root / "legacy", approved_event_id="event_fixture")
        self.assertTrue(result["ok"])
        release = load_release(result["ontology_release_path"])
        self.assertIn("R", self.guard(SimpleNamespace(project_schema=release.ontology)).relations)
        self.assertIn("source_ontology.json", release.manifest["artifacts"])

    def chain_args(self):
        config = self.root / "llm.yaml"
        config.write_text('llm:\n  api_key: unused-test-key\n  base_url: https://unused.invalid/v1\n', encoding="utf-8")
        review = self.root / "review.json"
        review.write_text('{}', encoding="utf-8")
        return ["--input", str(ROOT / "tests/fixtures/ontology_sample.md"), "--output-stem", "test",
                "--llm-config", str(config), "--review-config", str(review), "--chain-id", "chain_test"]

    def test_successful_legacy_generation_never_extracts_with_an_old_ontology(self):
        args = self.chain_args()
        with patch.object(chain, "ROOT", self.root), patch.object(chain, "load_llm_config"), \
             patch.dict(os.environ, {"ONTOLOGY_MODEL": "test"}), \
             patch.object(chain, "run_ontology_pipeline", return_value={"run_id": "test", "status": "succeeded"}), \
             patch.object(chain.subprocess, "run") as extract:
            self.assertEqual(chain.main(args), 2)
            extract.assert_not_called()
        status = json.loads((self.root / "runs/chains/chain_test/status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["stage"], "awaiting_ontology_release")

    def test_chain_passes_selected_release_to_extractor_and_records_binding(self):
        release = self.publish()
        args = self.chain_args() + ["--ontology-release", str(release.path)]
        with patch.object(chain, "ROOT", self.root), patch.object(chain, "load_llm_config"), \
             patch.dict(os.environ, {"ONTOLOGY_MODEL": "test"}), \
             patch.object(chain, "run_ontology_pipeline") as build, \
             patch.object(chain.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as extract:
            self.assertEqual(chain.main(args), 0)
            build.assert_not_called()
            command = extract.call_args.args[0]
            self.assertEqual(command[command.index("--ontology-release") + 1], str(release.path))
        status = json.loads((self.root / "runs/chains/chain_test/status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["ontology_release_id"], release.manifest["release_id"])

    def test_extractor_binds_release_to_manifest_and_candidate_in_isolated_database(self):
        from extract_triplets import extrct
        from review_agent.repository import ReviewRepository
        release = self.publish()
        database = self.root / "review.sqlite3"
        args = extrct.parse_cli_args(["--ontology-release", str(release.path), "--config", str(self.root / "review.json")])
        (self.root / "review.json").write_text('{}', encoding="utf-8")
        with patch.object(extrct, "ONTOLOGY_RELEASE", None), patch.object(extrct, "ONTOLOGY_FILE_PATH", ""), \
             patch.object(extrct, "REVIEW_DATABASE_PATH", str(database)), \
             patch.object(extrct, "project_root", str(self.root)), \
             patch.dict(os.environ, {"KG_RUN_ID": "run_fixture", "KG_REVIEW_DATABASE": str(database)}):
            extrct.configure_from_cli(args)
            self.assertEqual(extrct.ONTOLOGY_FILE_PATH, str(release.snapshot_path))
            manifest = extrct.write_v3_run([{"head": "sensor", "relation": "measures", "tail": "humidity"}],
                                          {"document_id": "doc", "document_version": "v1"}, release.ontology)
            self.assertEqual(manifest["ontology_release_id"], release.manifest["release_id"])
            local = load_release(manifest["ontology_release_path"])
            self.assertEqual(local.manifest, release.manifest)
            candidate = ReviewRepository(database).list_candidates("run_fixture")[0]
            self.assertEqual(candidate["profile_hash"], release.manifest["profile_hash"])
            with self.assertRaises(FileExistsError):
                extrct.write_v3_run([], {"document_id": "doc", "document_version": "v1"}, release.ontology)
            with self.assertRaisesRegex(ValueError, "does not match"):
                extrct.write_v3_run([], {"document_id": "doc", "document_version": "v1"}, {"classes": {}})


if __name__ == "__main__":
    unittest.main()
