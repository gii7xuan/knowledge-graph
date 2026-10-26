"""No-network tests for bounded requests, killable batches and checkpoints."""

import json
import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from review_agent.batch import run_review_batch, versions_for
from review_agent.execution import AuditPaused
from review_agent.jobs import JobRunner
from review_agent.llm_clients import MockLLMClient
from review_agent.progress import write_status_snapshot
from review_agent.repository import ReviewRepository
from review_agent.validators import judge_independently, judge_validator


def _fake_worker(sender, payload):
    repository = ReviewRepository(payload["database"])
    began = time.time()
    context = {**versions_for(payload["config"]), "execution_deadline_epoch": payload["deadline_epoch"]}
    repository.start_audit(payload["candidate_id"], payload["revision"], {**context, "audit_id": payload["audit_id"]})
    mode = payload["config"].get("test_mode", "complete")
    if mode == "paused" and payload["candidate_id"] == "c0":
        time.sleep(.15)
        sender.send({"status": "paused", "reason_code": "INSUFFICIENT_BALANCE", "vote": {"http_status": 402}})
        time.sleep(60)
    if mode in {"blocked", "paused"}:
        time.sleep(60)
    time.sleep(.3)
    proposal = {"round_id": 1, "proposal_digest": "test", "evidence_bundle_hash": "test"}
    status = "api_error" if mode == "api_error" else "ok"
    vote = {"voter_id": "a", "model_id": "test-model", "model_family": "test-family",
            "response_status": status, "verdict": "uncertain" if status == "ok" else None}
    repository.append_round(payload["audit_id"], proposal, [vote], {}, {})
    repository.commit_audit_if_current(payload["candidate_id"], payload["revision"], payload["audit_id"],
                                       {"decision": "manual_review", "features": {"test": 1}})
    sender.send({"status": "completed", "started": began, "finished": time.time()})
    sender.close()


def _proposal():
    return {"candidate": {"candidate_id": "c", "revision": 1,
                           "triple": {"head": "a", "surface_relation": "r", "tail": "b"}},
            "audit_id": "audit", "round_id": 1, "proposal_digest": "p", "evidence_bundle": {"items": []}}


class RequestDeadlineTests(unittest.TestCase):
    def test_402_is_not_retried(self):
        client = MockLLMClient([RuntimeError("Error code: 402 - Insufficient Balance")])
        result = judge_validator({"id": "a", "client": client}, _proposal(), max_retries=3)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(result["http_status"], 402)
        self.assertFalse(result["retryable"])
        self.assertTrue(result["pause_job"])

    def test_blocked_request_returns_by_its_deadline(self):
        def blocked(_):
            time.sleep(.4)
            return {"verdict": "valid"}
        client = MockLLMClient(blocked)
        began = time.monotonic()
        result = judge_validator({"id": "a", "client": client}, _proposal(), per_call_timeout_seconds=.06)
        self.assertLess(time.monotonic() - began, .3)
        self.assertEqual(result["response_status"], "timeout")
        self.assertEqual(result["error_code"], "HARD_REQUEST_TIMEOUT")
        time.sleep(.42)
        self.assertIsNone(result["verdict"])

    def test_402_does_not_wait_for_other_model_sockets(self):
        def blocked(_):
            time.sleep(.4)
            return {}
        voters = [{"id": "slow", "client": MockLLMClient(blocked)},
                  {"id": "billing", "client": MockLLMClient([RuntimeError("Error code: 402")])}]
        began = time.monotonic()
        with self.assertRaises(AuditPaused):
            judge_independently(voters, _proposal(), per_call_timeout_seconds=.15)
        self.assertLess(time.monotonic() - began, .3)


class BatchExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "runs")
        self.addCleanup(self.tmp.cleanup)
        self.repo = ReviewRepository(Path(self.tmp.name) / "review.sqlite3")
        self.repo.initialize()
        self.repo.create_run({"run_id": "run", "mode": "shadow"})
        for i in range(4):
            self.repo.add_candidate({"candidate_id": f"c{i}", "run_id": "run", "revision": 1,
                "document_id": "doc", "document_version": "doc-v1", "ontology_version": "onto-v1",
                "source_chunk_id": "chunk", "triple": {"head": f"head{i}", "relation_id": "r", "tail": "tail"}})
        self.config = {"mode": "shadow", "validators": [{"id": "a", "model_env": "TEST_REVIEW_MODEL",
            "family_env": "TEST_REVIEW_FAMILY", "api_key_env": "TEST_REVIEW_KEY", "base_url_env": "TEST_REVIEW_URL"}],
            "budget": {"max_parallel_candidates": 2, "total_timeout_seconds": 3}}
        self.env = patch.dict("os.environ", {"TEST_REVIEW_MODEL": "test-model", "TEST_REVIEW_FAMILY": "test-family",
                                              "TEST_REVIEW_KEY": "test", "TEST_REVIEW_URL": "https://example.test"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_batch(self, config=None, **kwargs):
        return run_review_batch(self.repo, "run", config or self.config, _worker_target=_fake_worker, **kwargs)

    def test_two_candidates_actually_overlap_and_leave_no_children(self):
        states = []
        result = self.run_batch(on_progress=lambda state: states.append(state))
        self.assertEqual(result["completed"], 4)
        self.assertEqual(result["failed"], 0)
        first = result["results"][:2]
        self.assertLess(max(r["started"] for r in first), min(r["finished"] for r in first))
        self.assertEqual(max(len(s["active"]) for s in states), 2)
        self.assertFalse(any(p.name == "review-candidate" for p in multiprocessing.active_children()))

    def test_healthy_checkpoints_skip_and_broken_completed_votes_retry(self):
        first = self.run_batch(candidate_ids=["c0"])
        self.assertEqual(first["completed"], 1)
        resumed = self.run_batch(candidate_ids=["c0"])
        self.assertEqual(resumed["skipped"], 1)
        self.assertEqual(resumed["selected"], 0)
        broken = {**self.config, "test_mode": "api_error"}
        self.run_batch(broken, candidate_ids=["c1"])
        repaired = self.run_batch(candidate_ids=["c1"])
        self.assertEqual(repaired["completed"], 1)
        self.assertEqual(repaired["skipped"], 0)

    def test_persistently_locked_status_file_does_not_interrupt_review(self):
        states = []
        config = {**self.config, "budget": {**self.config["budget"], "total_timeout_seconds": 8}}
        with patch("review_agent.batch.write_status_snapshot", side_effect=PermissionError("status file locked")), \
                self.assertLogs("review_agent.batch", level="WARNING") as logged:
            result = self.run_batch(config, candidate_ids=["c0", "c1"],
                status_file=Path(self.tmp.name) / "status.json", on_progress=states.append)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["completed"], 2)
        self.assertEqual(result["interrupted"], 0)
        self.assertTrue(result["progress_file_stale"])
        self.assertGreater(result["progress_write_failures"], 0)
        self.assertEqual(states[-1]["status"], "completed")
        self.assertEqual(len(logged.records), 1)
        self.assertFalse(any(p.name == "review-candidate" for p in multiprocessing.active_children()))

    def test_status_file_recovers_on_next_publish_after_lock_is_released(self):
        path = Path(self.tmp.name) / "status.json"
        calls = []
        def initially_locked(path, state):
            calls.append(state["status"])
            if len(calls) == 1:
                raise PermissionError("temporarily locked")
            write_status_snapshot(path, state)
        with patch("review_agent.batch.write_status_snapshot", side_effect=initially_locked), \
                self.assertLogs("review_agent.batch", level="WARNING"):
            result = self.run_batch(candidate_ids=["c0"], status_file=path)
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["progress_write_failures"], 1)
        self.assertFalse(result["progress_file_stale"])
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["progress_write_failures"], 1)

    def test_status_write_failure_does_not_mask_a_billing_pause(self):
        with patch("review_agent.batch.write_status_snapshot", side_effect=PermissionError("locked")), \
                self.assertLogs("review_agent.batch", level="WARNING"):
            result = self.run_batch({**self.config, "test_mode": "paused"},
                                    status_file=Path(self.tmp.name) / "status.json")
        self.assertEqual(result["status"], "paused")
        self.assertTrue(result["paused"])
        self.assertTrue(result["progress_file_stale"])

    def test_expired_agent_checkpoint_does_not_reuse_an_expired_execution_deadline(self):
        versions = versions_for(self.config)
        members = [{"id": "a", "model_id": "test-model", "model_family": "test-family"}]
        aid = self.repo.start_audit("c0", 1, {**versions, "committee_members": members,
                                               "execution_deadline_epoch": time.time() - 1})
        self.repo.save_agent_checkpoint(aid, {}, {}, "planning")
        self.repo.finish_incomplete_audit(aid, "interrupted", {})
        self.assertIsNone(self.repo.resumable_agent_audit("c0", 1, versions, members))

    def test_total_deadline_kills_blocked_process_and_keeps_audit(self):
        config = {**self.config, "test_mode": "blocked", "budget": {
            "max_parallel_candidates": 1, "total_timeout_seconds": 1.2}}
        began = time.monotonic()
        result = self.run_batch(config, candidate_ids=["c0"])
        self.assertLess(time.monotonic() - began, 4)
        self.assertEqual(result["failed"], 1)
        audit = self.repo.get_audit("c0", result["results"][0]["audit_id"])
        self.assertEqual(audit["status"], "timeout")
        self.assertIsNone(self.repo.latest_decision("c0"))
        self.assertFalse(any(p.name == "review-candidate" for p in multiprocessing.active_children()))

    def test_billing_pauses_batch_and_does_not_dispatch_remaining_candidates(self):
        status = Path(self.tmp.name) / "status.json"
        result = self.run_batch({**self.config, "test_mode": "paused"}, status_file=status)
        self.assertEqual(result["status"], "paused")
        self.assertTrue(result["paused"])
        self.assertTrue(all(r["candidate_id"] in {"c0", "c1"} for r in result["results"]))
        self.assertEqual(self.repo.get_audit("c0")["status"], "paused")
        self.assertIsNone(self.repo.latest_decision("c0"))
        self.assertEqual(json.loads(status.read_text(encoding="utf-8"))["status"], "paused")

    def test_late_result_cannot_commit_a_decision(self):
        aid = self.repo.start_audit("c0", 1, {"execution_deadline_epoch": time.time() - 1})
        self.assertFalse(self.repo.commit_audit_if_current("c0", 1, aid, {"decision": "auto_accept"}))
        self.assertEqual(self.repo.get_audit("c0", aid)["status"], "timeout")
        self.assertIsNone(self.repo.latest_decision("c0"))
        self.assertEqual(self.repo.list_outbox(run_id="run"), [])

    def test_http_job_pause_status_is_terminal_until_explicit_resume(self):
        self.repo.create_job("audit", {"run_id": "run"})
        runner = JobRunner(self.repo, lambda _: {"paused": True, "status": "paused"}, max_workers=1)
        self.addCleanup(runner.shutdown)
        job = runner.run_available_once()
        self.assertEqual(job["status"], "paused")
        self.assertIsNone(self.repo.claim_job("other-worker"))
        self.assertEqual(self.repo.request_cancel(job["job_id"])["status"], "cancelled")

    def test_model_or_contract_change_does_not_reuse_checkpoint(self):
        self.run_batch(candidate_ids=["c0"])
        members = [{"id": "a", "model_id": "test-model", "model_family": "test-family"}]
        versions = versions_for(self.config)
        self.assertFalse(self.repo.has_healthy_audit("c0", 1, versions,
            [{**members[0], "model_id": "different-model"}]))
        self.assertFalse(self.repo.has_healthy_audit("c0", 1,
            {**versions, "prompt_version": "different-version"}, members))

    def prepare_real_worker(self, http_status):
        """Exercise real SDK, evidence loader and Windows spawn using localhost only."""
        from review_agent.cli import _document_index
        from review_agent.ontology_guard import load_snapshot
        from review_agent.response_contract import CHECK_NAMES

        fixture = json.loads((Path(__file__).parent / "fixtures/review_agent/sensor_demo.json").read_text(encoding="utf-8"))
        index = _document_index("doc", fixture["document"])
        root = Path(self.tmp.name)
        document_path, ontology_path = root / "document.json", root / "ontology.json"
        document_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        ontology_path.write_text(json.dumps(fixture["ontology"], ensure_ascii=False), encoding="utf-8")
        self.repo.create_run({"run_id": "real", "mode": "shadow", "document_path": str(document_path),
            "ontology_path": str(ontology_path), "ontology_hash": load_snapshot(fixture["ontology"]).ontology_version})
        scenario = fixture["scenarios"][0]
        self.repo.add_candidate({"candidate_id": "real-candidate", "run_id": "real", "revision": 1,
            "document_id": "doc", "document_version": index["document_version"],
            "ontology_version": load_snapshot(fixture["ontology"]).ontology_version,
            "source_chunk_id": index["chunks"][0]["chunk_id"], "triple": scenario["triple"],
            "extraction_quote": scenario["quote"]})
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(request)
                if http_status == 402:
                    body = {"error": {"message": "Insufficient Balance", "type": "billing_error"}}
                else:
                    payload = json.loads(request["messages"][-1]["content"])
                    if "available_tools" in payload:
                        from tests.test_review_planner import action, ontology, finish
                        vote = ontology() if not payload["ontology_checked"] else (
                            action("run_validation") if not payload["current_validation"] else finish())
                        self._send({"id": "local", "object": "chat.completion", "created": 1, "model": "test-model",
                            "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(vote)},
                                         "finish_reason": "stop"}]}, http_status)
                        return
                    eid = payload["allowed_evidence_ids"][0]
                    vote = {"verdict": "valid", "source_support": "supported", "schema_status": "pass", "schema_issue": "none", "schema_change_hint": None,
                        "supporting_evidence_ids": [eid], "counter_evidence_ids": [], "reason_codes": [],
                        "checks": [{"check": name, "status": "pass", "evidence_ids": [eid], "note": "checked"}
                                   for name in CHECK_NAMES], "requested_context": None, "suggested_revision": None}
                    body = {"id": "local", "object": "chat.completion", "created": 1, "model": "test-model",
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(vote)},
                                     "finish_reason": "stop"}]}
                self._send(body, http_status)

            def _send(self, body, status):
                encoded = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close_server():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.addCleanup(close_server)
        os.environ["TEST_REVIEW_URL"] = f"http://127.0.0.1:{server.server_port}/v1"
        config = {**self.config, "consensus": {"max_rounds": 1}, "budget": {
            "total_timeout_seconds": 25, "per_call_timeout_seconds": 10,
            "max_parallel_candidates": 2, "max_retries_per_request": 1}}
        return config, requests

    def test_real_sdk_402_pauses_http_service_and_persists_audit(self):
        from review_agent.service import ReviewService
        config, requests = self.prepare_real_worker(402)
        result = ReviewService(self.repo, config=config).execute_audit_job({"run_id": "real"})
        self.assertEqual(result["status"], "paused", result)
        self.assertEqual(len(requests), 1)
        audit = self.repo.get_audit("real-candidate")
        self.assertEqual(audit["status"], "paused")
        self.assertEqual(audit["votes"][0]["http_status"], 402)
        self.assertIsNone(self.repo.latest_decision("real-candidate"))
        self.assertFalse(any(p.name == "review-candidate" for p in multiprocessing.active_children()))

    def test_real_sdk_cli_completes_and_resumes_without_another_request(self):
        from review_agent.cli import command_audit
        config, requests = self.prepare_real_worker(200)
        config_path = Path(self.tmp.name) / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        args = SimpleNamespace(config=str(config_path), database=str(self.repo.database_path),
            run_id="real", mode="shadow", candidate_id=None, force=False, limit=None,
            status_file=str(Path(self.tmp.name) / "status.json"))
        result = command_audit(args)
        self.assertEqual(result["completed"], 1, result)
        self.assertEqual(len(requests), 1)
        resumed = command_audit(args)
        self.assertEqual(resumed["skipped"], 1)
        self.assertEqual(len(requests), 1)

    def test_real_sdk_planner_is_injected_into_spawned_candidate_worker(self):
        config, requests = self.prepare_real_worker(200)
        config["planner"] = {**config["validators"][0], "enabled": True, "strategy": "llm_react", "fallback": "manual"}
        config["budget"].update(max_llm_calls_per_candidate=8, max_planner_steps=6)
        result = run_review_batch(self.repo, "real", config)
        self.assertEqual(result["completed"], 1, result)
        self.assertEqual(len(requests), 4)
        audit = self.repo.get_audit("real-candidate")
        self.assertEqual(audit["context"]["executed_strategy"], "llm_react")
        self.assertEqual(len(audit["planner_steps"]), 3)
        self.assertEqual(len(audit["votes"]), 1)

    def test_explicit_force_run_really_sends_a_fresh_model_request(self):
        config, requests = self.prepare_real_worker(200)
        self.assertEqual(run_review_batch(self.repo, "real", config)["completed"], 1)
        self.assertEqual(len(requests), 1)
        self.assertEqual(run_review_batch(self.repo, "real", config, force=True)["completed"], 1)
        self.assertEqual(len(requests), 2)
        self.assertEqual(run_review_batch(self.repo, "real", config)["skipped"], 1)
        self.assertEqual(len(requests), 2)


if __name__ == "__main__":
    unittest.main()
