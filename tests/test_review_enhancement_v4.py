"""Phase C: independent source votes, issue lifecycle, exact selection and waiting."""

import copy
import json
import sqlite3
import shutil
from contextlib import closing
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from review_agent.agent_state import AgentState
from review_agent.action_policy import ActionPolicy
from review_agent.consensus import aggregate_votes, Committee
from review_agent.contracts import sha256_json
from review_agent.issue_ledger import IssueLedger
from review_agent.llm_clients import MockLLMClient
from review_agent.proposal_resolver import selected_proposal
from review_agent.repository import ReviewRepository, ConflictError
from review_agent.response_contract import validate_response
from review_agent.service import ReviewService
from review_agent.validators import vote_binding
from document.hierarchy_index import build_document_index
from review_agent.cli import _document_index
import test_review_planner as planner_helpers
from test_review_planner import valid_response, action, ontology, finish


class IssueLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.state = AgentState({"candidate_id": "c", "revision": 1}, {}, "audit")
        self.ledger = IssueLedger(self.state)
        self.proposal = {"proposal_digest": "new", "evidence_bundle": {"evidence_hash": "new", "items": [{"evidence_id": "new"}]}}
        self.votes = [{"voter_id": v, "source_support": "supported", "supporting_evidence_ids": ["new"],
                       "counter_evidence_ids": [], "checks": []} for v in "abc"]
        self.consensus = {"eligible_voter_ids": list("abc"), "source_support_consensus": {
            "result": "supported", "support_voter_ids": list("abc")}}

    def test_new_evidence_resolves_insufficient_but_not_missing_condition(self):
        self.ledger.open("EVIDENCE_INSUFFICIENT", "补证", True, evidence_hash="old")
        self.ledger.open("CONDITION_MISSING", "需要新修订", True, evidence_hash="old")
        self.ledger.observe(self.proposal, self.votes, self.consensus, 3)
        self.assertEqual([i["code"] for i in self.ledger.unresolved()], ["CONDITION_MISSING"])
        self.assertEqual(self.state.issues[0]["resolution_rule"], "new_evidence_independent_source_recheck")
        self.assertEqual(len(self.state.issue_events), 3)

    def test_same_evidence_and_planner_text_cannot_resolve(self):
        self.ledger.open("EVIDENCE_INSUFFICIENT", "仍需补证", True, evidence_hash="new")
        self.ledger.observe(self.proposal, self.votes, self.consensus, 3)
        self.assertTrue(self.ledger.unresolved())

    def test_metadata_hash_change_without_new_source_span_does_not_resolve(self):
        self.ledger.open("EVIDENCE_INSUFFICIENT", "同一材料", True, evidence_hash="old", evidence_ids=["new"])
        self.ledger.observe(self.proposal, self.votes, self.consensus, 3)
        self.assertTrue(self.ledger.unresolved())

    def test_all_protected_references_must_be_independently_rechecked(self):
        issue = self.ledger.open("source_conflict", "反证适用范围", True, evidence_ids=["support", "counter"])
        for v in self.votes:
            v["checks"] = [{"check": "counterevidence", "status": "pass", "evidence_ids": ["counter"]}]
        self.ledger.observe(self.proposal, self.votes, self.consensus, 3)
        self.assertEqual(issue["status"], "open")
        for v in self.votes:
            v["checks"][0]["evidence_ids"].append("support")
        self.ledger.observe(self.proposal, self.votes, self.consensus, 3)
        self.assertEqual(issue["status"], "resolved")
        self.assertEqual(issue["resolved_in_proposal"], "new")

    def test_irrelevant_recall_can_be_excluded_but_current_counter_cannot(self):
        self.proposal["evidence_bundle"]["items"] = [{"evidence_id": "recall"}]
        self.state.archive = [{"evidence_id": "recall", "review_obligation": True}]
        for v in self.votes:
            v["evidence_assessments"] = [{"evidence_id": "recall", "role": "irrelevant", "note": "另一设备"}]
        self.ledger.observe(self.proposal, self.votes, self.consensus, 3)
        self.assertIn("recall", self.state.evidence_exclusions)
        self.assertFalse(ActionPolicy({}).final_gaps(self.state)["unreviewed_discovered_evidence"])
        self.state.evidence_exclusions.clear()
        self.votes[0]["counter_evidence_ids"] = ["recall"]
        self.ledger.observe(self.proposal, self.votes, self.consensus, 3)
        self.assertNotIn("recall", self.state.evidence_exclusions)


class EnhancedAgentTests(unittest.TestCase):
    setUp = planner_helpers.PlannerRuntimeTests.setUp
    runner = planner_helpers.PlannerRuntimeTests.runner
    run_script = planner_helpers.PlannerRuntimeTests.run_script
    active_config = planner_helpers.PlannerRuntimeTests.active_config
    ready_policy = planner_helpers.PlannerRuntimeTests.ready_policy
    ready_calibrator = planner_helpers.PlannerRuntimeTests.ready_calibrator

    def gap_candidate(self, candidate_id="gap"):
        candidate = copy.deepcopy(self.candidate)
        candidate.update(candidate_id=candidate_id, ontology_release_id="release_fixture", profile_hash="profile_fixture")
        candidate["triple"].update(relation_id=None, surface_relation="调节")
        self.repo.add_candidate(candidate)
        return candidate

    def gap_validators(self):
        def respond(payload):
            value = valid_response(payload)
            value.update(verdict="uncertain", schema_status="unknown", schema_issue="not_covered",
                         schema_change_hint={"change_kind": "add_object_property"}, reason_codes=["SCHEMA_GAP"])
            next(c for c in value["checks"] if c["check"] == "ontology")["status"] = "unknown"
            return value
        for v in self.validators:
            v["client"] = MockLLMClient(respond, model_id=v["model_id"], model_family=v["model_family"])

    def extension_plan(self, payload):
        if not payload["history"]:
            return action("inspect_ontology", relation_id=None, surface_relation="调节", issue="完整本体查找")
        if payload["history"][-1]["tool"] == "inspect_ontology":
            return action("run_validation")
        return action("request_ontology_extension", change_kind="add_object_property", description="原文调节关系待审核",
                      proposed_changes={"label": "调节"}, evidence_ids=payload["selected_evidence_ids"][:1])

    def run_gap(self, candidate_id="gap"):
        self.gap_candidate(candidate_id)
        self.gap_validators()
        result = self.runner(self.extension_plan).audit_candidate(candidate_id, 1, self.index)
        return result, self.repo.get_audit(candidate_id, result["audit_id"])

    def test_source_supported_schema_gap_creates_waiting_task_without_outbox(self):
        result, audit = self.run_gap()
        self.assertTrue(result["committed"])
        self.assertEqual(result["source_support_result"], "supported")
        self.assertEqual(result["admission_consensus"]["result"], "unresolved")
        self.assertEqual(result["workflow_state"], "awaiting_ontology_review")
        self.assertEqual(audit["agent_state"]["phase"], "waiting_ontology")
        task = self.repo.extension_status(result["pending_extension_id"])
        self.assertFalse(task["ontology_review_executed"])
        self.assertEqual(task["base_release_id"], "release_fixture")
        self.assertEqual(task["subscriptions"][0]["expected_row_version"], self.repo.get_candidate("gap")["row_version"])
        with self.repo.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM import_outbox").fetchone()[0], 0)
        service = ReviewService(self.repo, config=self.config)
        self.assertEqual(service.items("run", workflow_state="awaiting_ontology_review")["total"], 1)
        self.assertEqual(service.summary("run")["workflow_states"]["awaiting_ontology_review"], 1)

    def test_same_gap_shares_task_but_has_distinct_subscriptions(self):
        first, _ = self.run_gap("gap1")
        second, _ = self.run_gap("gap2")
        self.assertEqual(first["pending_extension_id"], second["pending_extension_id"])
        task = self.repo.extension_status(first["pending_extension_id"])
        self.assertEqual(len(task["subscriptions"]), 2)
        self.assertEqual(len(task["trigger_candidates"]), 2)

    def test_candidate_revision_revokes_pending_subscription(self):
        result, _ = self.run_gap()
        current = self.repo.get_candidate("gap")
        revised = self.repo.revise_candidate("gap", current["row_version"], {"triple": {"tail": "湿度"}})
        self.assertEqual(revised["workflow_state"], "ready_for_review")
        self.assertIsNone(revised["pending_extension_id"])
        self.assertEqual(self.repo.extension_status(result["pending_extension_id"])["subscriptions"][0]["status"], "revoked")

    def test_human_reject_revokes_subscription(self):
        result, _ = self.run_gap()
        current = self.repo.get_candidate("gap")
        self.repo.apply_human_feedback({"candidate_id": "gap", "revision": 1, "expected_row_version": current["row_version"],
            "action": "reject", "idempotency_key": "reject-gap"})
        self.assertIsNone(self.repo.get_candidate("gap")["pending_extension_id"])
        self.assertEqual(self.repo.extension_status(result["pending_extension_id"])["subscriptions"][0]["status"], "revoked")

    def test_nullable_full_lookup_finds_existing_mapping_and_blocks_duplicate(self):
        self.gap_candidate()
        with self.repo.transaction(immediate=True) as conn:
            candidate = self.repo.get_revision("gap", 1)
            candidate["triple"]["surface_relation"] = "测量"
            conn.execute("UPDATE candidate_revisions SET candidate_json=? WHERE candidate_id='gap'", (json.dumps(candidate),))
        self.gap_validators()
        result = self.runner(self.extension_plan).audit_candidate("gap", 1, self.index)
        audit = self.repo.get_audit("gap", result["audit_id"])
        self.assertIn("R_measures", audit["agent_state"]["state"]["ontology_lookup"]["matched_ids"])
        self.assertTrue(any(c["status"] == "invalid_action" for c in audit["tool_calls"]))
        self.assertIsNone(result["pending_extension_id"])

    def valid_audit(self):
        result = self.run_script([ontology(), action("run_validation"), finish()])
        return result, self.audit["rounds"][0], self.audit["votes"]

    def add_source_and_two_round_plan(self):
        extra = _document_index("doc", "传感器A已完成校准，可以测量温度。")
        chunk = extra["chunks"][0]
        chunk.update(document_version=self.index["document_version"], chunk_id="extra", section_id="other")
        self.index = build_document_index([self.index["chunks"][0], chunk])
        return [ontology(), action("run_validation"), action("search_evidence", scope="other_section",
            query="校准测量温度", purpose="check_qualifiers", limit=2), action("run_validation"), finish()]

    def test_actual_react_rechecks_new_evidence_and_clears_only_insufficiency(self):
        def respond(payload):
            value = valid_response(payload)
            if len(payload["evidence"]) == 1:
                value.update(verdict="uncertain", source_support="insufficient", supporting_evidence_ids=[], reason_codes=["EVIDENCE_INSUFFICIENT"])
                value["checks"][0]["status"] = "unknown"
            return value
        for v in self.validators:
            v["client"] = MockLLMClient(respond, model_id=v["model_id"], model_family=v["model_family"])
        result = self.run_script(self.add_source_and_two_round_plan(), config=self.active_config(),
                                 risk_policy=self.ready_policy(), calibrator=self.ready_calibrator())
        self.assertEqual(result["decision"], "manual_review")
        self.assertIn("UNIFIED_ADMISSION_BLOCKED", result["reason_codes"])
        self.assertEqual(result["selected_round_id"], 2)
        issue = next(i for i in result["issue_history"] if i["code"] == "EVIDENCE_INSUFFICIENT")
        self.assertEqual(issue["status"], "resolved")
        self.assertEqual(len(self.audit["votes"]), 8)
        self.assertEqual(len(self.audit["issue_events"]), 2)

    def test_condition_error_cannot_be_washed_away_by_new_support(self):
        def respond(payload):
            value = valid_response(payload)
            if len(payload["evidence"]) == 1:
                value.update(verdict="uncertain", source_support="insufficient", supporting_evidence_ids=[], reason_codes=["CONDITION_MISSING"])
                value["checks"][1]["status"] = "unknown"
            return value
        for v in self.validators:
            v["client"] = MockLLMClient(respond, model_id=v["model_id"], model_family=v["model_family"])
        result = self.run_script(self.add_source_and_two_round_plan(), config=self.active_config(),
                                 risk_policy=self.ready_policy(), calibrator=self.ready_calibrator())
        self.assertEqual(result["decision"], "manual_review")
        self.assertEqual(result["workflow_state"], "revision_required")
        self.assertEqual(result["admission_consensus"]["result"], "unresolved")
        self.assertEqual(self.repo.get_candidate("c")["revision"], 1)
        self.assertEqual(next(i for i in result["issue_history"] if i["code"] == "CONDITION_MISSING")["status"], "open")

    def test_conflict_is_resolved_by_current_independent_checks_and_import_uses_only_selected_round(self):
        def respond(payload):
            value = valid_response(payload)
            for check in value["checks"]:
                check["evidence_ids"] = payload["allowed_evidence_ids"]
            return value
        for v in self.validators:
            v["client"] = MockLLMClient(respond, model_id=v["model_id"], model_family=v["model_family"])
        def minority(payload):
            return valid_response(payload, invalid=True) if len(payload["evidence"]) == 1 else respond(payload)
        self.validators[-1]["client"] = MockLLMClient(minority, model_id="model_d", model_family="d")
        result = self.run_script(self.add_source_and_two_round_plan(), config=self.active_config(),
                                 risk_policy=self.ready_policy(), calibrator=self.ready_calibrator())
        self.assertEqual(result["decision"], "manual_review")
        self.assertIn("UNIFIED_ADMISSION_BLOCKED", result["reason_codes"])
        self.assertEqual(result["selected_round_id"], 2)
        self.assertEqual(next(i for i in result["issue_history"] if i["code"] == "source_conflict")["status"], "resolved")
        from to_neo4j.save_to_neo4j import authoritative_item
        decision = self.repo.latest_decision("c")
        item = authoritative_item(self.repo, {"candidate_id": "c", "decision_id": decision["decision_id"], "decision_version": decision["decision_version"]})
        self.assertIsNone(item)  # This legacy fixture has no immutable release/source admission.
        self.assertTrue(any(v["counter_evidence_ids"] for v in self.audit["votes"] if v["round_id"] == 1))
        displayed = ReviewService(self.repo, config=self.config).items("run")["items"][0]["audit_summary"]
        self.assertEqual(len(displayed["votes"]), 4)
        self.assertTrue(displayed["authoritative_selection"])

    def test_waiting_candidate_is_skipped_even_when_batch_forced(self):
        self.run_gap()
        from review_agent.batch import run_review_batch
        with patch("review_agent.batch.configured_members", return_value=[{"id": v["id"]} for v in self.validators]):
            result = run_review_batch(self.repo, "run", self.config, candidate_ids=["gap"], force=True)
        self.assertEqual(result["selected"], 0)
        self.assertEqual(result["skipped"], 1)

    def test_constraint_review_retains_guard_failure_and_never_approves_ontology(self):
        candidate = copy.deepcopy(self.candidate)
        candidate.update(candidate_id="constraint", ontology_release_id="release_fixture", profile_hash="profile_fixture")
        candidate["triple"].update(tail_kind="literal", value=1, datatype="integer")
        self.repo.add_candidate(candidate)
        def respond(payload):
            value = valid_response(payload)
            value.update(verdict="uncertain", schema_status="fail", schema_issue="suspected_schema_error", reason_codes=["CONSTRAINT_VIOLATION"])
            next(c for c in value["checks"] if c["check"] == "ontology")["status"] = "fail"
            return value
        for v in self.validators:
            v["client"] = MockLLMClient(respond, model_id=v["model_id"], model_family=v["model_family"])
        def plan(payload):
            if not payload["history"]: return ontology()
            if not payload["current_validation"]: return action("run_validation")
            return action("request_ontology_extension", change_kind="revise_constraint", description="复核属性种类与端点约束",
                          proposed_changes={"review_relation_id": "R_measures"}, evidence_ids=payload["selected_evidence_ids"][:1])
        result = self.runner(plan).audit_candidate("constraint", 1, self.index)
        self.assertEqual(result["schema_status"], "fail")
        self.assertEqual(result["source_support_result"], "supported")
        self.assertEqual(result["workflow_state"], "awaiting_ontology_review")
        self.assertEqual(self.repo.extension_status(result["pending_extension_id"])["risk_category"], "high_impact")

    def test_unknown_v4_qualifiers_cannot_form_source_support(self):
        _, proposal, votes = self.valid_audit()
        proposal["candidate"].update(contract_version="extraction_candidate_v4")
        proposal["candidate"]["triple"]["qualifier_status"] = {"condition": "unknown"}
        result = aggregate_votes(votes, Committee(list("abcd"), 3, 3), proposal=proposal)
        self.assertEqual(result["source_support_consensus"]["result"], "unresolved")

    def test_reported_same_actual_model_does_not_get_distinct_seats(self):
        _, proposal, votes = self.valid_audit()
        for v in votes: v["returned_model_id"] = "provider-same-model"
        result = aggregate_votes(votes, Committee(list("abcd"), 3, 3), proposal=proposal)
        self.assertEqual(result["duplicate_model_votes"], 3)
        self.assertEqual(result["source_support_consensus"]["support_votes"], 1)

    def test_full_lookup_can_request_a_genuinely_missing_endpoint_class(self):
        candidate = copy.deepcopy(self.candidate)
        candidate.update(candidate_id="class_gap", ontology_release_id="release_fixture", profile_hash="profile_fixture")
        candidate["triple"]["head_type"] = "新型传感装置"
        self.repo.add_candidate(candidate)
        self.gap_validators()
        def plan(payload):
            if not payload["history"]: return ontology()
            if not payload["current_validation"]: return action("run_validation")
            return action("request_ontology_extension", change_kind="add_class", description="端点类待专门审核",
                          proposed_changes={"label": "新型传感装置"}, evidence_ids=payload["selected_evidence_ids"][:1])
        result = self.runner(plan).audit_candidate("class_gap", 1, self.index)
        self.assertEqual(result["workflow_state"], "awaiting_ontology_review")
        self.assertEqual(self.repo.extension_status(result["pending_extension_id"])["change_kind"], "add_class")

    def test_same_named_committee_with_changed_thresholds_cannot_reuse_proposal(self):
        _, proposal, votes = self.valid_audit()
        with self.assertRaisesRegex(ValueError, "thresholds"):
            aggregate_votes(votes, Committee(list("abcd"), 2, 2), proposal=proposal)
        from review_agent.batch import versions_for
        changed = copy.deepcopy(self.config)
        changed["consensus"]["required_votes"] = 2
        self.assertNotEqual(versions_for(changed)["committee_policy_hash"], versions_for(self.config)["committee_policy_hash"])

    def test_new_audit_does_not_erase_a_revision_bound_condition_issue(self):
        def missing(payload):
            value = valid_response(payload)
            value.update(verdict="uncertain", source_support="insufficient", supporting_evidence_ids=[], reason_codes=["CONDITION_MISSING"])
            value["checks"][1]["status"] = "unknown"
            return value
        for v in self.validators:
            v["client"] = MockLLMClient(missing, model_id=v["model_id"], model_family=v["model_family"])
        first = self.run_script([ontology(), action("run_validation"), finish()])
        for v in self.validators:
            v["client"] = MockLLMClient(valid_response, model_id=v["model_id"], model_family=v["model_family"])
        second = self.runner([ontology(), action("run_validation"), finish()]).audit_candidate("c", 1, self.index,
                        context={"request_cache_namespace": "explicit-new-run"})
        self.assertEqual(second["workflow_state"], "revision_required")
        self.assertTrue(any(i.get("inherited_from_audit") == first["audit_id"] for i in second["issue_history"]))

    def test_frozen_candidate_content_cannot_be_changed_under_an_old_digest(self):
        result, proposal, _ = self.valid_audit()
        proposal["candidate"]["triple"]["head_type"] = "changed_type"
        with self.repo.transaction(immediate=True) as conn:
            conn.execute("UPDATE proposals SET proposal_json=? WHERE audit_id=? AND round_id=1", (json.dumps(proposal), result["audit_id"]))
        with self.assertRaisesRegex(ValueError, "content hash"):
            selected_proposal(self.repo, self.repo.get_candidate("c"), result)

    def test_exact_selection_detects_changed_evidence_and_ignores_other_rounds(self):
        result, proposal, votes = self.valid_audit()
        candidate = self.repo.get_candidate("c")
        selected, counted = selected_proposal(self.repo, candidate, result)
        self.assertEqual(selected["round_id"], result["selected_round_id"])
        self.assertEqual(len(counted), 4)
        with self.assertRaisesRegex(ValueError, "evidence"):
            selected_proposal(self.repo, candidate, {**result, "selected_evidence_hash": "changed"})
        with self.assertRaisesRegex(ValueError, "authoritative"):
            selected_proposal(self.repo, candidate, {**result, "selected_round_id": None})

    def test_task_revision_release_and_committee_replay_never_count(self):
        _, proposal, votes = self.valid_audit()
        committee = Committee(list("abcd"), 3, 3)
        for key, value in (("task_type", "ontology_review"), ("object_revision", 2), ("round_id", 9),
                           ("evidence_hash", "different"), ("source_snapshot_hash", "other"), ("committee_version", "other"), ("ontology_release_id", "other")):
            changed = [{**v, key: value} for v in votes]
            with self.subTest(key=key):
                result = aggregate_votes(changed, committee, proposal_digest=proposal["proposal_digest"], proposal=proposal)
                self.assertEqual(result["missing_votes"], 4)
                self.assertEqual(result["source_support_consensus"]["result"], "unresolved")

    def test_duplicate_model_aliases_do_not_form_independent_source_consensus(self):
        _, proposal, votes = self.valid_audit()
        for v in votes:
            v["model_identity"] = "same-canonical-model"
        for m in proposal["committee_members"]:
            m["model_identity"] = "same-canonical-model"
        result = aggregate_votes(votes, Committee(list("abcd"), 3, 3), proposal=proposal)
        self.assertEqual(result["duplicate_model_votes"], 3)
        self.assertEqual(result["source_support_consensus"]["support_votes"], 1)
        self.assertEqual(result["source_support_consensus"]["committee_size"], 4)

    def test_malformed_ok_vote_cannot_bypass_contract(self):
        _, proposal, votes = self.valid_audit()
        for v in votes:
            v["schema_issue"] = "invented"
        result = aggregate_votes(votes, Committee(list("abcd"), 3, 3), proposal=proposal)
        self.assertEqual(result["invalid_attempts"], 4)

    def test_schema_only_failure_cannot_claim_source_rejection(self):
        value = valid_response({"allowed_evidence_ids": ["ev"]})
        value.update(verdict="invalid", schema_status="fail", schema_issue="suspected_schema_error", reason_codes=["CONSTRAINT_VIOLATION"])
        next(c for c in value["checks"] if c["check"] == "ontology")["status"] = "fail"
        self.assertTrue(validate_response(value))


class LifecycleMigrationTests(unittest.TestCase):
    def test_v3_backup_migration_is_idempotent_and_can_be_restored(self):
        folder = Path(__file__).resolve().parents[1] / "runs" / ("migration_test_" + uuid4().hex)
        folder.mkdir()
        self.addCleanup(shutil.rmtree, folder)
        path = folder / "review.sqlite3"
        repo = ReviewRepository(path)
        repo.initialize()
        repo.create_run({"run_id": "keep", "mode": "shadow"})
        with repo.connect() as conn:
            conn.execute("ALTER TABLE candidates DROP COLUMN pending_extension_id")
            conn.execute("ALTER TABLE candidates DROP COLUMN workflow_state")
            conn.execute("DELETE FROM schema_migrations WHERE migration_version=4")
            conn.commit()
        repo.initialize()
        repo.initialize()
        backups = list((folder / "migration_backups").glob("*.sqlite3"))
        self.assertEqual(len(backups), 1)
        with repo.connect() as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertIn("workflow_state", {r[1] for r in conn.execute("PRAGMA table_info(candidates)")})
        with closing(sqlite3.connect(backups[0])) as conn:
            self.assertNotIn("workflow_state", {r[1] for r in conn.execute("PRAGMA table_info(candidates)")})
            self.assertEqual(conn.execute("SELECT run_id FROM runs").fetchone()[0], "keep")

    def test_lifecycle_ddl_rolls_back_as_a_unit_on_failure(self):
        folder = Path(__file__).resolve().parents[1] / "runs" / ("migration_failure_" + uuid4().hex)
        folder.mkdir()
        self.addCleanup(shutil.rmtree, folder)
        repo = ReviewRepository(folder / "review.sqlite3")
        with closing(sqlite3.connect(repo.database_path)) as conn:
            conn.execute("CREATE TABLE candidates(candidate_id TEXT PRIMARY KEY)")
        with self.assertRaises(sqlite3.OperationalError), repo.transaction(immediate=True) as conn:
            repo.initialize_lifecycle(conn)
            conn.execute("INVALID SQL")
        with repo.connect() as conn:
            self.assertEqual([r[1] for r in conn.execute("PRAGMA table_info(candidates)")], ["candidate_id"])


if __name__ == "__main__":
    unittest.main()
