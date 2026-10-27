"""Offline real-tool control-flow and publication tests, no real model/Neo4j."""
import copy
import json
import shutil
import sqlite3
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from contextlib import closing

from document.hierarchy_index import build_document_index, split_markdown_text, persist_document_index
from extract_triplets.candidate_contract import normalize_candidate
from extract_triplets.pipeline_v4 import source_scope
from ontologyBuild.providers.base import OntologyArtifact
from ontologyBuild.release_catalog import ReleaseCatalog, ReleaseConflictError
from ontologyBuild.release_service import load_release
from review_agent.repository import ReviewRepository, ConflictError
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.llm_clients import MockLLMClient
from review_agent.response_contract import CHECK_NAMES
from review_agent.contracts import sha256_json
from review_agent.agent_state import AgentStopped
from review_agent.case_budget import CaseBudget
from ontology_review.runtime import OntologyReviewRuntime
from ontology_review.publisher import OntologyPublisher, current_release_id
from ontology_review.contracts import CHECKS, validate_response, proposal_digest
from ontology_review.delta import apply_delta
from ontology_review.validators import consensus


ROOT = Path(__file__).resolve().parents[1]


def blocked_ontology_worker(sender, *args):
    time.sleep(5)
    sender.close()


def action(name, **arguments):
    return {"action": name, "arguments": arguments, "target_issue": "fixture modeling investigation", "rationale_summary": "MOCK planner following actual observations"}


def ontology_vote(payload):
    eid = payload["allowed_evidence_ids"][0]
    return {"decision": "approve", "necessity": "confirmed_gap", "semantic_status": "pass", "structure_status": "pass", "impact_status": "pass",
        "supporting_evidence_ids": [eid], "counter_evidence_ids": [], "checks": [
            {"check": name, "status": "pass", "evidence_ids": [eid], "note": "MOCK semantic result, not measured model quality",
             **({'evidence_scope':'definition'} if name=='constraint_strength' else {})} for name in CHECKS],
        "reason_codes": [], "suggested_changes": None, "requested_context": None}


class OntologyExtensionTests(unittest.TestCase):
    def setUp(self):
        self.folder = getattr(self, "output_folder", ROOT / "runs" / "_test_ontology_extension" / uuid4().hex)
        self.folder.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.folder)
        self.repo = ReviewRepository(self.folder / "review.sqlite3")
        self.repo.initialize()
        self.root = self.folder / "releases"
        self.schema = {"domain": "fixture", "classes": {"C": {"id": "C", "label": "device", "definition": "A device is a modeled controllable unit."}},
            "object_properties": {"R_old": {"id": "R_old", "label": "connects", "definition": "Connection between devices.", "domain": ["C"], "range": ["C"]}},
            "application_profile": {"profile_version": "project_json_v1", "constraint_semantics": "allowed_any", "auto_review_blocked": False, "reasoner": "not_run"}}
        self.base = ReleaseCatalog(self.root).publish(self.artifact(self.schema), approval={"status": "approved", "approved_by": "fixture", "reason": "fixture baseline"})
        self.index = build_document_index(split_markdown_text("Regulation is a device changing another device's operating state. A regulates B.", "doc", 600, 20))
        source_path = persist_document_index(self.index, self.folder / "document.json")
        self.repo.create_run({"run_id": "run", "mode": "shadow", "document_path": str(source_path), "source_index_hash": self.index["index_content_hash"],
            "ontology_path": str(self.base.snapshot_path), "ontology_hash": self.base.manifest["ontology_version"], "candidate_contract_version": "extraction_candidate_v4"})
        self.config = {"mode": "shadow", "planner": {"enabled": True, "strategy": "llm_react", "model": "mock_planner", "family": "mock_planner"},
            "consensus": {"committee_size": 3, "required_votes": 3, "minimum_distinct_families": 3},
            "budget": {"max_planner_steps": 6, "max_tool_calls": 8, "max_llm_calls_per_candidate": 24, "max_retries_per_request": 0, "total_timeout_seconds": 60},
            "ontology_extension": {"enabled": True, "auto_publish_enabled": False, "budget": {"per_call_timeout_seconds": 1, "total_timeout_seconds": 90, "max_retries_per_request": 0}}}
        self.candidate, self.source_result = self.source_candidate()
        self.extension_id = self.source_result["pending_extension_id"]
        self.voters = [{"id": v, "model_id": "ontology_model_" + v, "model_family": v,
            "client": MockLLMClient(ontology_vote, model_id="ontology_model_" + v, model_family=v)} for v in "abc"]

    def artifact(self, schema):
        return OntologyArtifact(copy.deepcopy(schema), {"method": "fixture"}, {}, {"errors": [], "unsupported_axioms": []},
                                copy.deepcopy(schema["application_profile"]), source_json=copy.deepcopy(schema))

    def source_candidate(self, ordinal=0):
        raw = {"head": "A", "tail": "B", "surface_relation": "regulates", "head_type_candidate": "C", "tail_type_candidate": "C",
            "evidence_quotes": ["A regulates B."], "qualifiers": {"polarity": "positive", "condition": None, "time_scope": None,
                "applicability": None, "modality": "asserted", "quantifier": None},
            "qualifier_status": {"polarity": "extracted", "condition": "not_stated", "time_scope": "not_stated", "applicability": "not_stated", "modality": "extracted", "quantifier": "not_stated"}}
        candidate = normalize_candidate(raw, index=self.index, scope=source_scope(self.index, self.index["chunks"][0]),
            ontology=self.base.ontology, release_binding=self.base.binding(), run_id="run", ordinal=ordinal, prompt_hash="fixture")
        self.repo.add_candidate(candidate)
        def vote(payload):
            eid = payload["allowed_evidence_ids"][0]
            known = payload["ontology_check"].get("relation_known")
            return {"verdict": "valid" if known else "uncertain", "source_support": "supported", "schema_status": "pass" if known else "unknown",
                "schema_issue": "none" if known else "not_covered", "schema_change_hint": None, "supporting_evidence_ids": [eid], "counter_evidence_ids": [],
                "reason_codes": [] if known else ["SCHEMA_GAP"], "checks": [{"check": c, "status": "unknown" if c == "ontology" and not known else "pass", "evidence_ids": [eid], "note": "MOCK"} for c in CHECK_NAMES],
                "requested_context": None, "suggested_revision": None}
        self.source_voters = [{"id": v, "model_id": "source_" + v, "model_family": v, "client": MockLLMClient(vote, model_id="source_" + v, model_family=v)} for v in "abc"]
        def plan(payload):
            if not payload["ontology_checked"]: return action("inspect_ontology", relation_id=None, surface_relation="regulates", issue="full ontology lookup")
            if not payload["current_validation"]: return action("run_validation")
            return action("request_ontology_extension", change_kind="add_object_property", description="Missing regulation relation", proposed_changes={"label": "regulates"}, evidence_ids=payload["selected_evidence_ids"][:1])
        result = ReviewOrchestrator(self.repo, OntologyGuard(load_snapshot(self.base.snapshot_path)), self.source_voters,
            config=self.config, planner_client=MockLLMClient(plan)).audit_candidate(candidate["candidate_id"], 1, self.index)
        self.assertEqual(result["workflow_state"], "awaiting_ontology_review", result)
        return candidate, result

    def plan(self, payload):
        history = payload["history"]
        if not history: return action("search_existing_schema", query="regulates regulation")
        if len(history) == 1: return action("retrieve_definition_evidence", query="regulation definition", scope="current_chunk", limit=3)
        if not payload["delta"]:
            return action("propose_schema_delta", operations=[{"kind": "add_object_property", "target": None,
                "changes": {"label": "regulates", "definition": "A device changes another device's operating state.", "domain": ["C"], "range": ["C"]},
                "evidence_ids": [e["evidence_id"] for e in payload["evidence_catalog"]]}])
        for key, tool in (("structure", "validate_schema_structure"), ("impact", "analyze_schema_impact"), ("competency", "run_competency_checks"), ("reasoner", "run_reasoner_checks")):
            if key not in payload["check_records"]: return action(tool)
        if not payload["current_validation"]: return action("run_ontology_validation")
        return action("finish_extension_review", stop_reason="fixture investigation complete")

    def review(self, planner=None, audit_id=None):
        self.planner = MockLLMClient(planner or self.plan, model_id="ontology_planner", model_family="planner")
        self.runtime = OntologyReviewRuntime(self.repo, self.root, self.voters, self.planner, self.config)
        result = self.runtime.run(self.extension_id, audit_id)
        self.audit = self.repo.ontology_audit(result["audit_id"])
        return result

    def publication_request(self):
        preview = OntologyPublisher(self.repo, self.root).preview(self.extension_id)
        return {"expected_row_version": preview["expected_row_version"], "proposal_digest": preview["proposal_digest"],
            "expected_base_release_id": preview["base_release_id"], "approved_by": "fixture_operator", "reason": "Offline fixture modeling review"}

    def test_real_planner_tools_and_fixed_independent_committee_in_shadow(self):
        result = self.review()
        self.assertEqual(result["recommendation"], "approve", result)
        self.assertEqual(result["task_status"], "needs_human")
        self.assertEqual(current_release_id(self.root), self.base.manifest["release_id"])
        self.assertEqual([len(v["client"].calls) for v in self.voters], [1, 1, 1])
        self.assertEqual(len(self.audit["votes"]), 3)
        self.assertTrue(all(v["task_type"] == "ontology_extension" for v in self.audit["votes"]))
        self.assertEqual(self.audit["proposals"][0]["check_records"]["reasoner"]["status"], "not_run")
        with self.repo.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM import_outbox").fetchone()[0], 0)

    def test_explicit_publish_rebinds_new_revision_and_queues_fresh_triple_review(self):
        self.review()
        result = OntologyPublisher(self.repo, self.root, self.config).publish(self.extension_id, self.publication_request())
        current = self.repo.get_candidate(self.candidate["candidate_id"])
        self.assertEqual(current["revision"], 2)
        self.assertEqual(current["revision_reason"], "ontology_rebind")
        self.assertEqual(current["workflow_state"], "reaudit_required")
        self.assertEqual(current["ontology_release_id"], result["ontology_release_id"])
        self.assertTrue(current["triple"]["relation_id"].startswith("R_"))
        self.assertEqual(current["triple"]["qualifiers"], self.candidate["triple"]["qualifiers"])
        self.assertIsNone(self.repo.latest_decision(current["candidate_id"]))
        self.assertEqual(len(result["reaudit_jobs"]), 1)
        self.assertEqual(load_release(self.base.path).ontology, self.base.ontology)
        self.assertFalse(result["old_votes_reused"])

    def test_one_timeout_does_not_shrink_three_seat_denominator(self):
        self.voters[-1]["client"] = MockLLMClient([TimeoutError("unknown request outcome")])
        result = self.review()
        self.assertEqual(result["recommendation"], "needs_human")
        c = self.runtime.state["validation"]["consensus"]
        self.assertEqual(c["committee_size"], 3)
        self.assertEqual(c["counts"]["approve"], 2)
        self.assertEqual(c["counts"]["error"], 1)

    def test_declared_aliases_of_one_model_are_not_independent_votes(self):
        for voter in self.voters: voter["model_identity"] = "same_backend"
        result = self.review()
        self.assertEqual(result["recommendation"], "needs_human")
        self.assertEqual(self.runtime.state["validation"]["consensus"]["counts"]["approve"], 1)

    def test_reasoner_required_cannot_be_satisfied_by_not_run(self):
        self.config["ontology_extension"]["require_reasoner"] = True
        result = self.review()
        self.assertEqual(result["recommendation"], "needs_human")
        self.assertFalse(self.runtime.state["validation"]["consensus"]["deterministic_checks_pass"])

    def test_publication_is_idempotent_and_never_rebinds_twice(self):
        self.review()
        publisher = OntologyPublisher(self.repo, self.root, self.config)
        request = self.publication_request()
        first = publisher.publish(self.extension_id, request)
        again = publisher.publish(self.extension_id, request)
        self.assertEqual(first, again)
        self.assertEqual(self.repo.get_candidate(self.candidate["candidate_id"])["revision"], 2)

    def test_new_delta_invalidates_old_round_and_stale_approval(self):
        result = self.review()
        old = self.audit["proposals"][0]
        old_votes = self.audit["votes"]
        operations = copy.deepcopy(old["operations"])
        operations[0]["target"] = None
        operations[0]["changes"]["definition"] += " with explicitly bounded scope."
        observed = self.runtime.tools.propose_schema_delta(operations)
        self.assertEqual(observed["delta_revision"], 2)
        self.assertIsNone(self.runtime.state["validation"])
        with self.assertRaises((ValueError, ConflictError)):
            OntologyPublisher(self.repo, self.root).preview(self.extension_id)
        for tool in (self.runtime.tools.validate_schema_structure, self.runtime.tools.analyze_schema_impact, self.runtime.tools.run_competency_checks): tool()
        changed = self.runtime.freeze_proposal(self.runtime.state)
        changed["round_id"] = 2
        changed["proposal_digest"] = proposal_digest(changed)
        self.assertEqual(consensus(old_votes, changed)["counts"]["approve"], 0)
        self.assertNotEqual(changed["proposal_digest"], result["selected_proposal_digest"])

    def test_human_modified_candidate_is_never_rebound_by_old_task(self):
        self.review()
        request = self.publication_request()
        current = self.repo.get_candidate(self.candidate["candidate_id"])
        self.repo.revise_candidate(current["candidate_id"], current["row_version"], {"triple": {"tail": "C"}})
        with self.assertRaises(AgentStopped): OntologyPublisher(self.repo, self.root).publish(self.extension_id, request)
        current = self.repo.get_candidate(current["candidate_id"])
        self.assertEqual(current["triple"]["tail"], "C")
        self.assertEqual(current_release_id(self.root), self.base.manifest["release_id"])

    def test_release_head_race_requires_rebase_and_fresh_review(self):
        self.review()
        request = self.publication_request()
        other = copy.deepcopy(self.schema)
        other["classes"]["C_other"] = {"id": "C_other", "label": "other device", "definition": "Another modeled device."}
        newer = ReleaseCatalog(self.root).publish(self.artifact(other), approval={"status": "approved", "approved_by": "fixture", "reason": "concurrent fixture"}, expected_base_release_id=self.base.manifest["release_id"])
        publisher = OntologyPublisher(self.repo, self.root)
        with self.assertRaises(ReleaseConflictError): publisher.publish(self.extension_id, request)
        task = self.repo.extension_status(self.extension_id)
        self.assertEqual(task["status"], "needs_rebase")
        rebased = publisher.rebase(self.extension_id, task["row_version"], newer.manifest["release_id"])
        self.assertNotIn("latest_audit_id", rebased)
        self.assertEqual(rebased["base_release_id"], newer.manifest["release_id"])
        self.assertEqual(len(self.audit["votes"]), 3)
        with self.assertRaises(ValueError): publisher.preview(self.extension_id)
        result = self.review()
        self.assertEqual(result["recommendation"], "approve")
        self.assertEqual(self.audit["proposals"][0]["base_release_id"], newer.manifest["release_id"])
        self.assertEqual([len(v["client"].calls) for v in self.voters], [2, 2, 2])

    def test_crash_after_release_commit_recovers_publication_intent(self):
        self.review()
        publisher = OntologyPublisher(self.repo, self.root, self.config)
        request = self.publication_request()
        with patch.object(publisher, "_wake", side_effect=RuntimeError("crash after catalog commit")):
            with self.assertRaises(RuntimeError): publisher.publish(self.extension_id, request)
        self.assertNotEqual(current_release_id(self.root), self.base.manifest["release_id"])
        self.assertEqual(self.repo.get_candidate(self.candidate["candidate_id"])["revision"], 1)
        recovered = publisher.publish(self.extension_id, request)
        self.assertEqual(self.repo.get_candidate(self.candidate["candidate_id"])["revision"], 2)
        self.assertEqual(len(ReleaseCatalog(self.root).history()), 2)
        self.assertEqual(len(recovered["reaudit_jobs"]), 1)

    def test_completed_ontology_votes_recover_without_repeating_model_calls(self):
        from ontology_review.validators import judge_independently
        def crash(*args, **kwargs):
            judge_independently(*args, **kwargs)
            raise KeyboardInterrupt()
        with patch("ontology_review.runtime.judge_independently", side_effect=crash):
            with self.assertRaises(KeyboardInterrupt): self.review(audit_id="recover_audit")
        self.assertEqual([len(v["client"].calls) for v in self.voters], [1, 1, 1])
        result = self.runtime.run(self.extension_id, "recover_audit")
        self.assertEqual(result["recommendation"], "approve")
        self.assertEqual([len(v["client"].calls) for v in self.voters], [1, 1, 1])

    def test_triple_ticket_cannot_be_used_as_ontology_approval(self):
        self.review()
        proposal = self.audit["proposals"][0]
        votes = [{**v, "task_type": "triple_review"} for v in self.audit["votes"]]
        self.assertEqual(consensus(votes, proposal)["counts"]["approve"], 0)
        value = ontology_vote({"allowed_evidence_ids": ["source"]})
        value["source_support"] = "supported"
        self.assertTrue(validate_response(value))

    def test_shared_task_has_two_subscriptions_and_charges_both_cases(self):
        second, second_result = self.source_candidate(ordinal=1)
        self.assertEqual(second_result["pending_extension_id"], self.extension_id)
        result = self.review()
        accounts = result["budget"]["case_accounts"]
        self.assertEqual(len(accounts), 2)
        self.assertTrue(all(c["usage"]["extension_requests"] == 1 and c["usage"]["llm_calls"] > 6 for c in accounts))
        publication = OntologyPublisher(self.repo, self.root).publish(self.extension_id, self.publication_request())
        self.assertEqual(len(publication["rebound"]), 2)
        self.assertEqual(self.repo.get_candidate(second["candidate_id"])["revision"], 2)

    def test_case_call_budget_cannot_reset_by_switching_agent(self):
        case_id = self.repo.ensure_case(self.candidate["candidate_id"])
        account = self.repo.case_status(case_id)
        remaining = account["limits"]["llm_calls"] - account["usage"]["llm_calls"]
        self.repo.charge_cases([case_id], {"llm_calls": remaining}, "consume_budget", "fixture", "fixture")
        result = self.review()
        self.assertEqual(result["recommendation"], "needs_human")
        self.assertEqual([len(v["client"].calls) for v in self.voters], [0, 0, 0])
        self.assertEqual(self.repo.case_status(case_id)["usage"]["llm_calls"], account["limits"]["llm_calls"])

    def test_duplicate_schema_and_unexecuted_fields_are_not_silently_added(self):
        op = {"kind": "add_class", "target": None, "changes": {"label": "device", "definition": "Duplicate."}, "evidence_ids": ["ev"]}
        with self.assertRaisesRegex(ValueError, "existing label"):
            apply_delta(self.base, [op], {"ev"})
        op["changes"].update(label="new", cardinality=1)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            apply_delta(self.base, [op], {"ev"})

    def test_unknown_natural_language_cq_is_not_marked_executed(self):
        self.review()
        original = self.runtime.base.path / "competency_questions.json"
        original.write_text(json.dumps(["Is every regulation constraint semantically justified?"]), encoding="utf-8")
        result = self.runtime.tools.run_competency_checks()
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(result["natural_language_questions_not_falsely_marked_executed"])

    def test_automatic_publication_is_blocked_in_shadow(self):
        self.review()
        with self.assertRaisesRegex(ValueError, "automatic"):
            OntologyPublisher(self.repo, self.root, self.config).publish(self.extension_id, self.publication_request(), automatic=True)

    def test_rebound_candidate_uses_new_proposal_and_never_old_votes(self):
        self.review()
        release = OntologyPublisher(self.repo, self.root, self.config).publish(self.extension_id, self.publication_request())
        def finish_plan(payload):
            if not payload["ontology_checked"]: return action("inspect_ontology", relation_id=payload["candidate"]["relation_id"], issue="new frozen release")
            if not payload["current_validation"]: return action("run_validation")
            return action("finish_review", stop_reason="new-release review", unresolved_issues=[], evidence_ids=[])
        result = ReviewOrchestrator(self.repo, OntologyGuard(load_snapshot(self.root / release["ontology_release_id"] / "ontology_snapshot.json")),
            self.source_voters, config=self.config, planner_client=MockLLMClient(finish_plan)).audit_candidate(self.candidate["candidate_id"], 2, self.index)
        audit = self.repo.get_audit(self.candidate["candidate_id"], result["audit_id"])
        self.assertEqual(result["source_support_result"], "supported")
        self.assertEqual(result["admission_consensus"]["result"], "accept_consensus")
        self.assertEqual(audit["rounds"][0]["ontology_release_id"], release["ontology_release_id"])
        self.assertTrue(all(v["object_revision"] == 2 for v in audit["votes"]))
        self.assertNotEqual(result["selected_proposal_digest"], self.source_result["selected_proposal_digest"])

    def test_configuration_aliases_are_unified_and_conflicts_rejected(self):
        from review_agent.configuration import normalize_config
        normalized = normalize_config({"agents": {"ontology_extension": {"enabled": True}}, "committees": {"ontology": {"size": 3, "required_votes": 3}}})
        self.assertTrue(normalized["ontology_extension"]["enabled"])
        self.assertEqual(normalized["ontology_extension"]["consensus"]["committee_size"], 3)
        self.assertNotIn("agents", normalized)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            normalize_config({"mode": "shadow", "routing": {"mode": "active"}})

    def test_high_impact_changes_require_explicit_operator_confirmation(self):
        def plan(payload):
            result = self.plan(payload)
            if result["action"] == "propose_schema_delta":
                result["arguments"]["operations"].append({"kind": "revise_definition", "target": "C",
                    "changes": {"definition": "A device is a controllable unit in this fixture domain."},
                    "evidence_ids": [e["evidence_id"] for e in payload["evidence_catalog"]]})
            return result
        self.review(plan)
        publisher = OntologyPublisher(self.repo, self.root)
        request = self.publication_request()
        with self.assertRaisesRegex(ValueError, "high-impact"): publisher.publish(self.extension_id, request)
        result = publisher.publish(self.extension_id, {**request, "confirm_high_impact": True})
        self.assertIn(result["ontology_version"], self.repo.get_candidate(self.candidate["candidate_id"])["fact_namespace"])

    def test_human_revision_invalidates_old_approval_and_requires_new_votes(self):
        self.review()
        from review_agent.service import ReviewService
        from ontology_review.service import OntologyReviewService
        service = OntologyReviewService(ReviewService(self.repo, config={**self.config, "ontology_release_root": str(self.root)}))
        task = self.repo.extension_status(self.extension_id)
        service.decide(self.extension_id, {"action": "revise", "reason": "Recheck the modeling definition.", "expected_row_version": task["row_version"]})
        self.assertIsNone(service.detail(self.extension_id)["publication_preview"])
        unchanged = self.review()
        self.assertEqual(unchanged["recommendation"], "needs_human")
        self.assertEqual([len(v["client"].calls) for v in self.voters], [1, 1, 1])
        def revise(payload):
            if len(payload["history"]) == 2:
                operations = copy.deepcopy(payload["delta"]["operations"])
                operations[0]["target"] = None
                operations[0]["changes"]["definition"] += " A revised, explicitly scoped definition."
                return action("propose_schema_delta", operations=operations)
            return self.plan(payload)
        self.review(revise)
        self.assertEqual([len(v["client"].calls) for v in self.voters], [2, 2, 2])
        self.assertTrue(self.audit["proposals"][0]["human_review_context"])

    def test_counterevidence_requires_explicit_current_proposal_recheck(self):
        self.review()
        proposal = copy.deepcopy(self.audit["proposals"][0])
        proposal["prior_counter_evidence_ids"] = ["counter_missing"]
        from ontology_review.validators import response_errors
        value = ontology_vote({"allowed_evidence_ids": [proposal["evidence"][0]["evidence_id"]]})
        self.assertTrue(any("counterevidence" in e for e in response_errors(value, proposal)))

    def test_malformed_contract_is_one_error_and_does_not_interrupt_other_voters(self):
        self.voters[0]["client"] = MockLLMClient({"default": {"decision": "approve", "checks": []}})
        result = self.review()
        self.assertEqual(result["recommendation"], "needs_human")
        self.assertEqual(self.runtime.state["validation"]["consensus"]["counts"]["approve"], 2)
        self.assertEqual(self.runtime.state["validation"]["consensus"]["counts"]["error"], 1)

    def test_reasoner_adapter_requires_exact_diff_and_source_binding(self):
        self.review()
        self.runtime.options["reasoner"] = {"command": ["fixture-reasoner"], "timeout_seconds": 1}
        with patch("subprocess.run") as run:
            def execute(*args, **kwargs):
                payload = json.loads(kwargs["input"])
                return type("Result", (), {"returncode": 0, "stdout": json.dumps({**{k: payload[k] for k in ("base_release_id", "delta_hash", "ontology_version", "profile_hash")}, "status": "pass", "consistent": True, "executed": True, "engine": "MOCK reasoner"})})()
            run.side_effect = execute
            record = self.runtime.tools.run_reasoner_checks()
            self.assertEqual(record["status"], "pass")
            self.assertFalse(run.call_args.kwargs["shell"])
            run.return_value = type("Result", (), {"returncode": 0, "stdout": json.dumps({"status": "pass", "consistent": True})})()
            run.side_effect = None
            self.assertEqual(self.runtime.tools.run_reasoner_checks()["status"], "unknown")

    def test_reasoner_failure_never_becomes_a_pass(self):
        import subprocess
        self.review()
        self.runtime.options["reasoner"] = {"command": ["fixture-reasoner"]}
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("fixture", 1)):
            self.assertEqual(self.runtime.tools.run_reasoner_checks()["status"], "unknown")

    def test_migration_v4_to_v5_keeps_sources_and_creates_a_restore_backup(self):
        before = self.repo.get_candidate(self.candidate["candidate_id"])
        with self.repo.connect() as connection:
            for name in ("ontology_votes", "ontology_proposals", "ontology_steps", "ontology_audits", "ontology_publish_outbox", "case_cost_events", "review_cases"):
                connection.execute("DROP TABLE " + name)
            connection.execute("ALTER TABLE ontology_extension_tasks DROP COLUMN row_version")
            connection.execute("DELETE FROM schema_migrations WHERE migration_version=5")
        self.repo.initialize()
        self.repo.initialize()
        backups = list((self.folder / "migration_backups").glob("*.pre_v5.*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.repo.get_candidate(self.candidate["candidate_id"]), before)
        with closing(sqlite3.connect(backups[0])) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='ontology_audits'").fetchone())
        case_id = self.repo.ensure_case(before["candidate_id"])
        self.assertEqual(self.repo.case_status(case_id)["usage"]["llm_calls"], 6)
        self.assertEqual(self.repo.case_status(case_id)["usage"]["extension_requests"], 1)

    def test_model_suggestions_cannot_create_arbitrary_executable_fields(self):
        op = {"kind": "revise_constraint", "target": "C", "changes": {"domain": ["C"]}, "evidence_ids": ["e"]}
        with self.assertRaisesRegex(ValueError, "unapproved"):
            apply_delta(self.base, [op], {"e"})

    def test_alias_collision_fails_program_structure_check(self):
        self.review()
        from ontology_review.delta import check_structure
        schema = copy.deepcopy(self.runtime.state["delta"]["merged_schema"])
        identity = self.runtime.state["delta"]["operations"][0]["target"]
        schema["object_properties"][identity]["aliases"] = ["connects"]
        report = check_structure(schema, self.base.application_profile, self.base.ontology)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any(e["code"] == "PROPERTY_ALIAS_CONFLICT" for e in report["errors"]))

    def test_request_job_is_deduplicated_per_extension(self):
        with self.repo.connect() as connection:
            first = connection.execute("SELECT job_id FROM jobs WHERE job_type='ontology_extension'").fetchone()[0]
        again = self.repo.create_job("ontology_extension", {"extension_id": self.extension_id, "audit_id": "different_audit"})
        self.assertEqual(again["job_id"], first)

    def test_completed_ontology_audit_is_reused_without_new_requests(self):
        result = self.review()
        again = self.runtime.run(self.extension_id, result["audit_id"])
        self.assertTrue(again["reused_completed"])
        self.assertEqual([len(v["client"].calls) for v in self.voters], [1, 1, 1])

    def test_unchanged_diff_cannot_reroll_valid_models_in_a_new_audit(self):
        self.review()
        result = self.review()
        self.assertEqual(result["recommendation"], "approve")
        self.assertEqual([len(v["client"].calls) for v in self.voters], [1, 1, 1])
        self.assertTrue(all(v.get("cached") for v in self.audit["votes"]))

    def test_billing_pause_is_visible_and_retries_only_the_failed_model(self):
        self.voters[0]["client"] = MockLLMClient([RuntimeError("Error code: 402 - Insufficient Balance")])
        result = self.review()
        self.assertTrue(result["paused"])
        self.voters[0]["client"] = MockLLMClient(ontology_vote)
        second = self.review()
        self.assertEqual(second["recommendation"], "approve")
        self.assertEqual([len(v["client"].calls) for v in self.voters], [1, 1, 1])

    def test_active_low_risk_auto_publication_requires_explicit_policy_enablement(self):
        self.config["mode"] = "active"
        self.config["ontology_extension"]["auto_publish_enabled"] = True
        result = self.review()
        self.assertEqual(result["task_status"], "approved")
        publication = OntologyPublisher(self.repo, self.root, self.config).publish(self.extension_id, self.publication_request(), automatic=True)
        self.assertNotEqual(publication["ontology_release_id"], self.base.manifest["release_id"])

    def test_new_class_cycle_fails_structural_check(self):
        from ontology_review.delta import check_structure
        delta = apply_delta(self.base, [{"kind": "add_class", "target": None, "changes": {"label": "cycle", "definition": "Fixture invalid hierarchy.", "parents": ["cycle"]}, "evidence_ids": ["e"]}], {"e"})
        self.assertEqual(check_structure(delta["merged_schema"], self.base.application_profile, self.base.ontology)["status"], "fail")

    def test_owned_worker_timeout_returns_and_marks_failure_without_hanging(self):
        from review_agent.service import ReviewService
        from ontology_review.service import OntologyReviewService
        config = copy.deepcopy(self.config)
        config["ontology_release_root"] = str(self.root)
        config["ontology_extension"]["budget"]["total_timeout_seconds"] = .15
        service = OntologyReviewService(ReviewService(self.repo, config=config))
        began = time.monotonic()
        result = service.execute({"extension_id": self.extension_id, "audit_id": "blocked_fixture"}, _worker_target=blocked_ontology_worker)
        self.assertTrue(result["failed"])
        self.assertEqual(result["stop_reason"], "hard_worker_timeout")
        self.assertLess(time.monotonic() - began, 3)

    def test_old_counter_cannot_be_cleared_by_repeating_the_same_diff(self):
        def counter(payload):
            value = ontology_vote(payload)
            eid = value["supporting_evidence_ids"][0]
            value.update(decision="reject", semantic_status="fail", supporting_evidence_ids=[], counter_evidence_ids=[eid], reason_codes=["SCOPE_UNCONFIRMED"])
            return value
        self.voters[0]["client"] = MockLLMClient(counter)
        first = self.review()
        self.assertEqual(first["recommendation"], "needs_human")
        self.voters[0]["client"] = MockLLMClient(ontology_vote)
        same = self.review()
        self.assertEqual(same["recommendation"], "needs_human")
        self.assertEqual(len(self.voters[0]["client"].calls), 0)
        def revised(payload):
            if len(payload["history"]) == 2:
                operations = copy.deepcopy(payload["delta"]["operations"])
                operations[0]["target"] = None
                operations[0]["changes"]["definition"] += " Only the explicitly sourced scope is asserted."
                return action("propose_schema_delta", operations=operations)
            return self.plan(payload)
        resolved = self.review(revised)
        self.assertEqual(resolved["recommendation"], "approve")
        self.assertTrue(self.audit["proposals"][0]["prior_counter_evidence_ids"])
        self.assertTrue(any(v["counter_evidence_ids"] for v in self.repo.ontology_audit(first["audit_id"])["votes"]))

    def test_revocation_between_release_commit_and_wakeup_keeps_human_change(self):
        self.review()
        publisher = OntologyPublisher(self.repo, self.root)
        wake = publisher._wake
        def human_change(*args):
            candidate = self.repo.get_candidate(self.candidate["candidate_id"])
            self.repo.revise_candidate(candidate["candidate_id"], candidate["row_version"], {"triple": {"tail": "human_changed"}})
            return wake(*args)
        with patch.object(publisher, "_wake", side_effect=human_change):
            result = publisher.publish(self.extension_id, self.publication_request())
        self.assertFalse(result["rebound"])
        self.assertTrue(result["skipped"])
        self.assertEqual(self.repo.get_candidate(self.candidate["candidate_id"])["triple"]["tail"], "human_changed")

    def test_owl_source_is_not_silently_replaced_with_project_json(self):
        self.review()
        base = copy.deepcopy(self.base)
        base.manifest["artifacts"]["ontology.ttl"] = "fixture"
        with self.assertRaisesRegex(ValueError, "OWL source"):
            OntologyPublisher(self.repo, self.root).artifact(self.audit["proposals"][0], base)

    def test_unexecuted_custom_datatype_fails_profile_validation(self):
        from ontology_review.delta import check_structure
        delta = apply_delta(self.base, [{"kind": "add_data_property", "target": None,
            "changes": {"label": "fictional value", "definition": "Fixture value.", "domain": ["C"], "datatype": "https://unsupported.invalid#string"}, "evidence_ids": ["e"]}], {"e"})
        report = check_structure(delta["merged_schema"], self.base.application_profile, self.base.ontology)
        self.assertTrue(any(e["code"] == "UNSUPPORTED_DATATYPE" for e in report["errors"]))

    def test_pre_v5_checkpoint_refund_does_not_subtract_historical_case_cost(self):
        case_id = self.repo.ensure_case(self.candidate["candidate_id"])
        before = self.repo.case_status(case_id)["usage"]["llm_calls"]
        saved = {"usage": {"llm_calls": 10}, "deadline_epoch": time.time() + 60}
        budget = CaseBudget({"max_llm_calls_per_candidate": 24}, self.repo, [case_id], task_type="triple_review", task_id="legacy_checkpoint", saved=saved)
        budget.refund_calls(4)
        self.assertEqual(budget.usage["llm_calls"], 6)
        self.assertEqual(self.repo.case_status(case_id)["usage"]["llm_calls"], before)

    def test_real_sdk_and_killable_worker_use_separate_ontology_contract(self):
        import os
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from review_agent.service import ReviewService
        from ontology_review.service import OntologyReviewService
        requests = []
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                payload = json.loads(body["messages"][-1]["content"])
                requests.append({"model": body["model"], "role": "validator" if "output_contract" in payload else "planner", "task_type": payload.get("task_type")})
                value = ontology_vote(payload) if "output_contract" in payload else fixture.plan(payload)
                result = {"id": "fixture_response", "object": "chat.completion", "created": 0, "model": body["model"],
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(value)}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
                data = json.dumps(result).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        config = copy.deepcopy(self.config)
        config["ontology_release_root"] = str(self.root)
        config["planner"].update(api_key_env="ONTOLOGY_FIXTURE_KEY", base_url_env="ONTOLOGY_FIXTURE_URL")
        config["validators"] = [{"id": v, "model": "http_" + v, "family": v, "api_key_env": "ONTOLOGY_FIXTURE_KEY", "base_url_env": "ONTOLOGY_FIXTURE_URL"} for v in "abc"]
        config["ontology_extension"]["budget"]["per_call_timeout_seconds"] = 10
        service = OntologyReviewService(ReviewService(self.repo, config=config))
        with patch.dict(os.environ, {"ONTOLOGY_FIXTURE_KEY": "fixture", "ONTOLOGY_FIXTURE_URL": f"http://127.0.0.1:{server.server_port}/v1"}):
            result = service.execute({"extension_id": self.extension_id, "audit_id": "sdk_ontology"})
        self.assertEqual(result.get("recommendation"), "approve", result)
        self.assertEqual(len([r for r in requests if r["role"] == "validator"]), 3)
        self.assertTrue(all(r["task_type"] == "ontology_extension" for r in requests))
        audit = self.repo.ontology_audit("sdk_ontology")
        self.assertEqual({v["returned_model_id"] for v in audit["votes"]}, {"http_a", "http_b", "http_c"})


if __name__ == "__main__": unittest.main()
