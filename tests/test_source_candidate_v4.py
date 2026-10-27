"""Canonical coordinates and source-preserving candidate intake, without models."""

import copy
import json
import os
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from document.snapshot import make_snapshot, read_snapshot
from document.hierarchy_index import split_markdown_text, build_document_index, persist_document_index, load_document_index
from evidence.hierarchical_retriever import context_sentences, document_sentence_evidence
from evidence.verification import verify_evidence_bundle, make_evidence_bundle
from extract_triplets.candidate_contract import normalize_candidate, refresh_revision
from extract_triplets.pipeline_v4 import source_scope, extract_candidates
from ontologyBuild.providers import LegacyOntologyProvider, OntologyImportRequest
from ontologyBuild.release_service import publish_release
from review_agent.contracts import fact_key_for, normalized_qualifiers
from review_agent.validators import validator_payload

ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY = {"domain": "sensor_test", "classes": {"C": {"label": "device"}}, "object_properties": {
    "R": {"id": "R", "label": "relates", "aliases": ["r"], "domain": ["C"], "range": ["C"]}}}


def raw_candidate(head="A", tail="B", quote="A relates B."):
    return {"head": head, "surface_relation": "relates", "tail": tail, "evidence_quotes": [quote],
            "qualifiers": {"polarity": "positive", "condition": None, "time_scope": None, "applicability": None,
                           "modality": "asserted", "quantifier": None},
            "qualifier_status": {"polarity": "extracted", "condition": "not_stated", "time_scope": "not_stated",
                                 "applicability": "not_stated", "modality": "extracted", "quantifier": "not_stated"}}


class FakeClient:
    def __init__(self, callback):
        self.callback, self.calls = callback, []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **request):
        source = json.loads(request["messages"][1]["content"])
        self.calls.append(request)
        value = self.callback(source, len(self.calls))
        if isinstance(value, Exception): raise value
        payload, finish = value if isinstance(value, tuple) else (value, "stop")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)), finish_reason=finish)],
                               model="fixture_model_v1", id="response_fixture", usage=None)


class SourceCandidateV4Tests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "runs" / "_test_source_v4" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)

    def index(self, text="A relates B.", size=600, overlap=20):
        return build_document_index(split_markdown_text(text, "doc", size, min(overlap, size-1)))

    def candidate(self, raw=None, index=None, ordinal=0, scope=None):
        index = index or self.index()
        return normalize_candidate(raw or raw_candidate(), index=index, scope=scope or source_scope(index, index["chunks"][0]),
                                   ontology=ONTOLOGY, release_binding={"ontology_version": "v1", "ontology_release_id": "release1", "profile_hash": "profile1"},
                                   run_id="run", ordinal=ordinal, prompt_hash="prompt_hash")

    def test_two_preprocessors_preserve_one_canonical_source_and_raw_hash(self):
        from extract_triplets import extrct
        from ontologyBuild.document_preprocessor import read_markdown, preprocess_markdown
        path = self.root / "source.md"
        raw = '\ufeff###HEADER#### Title\r\n- [名称](https://example.org) `x` 😀\r\n\r\n```\r\n# code\r\n```\r\n'.encode("utf-8")
        path.write_bytes(raw)
        snapshot = read_snapshot(path)
        with patch.object(extrct, "MARKDOWN_FILE_PATH", str(path)):
            self.assertEqual(extrct.read_markdown(), read_markdown(path))
        self.assertIn('[名称](https://example.org)', snapshot["canonical_text"])
        self.assertIn('```', snapshot["canonical_text"])
        small = preprocess_markdown(path, chunk_size=35, chunk_overlap=5)["document_index"]
        large = build_document_index(split_markdown_text(snapshot["canonical_text"], snapshot["document_id"], 100, 5, snapshot=snapshot))
        self.assertEqual(small["document_version"], large["document_version"])
        self.assertEqual(small["structure_hash"], large["structure_hash"])
        self.assertNotEqual(small["index_content_hash"], large["index_content_hash"])
        self.assertEqual(large["splitter_version"], "hierarchy_v3_chunk100_overlap5")
        self.assertEqual(snapshot["raw_text"].encode("utf-8"), raw)
        self.assertEqual(snapshot["offset_unit"], "unicode_code_point")

    def test_identical_repeated_headings_have_distinct_positions(self):
        text = "# Same\nA relates B.\n# Same\nA relates B.\n"
        index = self.index(text)
        sections = [s for s in index["source_sections"] if s["title"] == "Same"]
        self.assertEqual(len({s["section_id"] for s in sections}), 2)
        self.assertNotEqual(sections[0]["start"], sections[1]["start"])
        for chunk in index["chunks"]:
            self.assertEqual(chunk["content"], text[chunk["document_start"]:chunk["document_end"]])

    def test_english_decimal_abbreviation_and_fenced_heading_boundaries(self):
        index = self.index("# Header\nDr. Smith measured 3.14 units. It works.\n```\n# not a heading\n```\n")
        texts = [s["text"] for s in index["sentences"]]
        self.assertIn("Dr. Smith measured 3.14 units.", texts)
        self.assertIn("It works.", texts)
        self.assertEqual([s["title"] for s in index["source_sections"]], ["正文", "Header"])

    def test_overlapping_chunks_share_full_sentence_identity(self):
        index = self.index("A relates B. " * 15, size=44, overlap=18)
        sightings = {}
        for chunk in index["chunks"]:
            for sentence in chunk["sentences"]:
                sightings.setdefault(sentence["sentence_id"], set()).add(chunk["chunk_id"])
                self.assertEqual(chunk["content"][sentence["start"]:sentence["end"]], sentence["text"])
        self.assertTrue(any(len(chunks) > 1 for chunks in sightings.values()))

    def test_large_overlap_still_makes_forward_progress(self):
        index = self.index("A relates B. " * 8, size=20, overlap=18)
        self.assertLess(len(index["chunks"]), len(index["source_snapshot"]["canonical_text"]))
        ends = [c["document_end"] for c in index["chunks"]]
        self.assertTrue(all(a < b for a, b in zip(ends, ends[1:])))

    def test_context_crosses_chunk_boundaries_without_crossing_sections(self):
        index = self.index("# One\nOnly after calibration. A relates B. Next sentence.\n# Two\nForbidden neighbor.\n", size=35, overlap=5)
        sentence = next(s for s in index["sentences"] if s["text"] == "A relates B.")
        anchor = document_sentence_evidence(index, sentence)
        context = context_sentences(index, anchor, 2, 3)
        self.assertTrue(any("calibration" in s["text"] for s in context))
        self.assertFalse(any("Forbidden" in s["text"] for s in context))
        candidate = {"document_id": "doc", "document_version": index["document_version"], "source_index_hash": index["index_content_hash"]}
        bundle = verify_evidence_bundle(make_evidence_bundle([anchor], []), index, candidate)
        self.assertTrue(bundle["items"][0]["anchor_valid"])

    def test_tampering_text_offsets_or_structure_fails_without_version_change(self):
        index = self.index("# One\nA relates B.\n")
        path = self.root / "index.json"
        persist_document_index(index, path)
        self.assertEqual(load_document_index(path, index["index_content_hash"])["integrity_status"], "verified")
        payload = json.loads(path.read_text(encoding="utf-8"))
        for mutate in (lambda x: x["chunks"][0].update(content="tampered"),
                       lambda x: x["chunks"][0].update(document_start=1),
                       lambda x: x["source_sections"][0].update(title="tampered")):
            changed = copy.deepcopy(payload)
            mutate(changed)
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(ValueError): load_document_index(path)

    def test_legacy_snapshot_is_explicitly_unverified(self):
        path = self.root / "legacy.json"
        path.write_text('{"chunks":[]}', encoding="utf-8")
        self.assertEqual(load_document_index(path)["integrity_status"], "legacy_unverified")
        with self.assertRaises(ValueError): load_document_index(path, require_verified=True)

    def test_missing_polarity_and_status_remain_unknown(self):
        raw = raw_candidate()
        raw.pop("qualifiers")
        raw.pop("qualifier_status")
        candidate = self.candidate(raw)
        self.assertEqual(normalized_qualifiers({})["polarity"], "unknown")
        self.assertEqual(candidate["triple"]["qualifiers"]["polarity"], "unknown")
        self.assertTrue(all(v == "unknown" for v in candidate["triple"]["qualifier_status"].values()))
        self.assertEqual(candidate["fact_key_state"], "provisional")
        self.assertTrue(candidate["fact_key"].startswith("provisional:"))

    def test_unmatched_relation_survives_and_model_id_hint_is_not_authoritative(self):
        raw = {**raw_candidate(), "surface_relation": "uncovered expression", "relation_id_candidate": "R"}
        candidate = self.candidate(raw)
        self.assertIsNone(candidate["triple"]["relation_id"])
        self.assertEqual(candidate["triple"]["surface_relation"], "uncovered expression")
        self.assertEqual(candidate["extraction_hints"]["relation_id_candidate"], "R")
        self.assertEqual(candidate["fact_key_state"], "provisional")

    def test_qualifiers_and_typed_literal_identity_are_not_collapsed(self):
        baseline = self.candidate()
        self.assertEqual(baseline["fact_key_state"], "canonical")
        for field, value in (("condition", "after calibration"), ("polarity", "negative"), ("modality", "can"),
                             ("quantifier", "all"), ("time_scope", "today"), ("applicability", "indoors")):
            raw = raw_candidate()
            raw["qualifiers"][field], raw["qualifier_status"][field] = value, "extracted"
            self.assertNotEqual(self.candidate(raw)["fact_key"], baseline["fact_key"])
        literal = {**raw_candidate(), "tail_kind": "literal", "value": 12, "datatype": "integer", "unit": "m", "language": None}
        changed = {**literal, "unit": "cm"}
        self.assertNotEqual(self.candidate(literal)["fact_key"], self.candidate(changed)["fact_key"])
        shown = copy.deepcopy(baseline)
        shown["triple"]["relation_label"] = "new display label"
        shown["evidence_items"] = []
        self.assertEqual(fact_key_for(shown), baseline["fact_key"])

    def test_short_terms_and_symbols_are_not_deleted_or_forced_to_chinese(self):
        raw = raw_candidate("初速", "射程", "假设：初速影响射程。")
        raw["surface_relation"] = "影响"
        index = self.index(raw["evidence_quotes"][0])
        candidate = self.candidate(raw, index)
        self.assertEqual(candidate["triple"]["head"], "初速")
        symbol = self.candidate(raw_candidate("p", "P"))
        self.assertNotEqual(symbol["triple"]["head_entity_id"], symbol["triple"]["tail_entity_id"])

    def test_body_hypotheses_and_captions_are_requested_instead_of_filtered(self):
        from extract_triplets.extrct import is_example_or_calculation_paragraph
        for source in ("假设：初速影响射程。", "如图1所示，A relates B."):
            self.assertFalse(is_example_or_calculation_paragraph(source))
            client = FakeClient(lambda payload, n: {"triplets": [], "coverage_status": "complete_for_supplied_scope"})
            result = extract_candidates(self.index(source), ONTOLOGY, {}, llm_client=client, model="fixture", run_id="run")
            self.assertEqual(result["model_calls"], 1)

    def test_ambiguous_or_nonverbatim_quotes_stay_provisional(self):
        for text, quote in (("A relates B. A relates B.", "A relates B."), ("A relates B.", "A  relates B.")):
            raw = {**raw_candidate(), "evidence_quotes": [quote]}
            candidate = self.candidate(raw, self.index(text))
            self.assertEqual(candidate["evidence_items"], [])
            self.assertEqual(candidate["fact_key_state"], "provisional")

    def test_source_anchor_id_cannot_be_forged(self):
        index = self.index()
        candidate = self.candidate(index=index)
        item = {**candidate["evidence_items"][0], "evidence_id": "forged"}
        bundle = verify_evidence_bundle(make_evidence_bundle([item], []), index, candidate)
        self.assertEqual(bundle["items"], [])
        self.assertEqual(bundle["rejected_items"][0]["anchor_status"], "source_anchor_id_mismatch")

    def test_different_sources_keep_candidates_but_can_share_a_fact_key(self):
        index = self.index("# One\nA relates B.\n# Two\nA relates B.\n")
        client = FakeClient(lambda source, n: {"triplets": [raw_candidate()], "coverage_status": "complete_for_supplied_scope"})
        result = extract_candidates(index, ONTOLOGY, {"ontology_version": "v1"}, llm_client=client, model="fixture", run_id="run")
        self.assertEqual(len(result["candidates"]), 2)
        first, second = result["candidates"]
        self.assertNotEqual(first["candidate_id"], second["candidate_id"])
        self.assertNotEqual(first["evidence_items"][0]["start"], second["evidence_items"][0]["start"])
        self.assertEqual(first["fact_key"], second["fact_key"])

    def test_truncation_is_recovered_with_subscopes_and_all_calls_are_recorded(self):
        index = self.index("A relates B. C relates D.")
        def respond(source, number):
            if number == 1: return ({"triplets": [], "coverage_status": "partial"}, "length")
            raw = raw_candidate() if "A relates B." in source["core_text"] else raw_candidate("C", "D", "C relates D.")
            return {"triplets": [raw], "coverage_status": "complete_for_supplied_scope"}
        client = FakeClient(respond)
        result = extract_candidates(index, ONTOLOGY, {"ontology_version": "v1"}, llm_client=client, model="fixture", run_id="run",
                                    settings={"min_scope_chars": 4, "max_split_depth": 1})
        self.assertEqual(result["model_calls"], 3)
        self.assertEqual(len(result["candidates"]), 2)
        self.assertTrue(result["requests"][0]["recovered_by_subscopes"])
        self.assertEqual(result["status"], "completed_for_selected_policy")
        self.assertTrue(all(attempt["request_hash"] for entry in result["requests"] for attempt in entry.get("attempts", [])))

    def test_failure_and_budget_exhaustion_do_not_become_successful_empty_output(self):
        client = FakeClient(lambda source, n: TimeoutError("fixture timeout"))
        result = extract_candidates(self.index(), ONTOLOGY, {}, llm_client=client, model="fixture", run_id="run",
                                    settings={"max_model_calls": 1})
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["requests"][0]["attempts"][0]["status"], "failed")

    def test_duplicate_same_source_output_is_removed_and_unknown_qualifiers_reach_validator(self):
        client = FakeClient(lambda source, n: {"triplets": [raw_candidate(), raw_candidate()], "coverage_status": "complete_for_supplied_scope"})
        result = extract_candidates(self.index(), ONTOLOGY, {}, llm_client=client, model="fixture", run_id="run")
        self.assertEqual(len(result["candidates"]), 1)
        raw = raw_candidate()
        raw["qualifiers"]["modality"], raw["qualifier_status"]["modality"] = "can", "extracted"
        candidate = self.candidate(raw)
        payload = validator_payload({"candidate": candidate, "proposal_digest": "p", "evidence_bundle": {"items": candidate["evidence_items"]}})
        self.assertEqual(payload["candidate"]["triple"]["qualifiers"]["modality"], "can")
        self.assertEqual(payload["candidate"]["triple"]["qualifier_status"]["modality"], "extracted")

    def test_more_than_eight_supported_candidates_are_retained(self):
        rows = [raw_candidate(f"A{n}", f"B{n}", f"A{n} relates B{n}.") for n in range(10)]
        index = self.index(" ".join(row["evidence_quotes"][0] for row in rows))
        client = FakeClient(lambda source, n: {"triplets": rows, "coverage_status": "complete_for_supplied_scope"})
        result = extract_candidates(index, ONTOLOGY, {}, llm_client=client, model="fixture", run_id="run")
        self.assertEqual(len(result["candidates"]), 10)

    def test_qualified_candidate_is_not_flattened_by_the_legacy_graph_adapter(self):
        from to_neo4j.fact_projection import direct_edge_eligible
        candidate = self.candidate()
        candidate["latest_decision"] = {"decision_id": "d", "decision_version": 1}
        self.assertTrue(direct_edge_eligible(candidate["triple"]))
        candidate["triple"]["qualifiers"]["polarity"] = "negative"
        self.assertFalse(direct_edge_eligible(candidate["triple"]))

    def test_revision_rebinds_local_identity_and_invalidates_semantic_key(self):
        candidate = self.candidate()
        before = candidate["triple"]["head_entity_id"]
        candidate["triple"]["head"] = "changed"
        candidate["revision"] = 2
        refresh_revision(candidate)
        self.assertNotEqual(candidate["triple"]["head_entity_id"], before)
        self.assertEqual(fact_key_for(candidate), f"provisional:{candidate['candidate_id']}:2")

    def test_provisional_key_is_hidden_from_api_and_human_accept_is_blocked(self):
        from review_agent.repository import ReviewRepository, UnprocessableEntityError
        from review_agent.feedback import FeedbackService
        from review_agent.service import ReviewService
        raw = raw_candidate()
        raw.pop("qualifiers")
        candidate = self.candidate(raw)
        repository = ReviewRepository(self.root / "review.sqlite3")
        repository.initialize()
        repository.create_run({"run_id": "run", "source_hash": candidate["document_version"], "ontology_hash": "v1", "mode": "shadow"})
        repository.add_candidate(candidate)
        service = ReviewService(repository)
        self.assertIsNone(service.candidate(candidate["candidate_id"])["candidate"]["fact_key"])
        with self.assertRaises(UnprocessableEntityError):
            FeedbackService(repository).submit({"candidate_id": candidate["candidate_id"], "revision": 1,
                "expected_row_version": 1, "idempotency_key": "test", "action": "accept", "reason_code": "HUMAN_CONFIRMED", "labels": {}})

    def test_pool_registration_rolls_back_instead_of_leaving_partial_run(self):
        from review_agent.repository import ReviewRepository, NotFoundError
        import sqlite3
        candidate = self.candidate()
        repository = ReviewRepository(self.root / "atomic.sqlite3")
        repository.initialize()
        manifest = {"run_id": "run", "source_hash": candidate["document_version"], "ontology_hash": "v1",
                    "source_index_hash": candidate["source_index_hash"]}
        with self.assertRaises(sqlite3.IntegrityError):
            repository.register_v4_run(manifest, [candidate, candidate])
        with self.assertRaises(NotFoundError): repository.get_run("run")
        with self.assertRaises(NotFoundError): repository.get_candidate(candidate["candidate_id"])

    def test_unrequested_scopes_and_explicit_exercise_policy_are_recorded(self):
        index = self.index("# 习题\nA relates B.\n# 正文\nA relates B.\n")
        client = FakeClient(lambda source, n: {"triplets": [], "coverage_status": "complete_for_supplied_scope"})
        result = extract_candidates(index, ONTOLOGY, {}, llm_client=client, model="fixture", run_id="run", settings={"max_chunks": 1})
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["requests"][0]["status"], "exercise_policy_deferred")
        self.assertEqual(result["requests"][1]["status"], "not_requested_chunk_limit")
        self.assertEqual(result["model_calls"], 0)

    def test_v4_run_persists_full_source_and_candidates_in_isolated_database(self):
        from extract_triplets import extrct
        from extract_triplets.pipeline_v4 import run_v4_extraction
        from review_agent.repository import ReviewRepository
        source = self.root / "source.md"
        source.write_bytes(b"A relates B.\r\n")
        schema = self.root / "ontology.json"
        schema.write_text(json.dumps(ONTOLOGY), encoding="utf-8")
        release = publish_release(LegacyOntologyProvider().build_or_import(OntologyImportRequest(schema)), self.root / "releases",
                                  approval={"status": "approved", "approved_by": "test", "reason": "fixture"})
        client = FakeClient(lambda source, n: {"triplets": [raw_candidate()], "coverage_status": "complete_for_supplied_scope"})
        with patch.multiple(extrct, project_root=str(self.root), ONTOLOGY_RELEASE=release, MARKDOWN_FILE_PATH=str(source),
                            REVIEW_DATABASE_PATH=str(self.root / "review.sqlite3"), REVIEW_CONFIG={}, client=client,
                            OUTPUT_JSON_PATH=str(self.root / "accepted.json"), DISCARDED_TRIPLETS_PATH=str(self.root / "rejected.json"),
                            EVIDENCE_MD_PATH=str(self.root / "evidence.md"), RUNS_V2_DIR=str(self.root / "v2")), \
             patch.dict(os.environ, {"KG_RUN_ID": "source_fixture"}):
            self.assertEqual(run_v4_extraction(SimpleNamespace(max_chunks=None), extrct), 0)
        repository = ReviewRepository(self.root / "review.sqlite3")
        manifest = repository.get_run("source_fixture")
        index = load_document_index(manifest["document_path"], manifest["source_index_hash"])
        self.assertEqual(Path(manifest["raw_source_path"]).read_bytes(), source.read_bytes())
        candidate = repository.list_candidates("source_fixture")[0]
        self.assertEqual(candidate["source_index_hash"], index["index_content_hash"])
        self.assertEqual(candidate["triple"]["qualifiers"]["modality"], "asserted")
        self.assertEqual(candidate["schema_version"], "extraction_candidate_v4")
        self.assertEqual(json.loads((self.root / "accepted.json").read_text()), [])
        pending = self.root / "runs/v3/source_fixture.pending"
        events = [json.loads(line) for line in (pending / "requests.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["event"] for e in events], ["started", "finished"])
        self.assertEqual(json.loads((pending / "status.json").read_text(encoding="utf-8"))["status"], "completed_for_selected_policy")

    def test_interruption_leaves_source_and_request_checkpoint(self):
        from extract_triplets import extrct
        from extract_triplets.pipeline_v4 import run_v4_extraction
        source = self.root / "source.md"
        source.write_text("A relates B.", encoding="utf-8")
        schema = self.root / "ontology.json"
        schema.write_text(json.dumps(ONTOLOGY), encoding="utf-8")
        release = publish_release(LegacyOntologyProvider().build_or_import(OntologyImportRequest(schema)), self.root / "releases",
                                  approval={"status": "approved", "approved_by": "test", "reason": "fixture"})
        interrupted = Mock()
        interrupted.chat.completions.create.side_effect = KeyboardInterrupt()
        with patch.multiple(extrct, project_root=str(self.root), ONTOLOGY_RELEASE=release, MARKDOWN_FILE_PATH=str(source),
                            REVIEW_CONFIG={}, client=interrupted), patch.dict(os.environ, {"KG_RUN_ID": "interrupted_fixture"}):
            with self.assertRaises(KeyboardInterrupt):
                run_v4_extraction(SimpleNamespace(max_chunks=None), extrct)
        pending = self.root / "runs/v3/interrupted_fixture.pending"
        self.assertTrue((pending / "source_index.json").exists())
        events = [json.loads(line) for line in (pending / "requests.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[-1]["status"], "interrupted")
        self.assertEqual(json.loads((pending / "status.json").read_text(encoding="utf-8"))["status"], "interrupted_or_failed")


if __name__ == "__main__":
    unittest.main()
