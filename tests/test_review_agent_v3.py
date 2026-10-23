import unittest
from pathlib import Path
from uuid import uuid4

from confidence.calibrator import TripleCalibrator
from confidence.risk_policy import RiskPolicy, find_accept_threshold
from evidence.hierarchical_retriever import HierarchicalEvidenceRetriever
from evidence.verification import verify_vote_references
from review_agent.consensus import Committee, aggregate_votes
from review_agent.contracts import fact_key_for
from review_agent.feedback import FeedbackService
from review_agent.llm_clients import MockLLMClient
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.repository import (
    ConflictError,
    ReviewRepository,
    UnprocessableEntityError,
    ValidationError,
)
from review_agent.service import ReviewService, ServiceUnavailableError
from review_agent.validators import judge_validator
from to_neo4j.save_to_neo4j import Neo4jImporter, apply_outbox


def candidate(run_id="run_test", candidate_id="cand_test"):
    value = {
        "schema_version": "triple_review_v3", "run_id": run_id,
        "candidate_id": candidate_id, "revision": 1, "document_id": "doc",
        "document_version": "doc_hash", "ontology_version": "onto_hash",
        "domain": "demo", "fact_namespace": "demo",
        "source_chunk_id": "chunk_1", "section_id": "section_1",
        "triple": {
            "head": "传感器A", "surface_relation": "测量", "relation_id": "R_measures",
            "tail": "湿度", "tail_kind": "entity", "qualifiers": {"polarity": "positive"},
        },
    }
    value["fact_key"] = fact_key_for(value, "demo")
    return value


class EvidenceSemanticsTests(unittest.TestCase):
    def test_exact_negative_quote_is_only_located_and_deduplicated(self):
        text = "传感器A不能测量湿度。"
        sentence = {"sentence_id": "s1", "text": text, "start": 0, "end": len(text),
                    "document_start": 0, "document_end": len(text)}
        chunk = {
            "document_id": "doc", "document_version": "v1", "chunk_id": "chunk_1",
            "section_id": "section_1", "parent_section_id": None, "content": text,
            "sentences": [sentence], "breadcrumb_path": "说明",
        }
        index = {"chunks": [chunk], "by_chunk_id": {"chunk_1": chunk},
                 "by_section_id": {"section_1": [chunk]}, "descendant_chunks_by_section_id": {}}
        result = HierarchicalEvidenceRetriever().retrieve(
            {"head": "传感器A", "relation": "测量", "tail": "湿度", "evidence_quote": text},
            chunk, index,
        )
        self.assertEqual(result["retrieved_unique_span_count"], 1)
        self.assertEqual(result["items"][0]["semantic_status"], "unknown")
        self.assertNotIn("entailment_score", result["items"][0])
        self.assertNotIn("contradiction_score", result["items"][0])


class ConsensusTests(unittest.TestCase):
    def setUp(self):
        self.committee = Committee(["a", "b", "c", "d"], 3, 3)

    def vote(self, voter, verdict="valid", status="ok", digest="p"):
        return {"voter_id": voter, "verdict": verdict, "response_status": status,
                "proposal_digest": digest, "model_family": voter}

    def test_timeouts_do_not_reduce_denominator(self):
        result = aggregate_votes([
            self.vote("a"), self.vote("b"), self.vote("c", status="timeout"),
            self.vote("d", status="timeout"),
        ], self.committee, proposal_digest="p")
        self.assertEqual(result["result"], "unresolved")
        self.assertEqual(result["committee_size"], 4)
        self.assertEqual(result["error_votes"], 2)

    def test_duplicate_voter_counts_once_and_round_digest_isolated(self):
        result = aggregate_votes([
            self.vote("a"), self.vote("a"), self.vote("b"), self.vote("c", digest="old"), self.vote("d")
        ], self.committee, proposal_digest="p")
        self.assertEqual(result["valid_votes"], 3)
        self.assertEqual(result["duplicate_attempts"], 1)
        self.assertEqual(result["invalid_attempts"], 1)

    def test_valid_minority_counterevidence_blocks_accept(self):
        votes = [self.vote("a"), self.vote("b"), self.vote("c"),
                 {**self.vote("d", "invalid"), "counter_evidence_ids": ["ev_1"]}]
        result = aggregate_votes(votes, self.committee, proposal_digest="p",
                                 unresolved_counterevidence=True)
        self.assertEqual(result["result"], "unresolved")

    def test_winning_votes_must_come_from_distinct_model_families(self):
        votes = [
            {**self.vote("a"), "model_family": "same"},
            {**self.vote("b"), "model_family": "same"},
            {**self.vote("c"), "model_family": "other"},
            {**self.vote("d", "invalid"), "model_family": "third"},
        ]
        result = aggregate_votes(votes, self.committee, proposal_digest="p")
        self.assertEqual(result["valid_votes"], 3)
        self.assertEqual(result["result"], "unresolved")


class ColdStartTests(unittest.TestCase):
    def test_untrained_calibrator_and_default_policy_fail_closed(self):
        self.assertIsNone(TripleCalibrator().predict_proba({}, raw_score=0.99))
        self.assertEqual(RiskPolicy().route(1.0), "manual_review")
        threshold = find_accept_threshold([1, 1], [1.0, 1.0], min_selected=100)
        self.assertFalse(threshold["enabled"])
        self.assertIsNone(threshold["threshold"])
        conservative = find_accept_threshold([1] * 100, [0.99] * 100, min_selected=100)
        self.assertTrue(conservative["enabled"])
        self.assertGreaterEqual(conservative["wilson_lower"], 0.95)

    def test_active_job_is_rejected_when_committee_and_policy_are_unready(self):
        database_path = Path(__file__).resolve().parents[1] / "runs" / "_test_review_agent" / f"{uuid4().hex}.sqlite3"
        repository = ReviewRepository(database_path)
        repository.initialize()
        try:
            repository.create_run({"run_id": "run_test", "schema_version": "triple_review_v3",
                                   "mode": "active", "source_hash": "doc", "ontology_hash": "onto"})
            service = ReviewService(repository, config={
                "mode": "active", "validators": [],
                "consensus": {"committee_size": 4, "minimum_distinct_families": 3},
                "calibration": {"model_path": None, "policy_path": None},
            })
            with self.assertRaises(ServiceUnavailableError):
                service.create_audit_job({"run_id": "run_test", "mode": "active"})
        finally:
            for path in (database_path, Path(str(database_path) + "-wal"), Path(str(database_path) + "-shm")):
                if path.exists():
                    path.unlink()


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.database_path = Path(__file__).resolve().parents[1] / "runs" / "_test_review_agent" / f"{uuid4().hex}.sqlite3"
        self.repository = ReviewRepository(self.database_path)
        self.repository.initialize()
        self.repository.create_run({"run_id": "run_test", "schema_version": "triple_review_v3",
                                    "mode": "shadow", "source_hash": "doc_hash", "ontology_hash": "onto_hash"})
        self.repository.add_candidate(candidate())
        self.feedback = FeedbackService(self.repository)

    def tearDown(self):
        for path in (self.database_path, Path(str(self.database_path) + "-wal"), Path(str(self.database_path) + "-shm")):
            if path.exists():
                path.unlink()

    def test_defer_has_no_truth_label_and_is_idempotent(self):
        current = self.repository.get_candidate("cand_test")
        payload = {"candidate_id": "cand_test", "revision": 1,
                   "expected_row_version": current["row_version"], "idempotency_key": "same",
                   "action": "defer", "reason_code": "EVIDENCE_INSUFFICIENT",
                   "labels": {"valid_for_kg": None}}
        first = self.feedback.submit(payload)
        second = self.feedback.submit(payload)
        self.assertIsNone(first["event"]["labels"]["valid_for_kg"])
        self.assertEqual(first["event"]["event_id"], second["event"]["event_id"])
        self.assertEqual(self.repository.feedback_rows(), [])

    def test_same_idempotency_key_with_different_body_conflicts(self):
        current = self.repository.get_candidate("cand_test")
        payload = {"candidate_id": "cand_test", "revision": 1,
                   "expected_row_version": current["row_version"], "idempotency_key": "same",
                   "action": "defer", "reason_code": "EVIDENCE_INSUFFICIENT", "labels": {}}
        self.feedback.submit(payload)
        with self.assertRaises(ConflictError):
            self.feedback.submit({**payload, "comment": "different"})

    def test_reject_cannot_silently_create_positive_label(self):
        current = self.repository.get_candidate("cand_test")
        with self.assertRaises(ValidationError):
            self.feedback.submit({
                "candidate_id": "cand_test", "revision": 1,
                "expected_row_version": current["row_version"], "idempotency_key": "reject",
                "action": "reject", "reason_code": "FACT_INCORRECT", "labels": {"valid_for_kg": 1},
            })

    def test_human_accept_requires_a_passing_ontology_check(self):
        guard = OntologyGuard(load_snapshot({
            "classes": {"C_Device": {"id": "C_Device", "label": "设备"}},
            "object_properties": {
                "R_measures": {"id": "R_measures", "label": "测量",
                               "domain": ["C_Device"], "range": ["C_Device"]}
            },
        }))
        checked_feedback = FeedbackService(self.repository, ontology_guard=guard)
        current = self.repository.get_candidate("cand_test")
        with self.assertRaises(UnprocessableEntityError):
            checked_feedback.submit({
                "candidate_id": "cand_test", "revision": 1,
                "expected_row_version": current["row_version"], "idempotency_key": "unknown-types",
                "action": "accept", "reason_code": "HUMAN_CONFIRMED",
                "labels": {"valid_for_kg": 1},
            })

    def test_modify_creates_revision_without_accepting_it(self):
        current = self.repository.get_candidate("cand_test")
        result = self.feedback.submit({
            "candidate_id": "cand_test", "revision": 1,
            "expected_row_version": current["row_version"], "idempotency_key": "modify",
            "action": "modify", "reason_code": "REVISION_REQUIRED", "labels": {},
            "modifications": {"triple": {"tail": "温度"}},
        })
        self.assertEqual(result["candidate"]["revision"], 2)
        self.assertEqual(result["candidate"]["human_status"], "pending")
        self.assertEqual(self.repository.get_revision("cand_test", 1)["triple"]["tail"], "湿度")
        self.assertEqual(self.repository.get_revision("cand_test", 2)["triple"]["tail"], "温度")
        self.assertEqual(result["candidate"]["fact_key"], fact_key_for(result["candidate"]))

    def test_modify_cannot_change_frozen_provenance(self):
        current = self.repository.get_candidate("cand_test")
        with self.assertRaises(ValidationError):
            self.feedback.submit({
                "candidate_id": "cand_test", "revision": 1,
                "expected_row_version": current["row_version"], "idempotency_key": "protected",
                "action": "modify", "reason_code": "REVISION_REQUIRED", "labels": {},
                "modifications": {"document_version": "tampered"},
            })

    def test_outbox_can_be_scoped_to_one_run(self):
        self.repository.create_run({"run_id": "run_other", "schema_version": "triple_review_v3",
                                    "mode": "shadow", "source_hash": "doc_hash", "ontology_hash": "onto_hash"})
        self.repository.add_candidate(candidate("run_other", "cand_other"))
        for candidate_id in ("cand_test", "cand_other"):
            current = self.repository.get_candidate(candidate_id)
            self.feedback.submit({
                "candidate_id": candidate_id, "revision": 1,
                "expected_row_version": current["row_version"],
                "idempotency_key": f"accept-{candidate_id}", "action": "accept",
                "reason_code": "HUMAN_CONFIRMED", "labels": {"valid_for_kg": 1},
            })
        scoped = self.repository.list_outbox(run_id="run_other")
        self.assertEqual([item["candidate_id"] for item in scoped], ["cand_other"])

    def test_relation_filter_is_applied_before_pagination_and_count(self):
        other = candidate(candidate_id="cand_other_relation")
        other["triple"]["relation_id"] = "R_controls"
        other["fact_key"] = fact_key_for(other, "demo")
        self.repository.add_candidate(other)
        rows = self.repository.list_candidates(
            "run_test", relation_id="R_measures", limit=1, offset=0,
        )
        self.assertEqual([item["candidate_id"] for item in rows], ["cand_test"])
        self.assertEqual(self.repository.count_candidates("run_test", relation_id="R_measures"), 1)

    def test_modify_after_accept_queues_a_current_retraction(self):
        current = self.repository.get_candidate("cand_test")
        accepted = self.feedback.submit({
            "candidate_id": "cand_test", "revision": 1,
            "expected_row_version": current["row_version"], "idempotency_key": "accept-first",
            "action": "accept", "reason_code": "HUMAN_CONFIRMED",
            "labels": {"valid_for_kg": 1},
        })
        self.feedback.submit({
            "candidate_id": "cand_test", "revision": 1,
            "expected_row_version": accepted["candidate"]["row_version"], "idempotency_key": "modify-after",
            "action": "modify", "reason_code": "REVISION_REQUIRED", "labels": {},
            "modifications": {"triple": {"tail": "温度"}},
        })
        events = self.repository.list_outbox(run_id="run_test")
        self.assertEqual([item["operation"] for item in events], ["upsert", "retract"])
        self.assertEqual(events[-1]["revision"], 1)

    def test_auto_accept_decision_is_complete_and_queued_for_import(self):
        audit_id = self.repository.start_audit(
            "cand_test", 1, {"mode": "active", "feature_schema_version": "review_features_v3"},
        )
        committed = self.repository.commit_audit_if_current("cand_test", 1, audit_id, {
            "audit_id": audit_id, "candidate_id": "cand_test", "revision": 1,
            "decision": "auto_accept", "reason_codes": [],
            "consensus": {"result": "accept_consensus"},
            "calibrated_probability": 0.99, "calibration_status": "ok",
            "calibrator_version": "cal_v1", "policy_version": "policy_v1", "features": {},
        })
        self.assertTrue(committed)
        decision = self.repository.latest_decision("cand_test")
        self.assertEqual(decision["decision_version"], 1)
        self.assertEqual(decision["audit_id"], audit_id)
        item = {**self.repository.get_candidate("cand_test"),
                "latest_decision": {**decision, "active": True}}
        importer = Neo4jImporter.__new__(Neo4jImporter)
        self.assertTrue(importer.is_importable(item))
        outbox = self.repository.list_outbox(run_id="run_test")
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0]["operation"], "upsert")
        self.repository.mark_outbox(outbox[0]["event_id"], "completed")
        self.assertEqual(self.repository.get_candidate("cand_test")["import_status"], "imported")

    def test_queued_job_cancellation_is_terminal(self):
        job = self.repository.create_job("audit", {"run_id": "run_test"})
        cancelled = self.repository.request_cancel(job["job_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["cancel_requested"], 1)
        self.assertIsNone(self.repository.claim_job("test-worker"))

    def test_import_compensates_if_decision_changes_during_projection(self):
        current = self.repository.get_candidate("cand_test")
        self.feedback.submit({
            "candidate_id": "cand_test", "revision": 1,
            "expected_row_version": current["row_version"], "idempotency_key": "accept-race",
            "action": "accept", "reason_code": "HUMAN_CONFIRMED",
            "labels": {"valid_for_kg": 1},
        })
        feedback = self.feedback
        repository = self.repository

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        class RacingImporter:
            def __init__(self):
                self.driver = self
                self.retracted = False

            def session(self):
                return FakeSession()

            def import_triplet(self, _session, _item):
                latest = repository.get_candidate("cand_test")
                feedback.submit({
                    "candidate_id": "cand_test", "revision": 1,
                    "expected_row_version": latest["row_version"], "idempotency_key": "reject-race",
                    "action": "reject", "reason_code": "FACT_INCORRECT",
                    "labels": {"valid_for_kg": 0},
                })
                return True

            def retract(self, _session, **_kwargs):
                self.retracted = True

        importer = RacingImporter()
        result = apply_outbox(repository, importer, run_id="run_test")
        self.assertEqual(result["stale"], 1)
        self.assertTrue(importer.retracted)


class ImportGateTests(unittest.TestCase):
    def setUp(self):
        self.importer = Neo4jImporter.__new__(Neo4jImporter)

    def test_legacy_accept_flag_never_grants_import(self):
        self.assertFalse(self.importer.is_importable({"decision": "auto_accept", "review_status": "accepted"}))

    def test_only_matching_human_decision_is_importable(self):
        item = candidate()
        item["latest_decision"] = {
            "decision_id": "d1", "decision_version": 1, "revision": 1,
            "decision": "human_accept", "source": "human_confirmed", "review_event_id": "e1",
            "active": True,
        }
        self.assertTrue(self.importer.is_importable(item))
        item["revision"] = 2
        self.assertFalse(self.importer.is_importable(item))

    def test_qualifiers_are_part_of_fact_identity(self):
        positive = candidate()
        conditional = candidate()
        conditional["triple"]["qualifiers"]["condition"] = "条件X"
        self.assertNotEqual(fact_key_for(positive), fact_key_for(conditional))


class VoteReferenceTests(unittest.TestCase):
    def test_fabricated_evidence_id_invalidates_vote_without_negative_count(self):
        vote = verify_vote_references({
            "response_status": "ok", "verdict": "valid", "supporting_evidence_ids": ["fake"]
        }, [{"evidence_id": "real"}])
        self.assertEqual(vote["response_status"], "invalid_reference")
        self.assertIsNone(vote["verdict"])

    def test_retry_attempts_are_preserved_separately_from_the_final_vote(self):
        client = MockLLMClient([
            RuntimeError("HTTP 503 temporary server error"),
            {"verdict": "valid", "source_support": "supported", "schema_status": "pass",
             "supporting_evidence_ids": ["ev_1"], "counter_evidence_ids": [],
             "reason_codes": [], "requested_context": None, "suggested_revision": None,
             "checks": [{"check": name, "status": "pass", "evidence_ids": ["ev_1"], "note": "checked"}
                        for name in ("triple_support", "qualifiers", "relation_direction", "ontology", "counterevidence")]},
        ])
        result = judge_validator(
            {"id": "judge_a", "model_id": "mock", "model_family": "family_a", "client": client},
            {"audit_id": "audit", "round_id": 1, "proposal_digest": "digest",
             "candidate": candidate(), "ontology_check": {},
             "evidence_bundle": {"items": [{"evidence_id": "ev_1"}]},
             "review_memory": {}},
            max_retries=1,
        )
        self.assertEqual(result["response_status"], "ok")
        self.assertEqual(result["attempt_no"], 2)
        self.assertEqual([item["response_status"] for item in result["attempts"]], ["api_error", "ok"])


if __name__ == "__main__":
    unittest.main()
