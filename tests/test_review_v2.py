"""Regression cases for the evidence-chain and validator-response upgrade."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from confidence.calibrator import TripleCalibrator
from confidence.risk_policy import RiskPolicy
from document.hierarchy_index import build_document_index, sentence_spans
from evidence.hierarchical_retriever import retrieve_initial, retrieve_more
from evidence.verification import make_evidence_bundle, verify_evidence_bundle
from review_agent.cli import _document_index
from review_agent.consensus import aggregate_votes, Committee
from review_agent.contracts import REVIEW_VERSION_FIELDS, proposal_digest
from review_agent.feedback import FeedbackService
from review_agent.llm_clients import MockLLMClient, OpenAICompatibleClient
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.repository import ReviewRepository, ValidationError
from review_agent.response_contract import CHECK_NAMES, CONTRACT_VERSION, PROMPT_VERSION, validate_response
from review_agent.service import ReviewService
from review_agent.validators import _parse_content, judge_independently, judge_validator, validator_payload
from scripts.train_calibrator import training_versions


def response(verdict="valid", eid="ev_1", schema="pass"):
    support = {"valid": "supported", "invalid": "contradicted", "uncertain": "insufficient"}[verdict]
    return {"verdict": verdict, "source_support": support, "schema_status": schema,
            "schema_issue": "none" if schema in {"pass", "not_applicable"} else "not_covered", "schema_change_hint": None,
            "supporting_evidence_ids": [eid] if support == "supported" else [],
            "counter_evidence_ids": [eid] if support == "contradicted" else [],
            "reason_codes": ["SOURCE_CONTRADICTS"] if verdict == "invalid" else [],
            "checks": [{"check": name, "status": schema if name == "ontology" else ("pass" if verdict == "valid" else "unknown"),
                        "evidence_ids": [eid], "note": "checked"} for name in CHECK_NAMES],
            "requested_context": None, "suggested_revision": None}


def proposal():
    return {"audit_id": "audit", "round_id": 1, "proposal_digest": "digest", "candidate": {
        "candidate_id": "c", "revision": 1, "evidence_items": [{"evidence_id": "old"}],
        "legacy_score": .99, "triple": {"head": "设备", "surface_relation": "测量", "tail": "温度",
                                         "evidence": [{"evidence_id": "old_nested"}]}},
        "evidence_bundle": {"items": [{"evidence_id": "ev_1"}]}, "ontology_check": {"status": "pass"}}


class ContractTests(unittest.TestCase):
    def test_types_and_semantic_constraints(self):
        self.assertEqual(validate_response(response()), [])
        for field, value in (("checks", None), ("checks", ["bad"]), ("reason_codes", "bad"),
                             ("supporting_evidence_ids", None), ("verdict", []),
                             ("requested_context", {"scope": "outside", "query": "", "limit": True})):
            with self.subTest(field=field, value=value):
                item = response()
                item[field] = value
                self.assertTrue(validate_response(item))
        item = response("uncertain")
        item["verdict"] = "invalid"
        self.assertTrue(validate_response(item))
        item = response()
        item["counter_evidence_ids"] = ["ev_1"]
        self.assertTrue(validate_response(item))

    def test_whitelist_excludes_all_legacy_evidence(self):
        payload = validator_payload(proposal())
        self.assertNotIn("evidence_items", payload["candidate"])
        self.assertNotIn("legacy_score", payload["candidate"])
        self.assertNotIn("evidence", payload["candidate"]["triple"])
        self.assertEqual(payload["allowed_evidence_ids"], ["ev_1"])

    def test_fenced_json_only(self):
        self.assertEqual(_parse_content('```json\n{"a": 1}\n```'), {"a": 1})
        with self.assertRaises(json.JSONDecodeError):
            _parse_content('Some answer: {"a": 1}')

    def test_bad_reference_gets_specific_feedback_and_one_final_vote(self):
        client = MockLLMClient([response(eid="old"), response()])
        voter = {"id": "a", "model_id": "m", "model_family": "f", "client": client}
        result = judge_validator(voter, proposal(), max_retries=1)
        self.assertEqual(result["response_status"], "ok")
        self.assertEqual([x["response_status"] for x in result["attempts"]], ["invalid_reference", "ok"])
        self.assertEqual(client.calls[1]["correction_feedback"]["invalid_evidence_ids"], ["old"])
        self.assertEqual(client.calls[0]["evidence"], client.calls[1]["evidence"])

    def test_bad_types_do_not_interrupt_other_models(self):
        malformed = {**response(), "checks": "not an array"}
        voters = [{"id": name, "client": MockLLMClient([item])} for name, item in
                  (("a", malformed), ("b", response()))]
        results = judge_independently(voters, proposal())
        self.assertEqual([r["response_status"] for r in results], ["invalid_contract", "ok"])

    def test_client_always_mentions_json(self):
        create = SimpleNamespace(create=lambda **kw: self._capture(kw))
        client = OpenAICompatibleClient(api_key="test", base_url="https://example.test", model_id="m", model_family="f")
        client._client = SimpleNamespace(chat=SimpleNamespace(completions=create))
        client.complete_json({}, system_prompt="审核")
        self.assertIn("JSON", self.request["messages"][0]["content"])

    def _capture(self, request):
        self.request = request
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))], usage=None)

    def test_schema_gap_retains_source_support(self):
        value = response()
        value.update(verdict="uncertain", schema_status="unknown", schema_issue="not_covered", reason_codes=["SCHEMA_GAP"])
        value["checks"][3]["status"] = "unknown"
        self.assertEqual(validate_response(value), [])


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.index = _document_index("doc", "传感器A测量温度。该功能仅在高温条件下有效。它不能测量湿度。")
        self.chunk = self.index["chunks"][0]
        self.candidate = {"document_id": "doc", "document_version": self.index["document_version"],
                          "source_chunk_id": self.chunk["chunk_id"],
                          "extraction_quote": "传感器A测量温度。",
                          "triple": {"head": "传感器A", "surface_relation": "测量", "tail": "温度"}}

    def test_quote_neighbors_and_negation_are_locatable_not_semantic_support(self):
        bundle = verify_evidence_bundle(retrieve_initial(self.candidate, self.index), self.index, self.candidate)
        self.assertTrue(bundle["items"])
        self.assertTrue(any("仅在高温" in i["quote"] for i in bundle["items"]))
        self.assertTrue(all(i["anchor_valid"] and i["semantic_status"] == "unknown" for i in bundle["items"]))
        candidate = {**self.candidate, "extraction_quote": "它不能测量湿度。"}
        negative = verify_evidence_bundle(retrieve_initial(candidate, self.index), self.index, candidate)
        self.assertTrue(any("不能" in i["quote"] and i["anchor_valid"] for i in negative["items"]))

    def test_whitespace_and_cross_sentence_quote_keep_actual_offsets(self):
        self.candidate["extraction_quote"] = "传感器 A测量温度。该功能仅在高温条件下有效。"
        bundle = verify_evidence_bundle(retrieve_initial(self.candidate, self.index), self.index, self.candidate)
        first = bundle["items"][0]
        self.assertIsNone(first["sentence_id"])
        self.assertEqual(first["quote"], self.chunk["content"][first["chunk_start"]:first["chunk_end"]])
        self.assertEqual(first["match_status"], "normalized_match")

    def test_invalid_versions_offsets_and_colliding_ids_are_excluded(self):
        item = retrieve_initial(self.candidate, self.index)["items"][0]
        for update in ({"document_version": "bad"}, {"end": 9999}, {"start": "0"}):
            checked = verify_evidence_bundle({"items": [{**item, **update}]}, self.index, self.candidate)
            self.assertEqual(checked["items"], [])
            self.assertFalse(checked["rejected_items"][0]["anchor_valid"])
        checked = verify_evidence_bundle({"items": [item, {**item, "end": item["end"] + 1}]}, self.index, self.candidate)
        self.assertEqual(checked["items"], [])
        self.assertTrue(all(x["anchor_status"] == "conflicting_evidence_id" for x in checked["rejected_items"]))

    def test_more_excludes_seen_before_limit_and_respects_scope(self):
        chunks = [self.chunk]
        for i in range(15):
            text = f"传感器A测量温度，补充说明{i}。"
            chunk = {**self.chunk, "chunk_id": f"chunk_{i}", "content": text,
                     "document_start": None, "document_end": None,
                     "sentences": sentence_spans(text, f"chunk_{i}")}
            chunks.append(chunk)
        index = build_document_index(chunks)
        first = retrieve_more(self.candidate, index, {"scope": "same_section", "limit": 6}, [])
        second = retrieve_more(self.candidate, index, {"scope": "same_section", "limit": 6},
                               [x["evidence_id"] for x in first["items"]])
        self.assertEqual(len(second["items"]), 6)
        self.assertFalse({x["evidence_id"] for x in first["items"]} & {x["evidence_id"] for x in second["items"]})
        other = retrieve_more(self.candidate, index, {"scope": "other_section", "limit": 6}, [])
        self.assertEqual(other["items"], [])
        self.assertEqual(other["searched_levels"], ["other_section"])

    def test_canonical_offsets_and_overlapping_chunks_share_one_span(self):
        first = {**self.chunk, "document_start": 100}
        second = {**first, "chunk_id": "overlap"}
        index = build_document_index([first, second])
        a = retrieve_initial(self.candidate, index)["items"][0]
        b = {**a, "chunk_id": second["chunk_id"]}
        checked = verify_evidence_bundle({"items": [a, b]}, index, self.candidate)
        self.assertEqual(len(checked["items"]), 1)
        self.assertEqual(checked["independent_document_count"], 1)
        self.assertEqual(a["start"], 100 + a["chunk_start"])
        wrong = verify_evidence_bundle({"items": [{**a, "chunk_start": a["chunk_start"] + 1}]}, index, self.candidate)
        self.assertEqual(wrong["items"], [])


class AuditPersistenceTests(EvidenceTests):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "runs")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.document = self.root / "document.json"
        self.document.write_text(json.dumps({"chunks": self.index["chunks"]}), encoding="utf-8")
        self.repo = ReviewRepository(self.root / "review.sqlite3")
        self.repo.initialize()
        self.repo.create_run({"run_id": "run", "schema_version": "triple_review_v3", "mode": "shadow",
                              "document_path": str(self.document)})
        self.candidate.update(candidate_id="c", revision=1, run_id="run", ontology_version="onto")
        self.repo.add_candidate(self.candidate)

    def test_feedback_falls_back_to_displayed_decision_and_exports_snapshot(self):
        versions = dict(zip(REVIEW_VERSION_FIELDS, (PROMPT_VERSION, CONTRACT_VERSION, "evidence_bundle_v2", "committee_v1")))
        audit_id = self.repo.start_audit("c", 1, {**versions, "mode": "shadow", "feature_schema_version": "review_features_v3"})
        self.repo.commit_audit_if_current("c", 1, audit_id, {"features": {"quote_located": 1}, "decision": "manual_review"})
        current = self.repo.get_candidate("c")
        event = FeedbackService(self.repo).submit({"candidate_id": "c", "revision": 1,
            "expected_row_version": current["row_version"], "idempotency_key": "accept", "action": "accept"})
        self.assertEqual(event["event"]["audit_id"], audit_id)
        row = self.repo.feedback_rows()[0]
        self.assertEqual(row["audit_id"], audit_id)
        self.assertEqual(row["features"], {"quote_located": 1})
        self.assertEqual(training_versions([row]), versions)

    def test_feedback_rejects_running_or_foreign_audit(self):
        audit_id = self.repo.start_audit("c", 1, {"mode": "shadow"})
        current = self.repo.get_candidate("c")
        with self.assertRaises(ValidationError):
            FeedbackService(self.repo).submit({"candidate_id": "c", "revision": 1, "audit_id": audit_id,
                "expected_row_version": current["row_version"], "idempotency_key": "bad", "action": "defer"})

    def test_old_unbound_label_is_not_attached_to_a_later_audit(self):
        current = self.repo.get_candidate("c")
        FeedbackService(self.repo).submit({"candidate_id": "c", "revision": 1,
            "expected_row_version": current["row_version"], "idempotency_key": "before-audit", "action": "accept"})
        aid = self.repo.start_audit("c", 1, {"mode": "shadow"})
        self.repo.commit_audit_if_current("c", 1, aid, {"features": {"quote_located": 1}, "decision": "manual_review"})
        row = self.repo.feedback_rows()[0]
        self.assertIsNone(row["audit_id"])
        self.assertEqual(row["audit_snapshot_status"], "missing")
        self.assertEqual(row["features"], {})

    def test_schema_rejection_does_not_imply_source_contradiction(self):
        current = self.repo.get_candidate("c")
        result = FeedbackService(self.repo).submit({"candidate_id": "c", "revision": 1,
            "expected_row_version": current["row_version"], "idempotency_key": "schema-gap", "action": "reject",
            "reason_code": "SCHEMA_GAP", "labels": {"source_support": "supported"}})
        self.assertEqual(result["event"]["labels"]["source_support"], "supported")

    def test_context_covers_entire_cross_sentence_quote_and_missing_source(self):
        self.candidate["extraction_quote"] = "传感器A测量温度。该功能仅在高温条件下有效。"
        bundle = verify_evidence_bundle(retrieve_initial(self.candidate, self.index), self.index, self.candidate)
        aid = self.repo.start_audit("c", 1, {"mode": "shadow"})
        p = {**proposal(), "candidate": self.candidate, "audit_id": aid, "evidence_bundle": bundle}
        self.repo.append_round(aid, p, [], {}, {})
        service = ReviewService(self.repo)
        context = service.evidence_context(bundle["items"][0]["evidence_id"], before=0, after=0, audit_id=aid, round_id=1)
        self.assertEqual(len(context["context"]), 2)
        self.assertEqual(context["status"], "exact")
        self.document.unlink()
        self.assertEqual(service.evidence_context(bundle["items"][0]["evidence_id"], audit_id=aid)["status"], "source_missing")

    def test_rounds_refreeze_all_models_and_preserve_conflict(self):
        text = "传感器A测量温度，还需要校准。"
        extra = {**self.chunk, "chunk_id": "supplement", "document_start": None,
                 "document_end": None, "content": text, "sentences": sentence_spans(text, "supplement")}
        self.index = build_document_index([self.chunk, extra])
        voters = []
        for name in "abcd":
            def reply(payload, name=name):
                value = response("invalid" if name == "d" else "valid", payload["allowed_evidence_ids"][0])
                return value
            voters.append({"id": name, "model_id": f"model-{name}", "model_family": name, "client": MockLLMClient(reply)})
        guard = SimpleNamespace(check=lambda _: {"status": "pass"})
        audit = ReviewOrchestrator(self.repo, guard, voters, config={"mode": "shadow"})
        result = audit.audit_candidate("c", 1, self.index)
        stored = self.repo.get_audit("c", result["audit_id"])
        self.assertEqual(result["decision"], "manual_review")
        self.assertTrue(result["consensus"]["unresolved_counterevidence"])
        self.assertEqual(len(stored["rounds"]), 2)
        self.assertNotEqual(stored["rounds"][0]["proposal_digest"], stored["rounds"][1]["proposal_digest"])
        for i in range(2):
            self.assertTrue(all(v["client"].calls[i]["evidence"] == voters[0]["client"].calls[i]["evidence"] for v in voters))


class VersionTests(unittest.TestCase):
    def test_duplicate_model_does_not_create_an_independent_vote(self):
        votes = [{"voter_id": name, "model_id": model, "model_family": family,
                  "response_status": "ok", "verdict": "valid"} for name, model, family in (
                    ("a", "Qwen", "qwen"), ("b", "Qwen", "qwen"), ("c", "Gemma", "gemma"))]
        result = aggregate_votes(votes, Committee(["a", "b", "c", "d"], 3, 3))
        self.assertEqual(result["valid_votes"], 2)
        self.assertEqual(result["duplicate_model_votes"], 1)
        self.assertEqual(result["committee_size"], 4)
        self.assertEqual(result["result"], "unresolved")

    def test_calibrator_rejects_missing_contract_version(self):
        calibrator = TripleCalibrator(model=object(), metadata={"prompt_version": PROMPT_VERSION})
        result = calibrator.predict_if_compatible({}, {"prompt_version": PROMPT_VERSION,
                                                       "response_contract_version": CONTRACT_VERSION})
        self.assertEqual(result.status, "incompatible")

    def test_mixed_training_versions_cannot_be_relabelled(self):
        row = {"audit_id": "a", "audit_snapshot_status": "bound",
               "review_versions": dict.fromkeys(REVIEW_VERSION_FIELDS, "v2")}
        other = copy.deepcopy(row)
        other["review_versions"]["prompt_version"] = "v1"
        with self.assertRaises(ValueError):
            training_versions([row, other])

    def test_digest_binds_response_contract(self):
        self.assertNotEqual(proposal_digest({"response_contract_version": "v1"}),
                            proposal_digest({"response_contract_version": "v2"}))

    def test_unverified_anchors_and_support_conflicts_cannot_route(self):
        result = RiskPolicy().route_v3(guard={"status": "pass"}, evidence={"items": [{}]},
            consensus={"result": "accept_consensus"}, probability=.99,
            gaps={"unresolved_support_conflict": True})
        self.assertEqual(result["decision"], "manual_review")
        self.assertIn("EVIDENCE_ANCHOR_UNVERIFIED", result["reason_codes"])


if __name__ == "__main__":
    unittest.main()
