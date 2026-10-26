"""Safety, observation-driven actions, costs, cancellation and crash recovery."""

import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from confidence.risk_policy import RiskPolicy
from document.hierarchy_index import build_document_index
from review_agent.cli import _document_index
from review_agent.contracts import FEATURE_SCHEMA_VERSION
from review_agent.llm_clients import MockLLMClient, LLMResponse
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.planner_contracts import ActionError, validate_action
from review_agent.repository import ReviewRepository
from review_agent.response_contract import CHECK_NAMES
from review_agent.tools import AgentTools


def action(name, **arguments):
    return {"action": name, "arguments": arguments, "target_issue": "核查当前候选", "rationale_summary": "根据实际工具观察选择下一步"}


def finish():
    return action("finish_review", stop_reason="investigation_finished", unresolved_issues=[], evidence_ids=[])


def ontology():
    return action("inspect_ontology", relation_id="R_measures", issue="核对类型和关系约束")


def valid_response(payload, invalid=False):
    eid = payload["allowed_evidence_ids"][0]
    return {"verdict": "invalid" if invalid else "valid", "source_support": "contradicted" if invalid else "supported",
        "schema_status": "pass", "supporting_evidence_ids": [] if invalid else [eid],
        "counter_evidence_ids": [eid] if invalid else [], "reason_codes": ["SOURCE_CONTRADICTS"] if invalid else [],
        "checks": [{"check": k, "status": "pass" if not invalid or k == "ontology" else "unknown", "evidence_ids": [eid], "note": "核查原文"} for k in CHECK_NAMES],
        "requested_context": None, "suggested_revision": None}


class PlannerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "runs")
        self.addCleanup(self.tmp.cleanup)
        self.repo = ReviewRepository(Path(self.tmp.name) / "audit.sqlite3")
        self.repo.initialize()
        self.repo.create_run({"run_id": "run", "mode": "shadow"})
        fixture = json.loads((Path(__file__).parent / "fixtures/review_agent/sensor_demo.json").read_text(encoding="utf-8"))
        self.index = _document_index("doc", "传感器A可以测量温度。")
        self.guard = OntologyGuard(load_snapshot(fixture["ontology"]))
        self.candidate = {"candidate_id": "c", "run_id": "run", "revision": 1, "document_id": "doc",
            "document_version": self.index["document_version"], "ontology_version": self.guard.snapshot.ontology_version,
            "source_chunk_id": self.index["chunks"][0]["chunk_id"], "triple": fixture["scenarios"][0]["triple"],
            "extraction_quote": fixture["scenarios"][0]["quote"]}
        self.repo.add_candidate(self.candidate)
        self.clients = [MockLLMClient(valid_response, model_id="model_" + v, model_family=v) for v in "abcd"]
        self.validators = [{"id": v, "model_id": c.model_id, "model_family": c.model_family, "client": c}
                           for v, c in zip("abcd", self.clients)]
        self.config = {"mode": "shadow", "planner": {"enabled": True, "strategy": "llm_react",
            "model": "planner-model", "family": "planner-family", "fallback": "manual"},
            "consensus": {"committee_size": 4, "required_votes": 3, "minimum_distinct_families": 3},
            "budget": {"max_planner_steps": 8, "max_validation_rounds": 2, "max_llm_calls_per_candidate": 24,
                "max_tool_calls": 9, "max_retries_per_request": 0, "per_call_timeout_seconds": 1,
                "total_timeout_seconds": 30}}

    def runner(self, script, config=None, **kwargs):
        self.planner = MockLLMClient(script, model_id="planner-model", model_family="planner-family")
        return ReviewOrchestrator(self.repo, self.guard, self.validators, config=config or self.config,
                                  planner_client=self.planner, **kwargs)

    def run_script(self, script, config=None, **kwargs):
        result = self.runner(script, config=config, **kwargs).audit_candidate("c", 1, self.index)
        self.audit = self.repo.get_audit("c", result["audit_id"])
        return result

    def active_config(self):
        config = copy.deepcopy(self.config)
        config.update(mode="active", routing={"auto_accept_enabled": True, "semantic_auto_reject_enabled": True})
        return config

    def ready_policy(self):
        return RiskPolicy({"enabled": True, "validated": True, "auto_accept_enabled": True,
                           "semantic_auto_reject_enabled": True, "accept_threshold": .9, "reject_threshold": .1})

    def ready_calibrator(self):
        return SimpleNamespace(predict_if_compatible=lambda *_: SimpleNamespace(probability=.99,
            compatibility=True, status="ok", model_version="test"))

    def test_model_replans_after_real_observations_and_calls_whole_committee(self):
        def plan(payload):
            history = payload["history"]
            if not history:
                return ontology()
            if history[-1]["tool"] == "inspect_ontology":
                self.assertEqual(history[-1]["check"]["status"], "pass")
                return action("run_validation")
            self.assertEqual(history[-1]["validation"]["consensus"]["valid_votes"], 4)
            return finish()
        result = self.run_script(plan)
        self.assertEqual(result["executed_strategy"], "llm_react")
        self.assertEqual(result["budget"]["llm_calls"], 7)
        self.assertEqual(result["budget"]["planner_steps"], 3)
        self.assertEqual(len(self.audit["planner_steps"]), 3)
        self.assertEqual(len(self.audit["tool_calls"]), 3)
        self.assertEqual(len(self.audit["votes"]), 4)
        self.assertTrue(all(len(c.calls) == 1 for c in self.clients))
        for client in self.clients:
            self.assertNotIn("rationale_summary", json.dumps(client.calls, ensure_ascii=False))
            self.assertNotIn("previous_votes", json.dumps(client.calls, ensure_ascii=False))
        self.assertEqual(self.audit["agent_state"]["phase"], "finished")
        self.assertEqual(self.audit["planner_steps"][0]["attempts"][0]["input_state"]["budget"]["usage"]["llm_calls"], 1)

    def test_finish_without_validation_cannot_auto_accept(self):
        result = self.run_script([finish()], config=self.active_config(),
            risk_policy=self.ready_policy(), calibrator=self.ready_calibrator())
        self.assertEqual(result["decision"], "manual_review")
        self.assertIn("CURRENT_VALIDATION_MISSING", result["reason_codes"])
        self.assertIn("ONTOLOGY_CHECK_REQUIRED", result["reason_codes"])
        self.assertEqual(sum(len(c.calls) for c in self.clients), 0)

    def test_intentional_human_handoff_is_reusable_completed_work(self):
        from review_agent.batch import versions_for
        result = self.run_script([ontology(), finish()])
        members = [{k: v[k] for k in ("id", "model_id", "model_family")} for v in self.validators]
        versions = versions_for(self.config)
        self.assertEqual(result["decision"], "manual_review")
        self.assertTrue(self.repo.has_healthy_audit("c", 1, versions, members))
        self.assertFalse(self.repo.has_healthy_audit("c", 1, {**versions, "prompt_version": "changed"}, members))
        changed = [*members[:-1], {**members[-1], "model_id": "different-model"}]
        self.assertFalse(self.repo.has_healthy_audit("c", 1, versions, changed))

    def test_planner_connection_failure_is_not_reusable_human_handoff(self):
        from review_agent.batch import versions_for
        result = self.run_script([RuntimeError("HTTP 503 unavailable")])
        members = [{k: v[k] for k in ("id", "model_id", "model_family")} for v in self.validators]
        self.assertEqual(result["executed_strategy"], "manual_degraded")
        self.assertFalse(self.repo.has_healthy_audit("c", 1, versions_for(self.config), members))

    def test_ready_current_validation_can_route_only_through_system_policy(self):
        result = self.run_script([ontology(), action("run_validation"), finish()], config=self.active_config(),
            risk_policy=self.ready_policy(), calibrator=self.ready_calibrator())
        self.assertEqual(result["decision"], "auto_accept")
        self.assertEqual(len(self.repo.list_outbox(run_id="run")), 1)
        self.assertEqual(result["consensus"]["committee_size"], 4)

    def test_repeated_same_proposal_uses_cached_votes_not_a_reroll(self):
        result = self.run_script([ontology(), action("run_validation"), action("run_validation"), finish()])
        self.assertEqual(sum(len(c.calls) for c in self.clients), 4)
        self.assertEqual(result["budget"]["validation_rounds"], 1)
        self.assertEqual(self.audit["tool_calls"][2]["status"], "cached")

    def test_new_audit_retries_only_failed_model_and_refunds_cached_reservations(self):
        def recover(payload):
            if len(self.clients[-1].calls) == 1:
                raise RuntimeError("HTTP 503 unavailable")
            return valid_response(payload)
        self.clients[-1]._callable = recover
        first = self.run_script([ontology(), action("run_validation"), finish()])
        second = self.run_script([ontology(), action("run_validation"), finish()])
        self.assertNotEqual(first["audit_id"], second["audit_id"])
        self.assertEqual([len(c.calls) for c in self.clients], [1, 1, 1, 2])
        self.assertEqual(second["budget"]["llm_calls"], 4)  # Three planner calls and one validator.
        self.assertEqual(sum(v.get("cached", False) for v in self.audit["votes"]), 3)
        self.assertTrue(all(v["response_status"] == "ok" for v in self.audit["votes"]))
        with self.repo.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(1) FROM validator_attempts WHERE audit_id=?",
                                               (second["audit_id"],)).fetchone()[0], 1)

    def test_new_evidence_still_revalidates_all_independent_models(self):
        extra = _document_index("doc", "传感器A仅在条件X下测量温度。")
        chunk = extra["chunks"][0]
        chunk.update(document_version=self.index["document_version"], chunk_id="extra", section_id="other")
        self.index = build_document_index([self.index["chunks"][0], chunk])
        result = self.run_script([ontology(), action("run_validation"),
            action("search_evidence", scope="other_section", query="条件X", purpose="check_qualifiers", limit=2),
            action("run_validation"), finish()])
        self.assertEqual(result["budget"]["validation_rounds"], 2)
        self.assertEqual([len(c.calls) for c in self.clients], [2, 2, 2, 2])
        self.assertFalse(any(v.get("cached") for v in self.audit["votes"]))

    def test_planner_timeout_is_not_automatically_retried(self):
        config = copy.deepcopy(self.config)
        config["budget"]["max_retries_per_request"] = 3
        result = self.run_script([TimeoutError("planner result unknown")], config=config)
        self.assertEqual(len(self.planner.calls), 1)
        self.assertEqual(result["budget"]["llm_calls"], 1)
        self.assertEqual(result["executed_strategy"], "manual_degraded")

    def test_empty_search_observation_changes_next_scope(self):
        def plan(payload):
            history = payload["history"]
            if not history:
                return action("search_evidence", scope="current_chunk", query="条件X", purpose="check_qualifiers", limit=2)
            if len(history) == 1:
                self.assertEqual(history[-1]["status"], "empty")
                return action("search_evidence", scope="other_section", query="适用条件", purpose="find_definition", limit=2)
            return finish()
        self.run_script(plan)
        self.assertEqual([x["arguments"]["scope"] for x in self.audit["tool_calls"][:2]], ["current_chunk", "other_section"])

    def test_new_evidence_invalidates_old_votes_and_retains_archive(self):
        extra = _document_index("doc", "传感器A仅在条件X下测量温度。")
        chunk = extra["chunks"][0]
        chunk.update(document_version=self.index["document_version"], chunk_id="extra", section_id="other")
        self.index = build_document_index([self.index["chunks"][0], chunk])
        result = self.run_script([ontology(), action("run_validation"),
            action("search_evidence", scope="other_section", query="条件X", purpose="check_qualifiers", limit=2), finish()],
            config=self.active_config(), risk_policy=self.ready_policy(), calibrator=self.ready_calibrator())
        self.assertEqual(result["decision"], "manual_review")
        self.assertIn("VERSION_STALE", result["reason_codes"])
        self.assertGreater(len(self.audit["agent_state"]["state"]["archive"]), 1)
        self.assertEqual(sum(len(c.calls) for c in self.clients), 4)

    def test_counterevidence_is_preserved_and_cannot_be_cleared_by_finish(self):
        self.clients[-1]._callable = lambda p: valid_response(p, invalid=True)
        result = self.run_script([ontology(), action("run_validation"), finish()], config=self.active_config(),
            risk_policy=self.ready_policy(), calibrator=self.ready_calibrator())
        self.assertEqual(result["decision"], "manual_review")
        self.assertIn("UNRESOLVED_COUNTEREVIDENCE", result["reason_codes"])
        self.assertTrue(self.audit["agent_state"]["state"]["protected_ids"])
        self.assertEqual(len(self.audit["agent_state"]["state"]["archive"]), 1)

    def test_illegal_tool_can_be_corrected_and_never_runs(self):
        bad = action("execute_sql", query="DELETE FROM candidates")
        self.run_script([bad, ontology(), finish()])
        self.assertEqual(self.audit["planner_steps"][0]["status"], "failed")
        self.assertEqual(self.planner.calls[1]["history"][-1]["status"], "planner_error")
        self.assertTrue(all(t["tool"] != "execute_sql" for t in self.audit["tool_calls"]))

    def test_unknown_evidence_id_is_an_observation_not_a_crash(self):
        self.run_script([action("inspect_context", evidence_id="memory_or_fake_id", before=1, after=1), finish()])
        self.assertEqual(self.audit["tool_calls"][0]["status"], "invalid_action")
        self.assertIn("outside", self.planner.calls[1]["history"][-1]["error"])

    def test_repeated_empty_actions_stop_under_budget(self):
        repeated = action("inspect_ontology", relation_id="R_measures", issue="repeat")
        result = self.run_script([repeated] * 8)
        self.assertEqual(result["stop_reason"], "no_information_gain")
        self.assertEqual(result["budget"]["planner_steps"], 3)
        self.assertEqual(result["decision"], "manual_review")

    def test_call_budget_preserves_full_committee_and_counts_planner_failures(self):
        config = copy.deepcopy(self.config)
        config["budget"].update(max_llm_calls_per_candidate=5, max_retries_per_request=1)
        result = self.run_script([RuntimeError("HTTP 503 unavailable")], config=config)
        self.assertEqual(result["budget"]["llm_calls"], 1)
        self.assertEqual(result["executed_strategy"], "manual_degraded")
        self.assertEqual(sum(len(c.calls) for c in self.clients), 0)

    def test_declared_deterministic_fallback_inherits_spent_budget(self):
        config = copy.deepcopy(self.config)
        config["planner"]["fallback"] = "deterministic"
        result = self.run_script([RuntimeError("HTTP 503 unavailable")], config=config)
        self.assertEqual(result["executed_strategy"], "deterministic_fallback")
        self.assertEqual(result["budget"]["llm_calls"], 5)
        self.assertEqual(self.audit["context"]["configured_strategy"], "llm_react")
        self.assertEqual(sum(len(c.calls) for c in self.clients), 4)

    def test_revision_draft_does_not_mutate_candidate_or_reuse_revision_votes(self):
        def plan(payload):
            if not payload["history"]:
                return action("propose_revision", changes={"tail": "湿度"},
                    evidence_ids=[payload["evidence_catalog"][0]["evidence_id"]], reason="待人工核查")
            return finish()
        self.run_script(plan)
        self.assertEqual(self.repo.get_candidate("c")["revision"], 1)
        self.assertEqual(self.repo.get_candidate("c")["triple"]["tail"], "温度")
        self.assertTrue(self.audit["context"]["revision_drafts"][0]["requires_human_confirmation"])

    def test_cancellation_after_model_return_never_commits_a_decision(self):
        cancelled = [False]
        def plan(_):
            cancelled[0] = True
            return finish()
        result = self.run_script(plan, should_cancel=lambda: cancelled[0])
        self.assertFalse(result["committed"])
        self.assertEqual(self.audit["status"], "interrupted")
        self.assertIsNone(self.repo.latest_decision("c"))

    def test_candidate_revision_change_never_publishes_obsolete_result(self):
        def plan(_):
            candidate = self.repo.get_candidate("c")
            self.repo.revise_candidate("c", changes={"triple": {"tail": "湿度"}}, expected_row_version=candidate["row_version"])
            return finish()
        result = self.run_script(plan)
        self.assertFalse(result["committed"])
        self.assertIsNone(self.repo.latest_decision("c"))

    def test_resume_after_commit_does_not_repeat_model_calls_or_decision(self):
        runner = self.runner([ontology(), action("run_validation"), finish()])
        result = runner.audit_candidate("c", 1, self.index, context={"audit_id": "fixed"})
        again = runner.audit_candidate("c", 1, self.index, context={"audit_id": "fixed"})
        self.assertTrue(again["reused_completed"])
        self.assertEqual(len(self.planner.calls), 3)
        self.assertEqual(sum(len(c.calls) for c in self.clients), 4)
        self.assertEqual(self.repo.latest_decision("c")["audit_id"], result["audit_id"])

    def test_resume_after_validation_crash_reuses_already_stored_valid_votes(self):
        runner = self.runner([ontology(), action("run_validation"), finish()])
        original = AgentTools.run_validation
        def crash(tool):
            original(tool)
            raise KeyboardInterrupt()
        with patch.object(AgentTools, "run_validation", crash), self.assertRaises(KeyboardInterrupt):
            runner.audit_candidate("c", 1, self.index, context={"audit_id": "crash"})
        self.assertEqual(sum(len(c.calls) for c in self.clients), 4)
        result = runner.audit_candidate("c", 1, self.index, context={"audit_id": "crash"})
        self.assertTrue(result["committed"])
        self.assertEqual(sum(len(c.calls) for c in self.clients), 4)
        self.assertEqual(result["budget"]["llm_calls"], 7)

    def test_resume_rejects_changed_model_even_if_committee_version_was_not_bumped(self):
        runner = self.runner([ontology(), action("run_validation"), finish()])
        original = AgentTools.run_validation
        def crash(tool):
            original(tool)
            raise KeyboardInterrupt()
        with patch.object(AgentTools, "run_validation", crash), self.assertRaises(KeyboardInterrupt):
            runner.audit_candidate("c", 1, self.index, context={"audit_id": "changed-model"})
        self.validators[-1]["model_id"] = "different-model"
        with self.assertRaisesRegex(ValueError, "version mismatch"):
            runner.audit_candidate("c", 1, self.index, context={"audit_id": "changed-model"})
        self.assertEqual(sum(len(c.calls) for c in self.clients), 4)

    def test_no_planner_connection_is_explicit_manual_degradation(self):
        runner = ReviewOrchestrator(self.repo, self.guard, self.validators, config=self.config)
        result = runner.audit_candidate("c", 1, self.index)
        self.assertEqual(result["executed_strategy"], "manual_degraded")
        self.assertEqual(result["budget"]["llm_calls"], 0)

    def test_token_budget_counts_reported_usage_before_next_action(self):
        config = copy.deepcopy(self.config)
        config["budget"]["max_total_tokens"] = 10
        result = self.run_script([LLMResponse(ontology(), input_tokens=9, output_tokens=8)], config=config)
        self.assertEqual(result["budget"]["input_tokens"], 9)
        self.assertEqual(result["stop_reason"], "token_budget_exhausted")
        self.assertEqual(result["decision"], "manual_review")

    def test_planner_contract_rejects_overrides_and_bad_types(self):
        for bad in (action("run_validation", model="chosen-voter"),
                    action("search_evidence", scope="all_disk", query="x", purpose="check_support", limit=1),
                    action("inspect_context", evidence_id="x", before=True, after=1),
                    {**finish(), "votes": ["valid"]}):
            with self.subTest(bad=bad), self.assertRaises(ActionError):
                validate_action(bad)


if __name__ == "__main__":
    unittest.main()
