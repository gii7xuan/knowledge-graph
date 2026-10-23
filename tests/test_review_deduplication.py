"""No-network checks for paid request reuse, concurrency and unknown outcomes."""

import copy
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from review_agent.llm_clients import MockLLMClient
from review_agent.repository import ReviewRepository
from review_agent.request_cache import validation_request_key
from review_agent.validators import judge_validator, model_request_count
from tests.test_review_v2 import proposal, response


class ValidationDeduplicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "runs")
        self.addCleanup(self.tmp.cleanup)
        self.repo = ReviewRepository(Path(self.tmp.name) / "review.sqlite3")
        self.repo.initialize()
        self.client = MockLLMClient(lambda _: response(), model_id="model-a", model_family="family-a")
        self.voter = {"id": "a", "client": self.client}

    def judge(self, item=None, voter=None, **kwargs):
        return judge_validator(voter or self.voter, item or proposal(), repository=self.repo, **kwargs)

    def test_new_audit_and_round_reuse_one_vote_and_do_not_repeat_usage(self):
        first = self.judge()
        item = {**proposal(), "audit_id": "new-audit", "round_id": 2, "proposal_digest": "new-digest"}
        reused = self.judge(item)
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(reused["response_status"], "ok")
        self.assertEqual(reused["audit_id"], "new-audit")
        self.assertEqual(reused["round_id"], 2)
        self.assertEqual(reused["proposal_digest"], "new-digest")
        self.assertEqual(reused["cache_source"]["audit_id"], first["audit_id"])
        self.assertTrue(reused["cached"])
        self.assertEqual(model_request_count(reused), 0)
        self.assertEqual(reused["attempts"], [])
        self.assertEqual(reused["input_tokens"] + reused["output_tokens"], 0)

    def test_concurrent_repositories_share_one_inflight_model_request(self):
        entered, release = threading.Event(), threading.Event()
        def slow(_):
            entered.set()
            self.assertTrue(release.wait(3))
            return response()
        self.client._callable = slow
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.judge, per_call_timeout_seconds=3)
            self.assertTrue(entered.wait(3))
            second_repo = ReviewRepository(self.repo.database_path)
            other = {**proposal(), "audit_id": "concurrent"}
            second = pool.submit(judge_validator, self.voter, other, repository=second_repo,
                                 per_call_timeout_seconds=3)
            release.set()
            results = [first.result(4), second.result(4)]
        self.assertEqual(len(self.client.calls), 1)
        self.assertTrue(all(r["response_status"] == "ok" for r in results))
        self.assertEqual(sum(model_request_count(r) for r in results), 1)

    def test_input_evidence_versions_memory_and_force_namespace_invalidate_reuse(self):
        self.judge()
        changes = [
            lambda p: p["candidate"].update(revision=2),
            lambda p: p["candidate"]["triple"].update(tail="湿度"),
            lambda p: p["candidate"].update(document_version="doc-v2"),
            lambda p: p["candidate"].update(ontology_version="ontology-v2"),
            lambda p: p["evidence_bundle"]["items"][0].update(quote="新增条件"),
            lambda p: p.update(evidence_bundle_hash="new-evidence"),
            lambda p: p.update(committee_version="new-committee"),
            lambda p: p.update(committee_members=[{"id": "new-member"}]),
            lambda p: p.update(prompt_version="new-prompt"),
            lambda p: p.update(response_contract_version="new-contract"),
            lambda p: p.update(memory_snapshot_id=2),
            lambda p: p.update(review_memory={"new": "case"}),
            lambda p: p.update(request_cache_namespace="explicit-force-run"),
        ]
        for index, mutate in enumerate(changes):
            item = copy.deepcopy(proposal())
            mutate(item)
            with self.subTest(index=index):
                self.assertEqual(self.judge(item)["response_status"], "ok")
                self.assertEqual(len(self.client.calls), index + 2)

    def test_model_and_provider_identity_invalidate_reuse(self):
        self.judge()
        for index, attributes in enumerate([
            {"model_id": "different-model"}, {"model_family": "different-family"},
            {"base_url": "https://different.test/v1"}, {"api_key": "different-test-key"}
        ]):
            client = MockLLMClient(lambda _: response(), model_id="model-a", model_family="family-a")
            for name, value in attributes.items():
                setattr(client, name, value)
            self.assertEqual(self.judge(voter={"id": "a", "client": client})["response_status"], "ok")
            self.assertEqual(len(client.calls), 1, index)

    def test_uncertain_is_reused_instead_of_rerolled_for_a_favourable_vote(self):
        self.client._callable = lambda _: response("uncertain")
        self.assertEqual(self.judge()["verdict"], "uncertain")
        self.assertEqual(self.judge()["verdict"], "uncertain")
        self.assertEqual(len(self.client.calls), 1)

    def test_timeout_never_starts_a_retry_even_after_late_response(self):
        release, finished = threading.Event(), threading.Event()
        def slow(_):
            release.wait(3)
            finished.set()
            return response()
        self.client._callable = slow
        try:
            timed_out = self.judge(max_retries=3, per_call_timeout_seconds=.03)
            self.assertEqual(timed_out["response_status"], "timeout")
            self.assertFalse(timed_out["retryable"])
            self.assertEqual(len(self.client.calls), 1)
            release.set()
            self.assertTrue(finished.wait(3))
            suppressed = self.judge({**proposal(), "audit_id": "after-timeout"}, per_call_timeout_seconds=.03)
            self.assertEqual(suppressed["error_code"], "DUPLICATE_REQUEST_SUPPRESSED")
            self.assertEqual(model_request_count(suppressed), 0)
            self.assertEqual(len(self.client.calls), 1)
        finally:
            release.set()

    def test_abandoned_request_has_no_automatic_lease_expiry_resend(self):
        key = validation_request_key(self.voter, proposal())
        self.repo.claim_validation_request(key, "crashed-owner")
        result = self.judge(per_call_timeout_seconds=.02)
        self.assertEqual(result["error_code"], "DUPLICATE_REQUEST_SUPPRESSED")
        self.assertEqual(len(self.client.calls), 0)

    def test_connection_lost_without_http_response_does_not_resend(self):
        class APIConnectionError(RuntimeError):
            pass
        self.client._callable = None
        self.client._queue = [APIConnectionError("connection lost after sending"), response()]
        result = self.judge(max_retries=3)
        self.assertTrue(result["outcome_unknown"])
        self.assertFalse(result["retryable"])
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.judge()["error_code"], "DUPLICATE_REQUEST_SUPPRESSED")
        self.assertEqual(len(self.client.calls), 1)

    def test_known_failed_request_can_be_retried_on_a_later_job(self):
        self.client._callable = None
        self.client._queue = [RuntimeError("Error code: 402 - Insufficient Balance"), response()]
        self.assertEqual(self.judge(max_retries=3)["http_status"], 402)
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.judge()["response_status"], "ok")
        self.assertEqual(len(self.client.calls), 2)
        self.assertTrue(self.judge()["cached"])

    def test_append_same_vote_twice_does_not_duplicate_request_history(self):
        self.repo.create_run({"run_id": "run"})
        self.repo.add_candidate({**proposal()["candidate"], "run_id": "run", "document_id": "doc",
            "document_version": "doc-v1", "ontology_version": "ontology-v1"})
        self.repo.start_audit("c", 1, {"audit_id": "audit"})
        item = proposal()
        vote = self.judge(item)
        self.repo.append_round("audit", item, [vote], {}, {})
        self.repo.append_round("audit", item, [vote], {}, {})
        with self.repo.connect() as c:
            self.assertEqual(c.execute("select count(1) from validator_votes").fetchone()[0], 1)
            self.assertEqual(c.execute("select count(1) from validator_attempts").fetchone()[0], 1)

    def test_duplicate_active_job_submissions_share_one_job(self):
        payload = {"run_id": "run", "candidate_ids": ["b", "a", "a"]}
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(pool.map(lambda _: self.repo.create_job("audit", payload), range(4)))
        self.assertEqual(len({j["job_id"] for j in jobs}), 1)
        first = jobs[0]
        again = self.repo.create_job("audit", {"run_id": "run", "candidate_ids": ["a", "b"]})
        self.assertEqual(first["job_id"], again["job_id"])
        self.repo.claim_job("worker")
        self.assertEqual(self.repo.create_job("audit", payload)["job_id"], first["job_id"])
        self.repo.complete_job(first["job_id"], {})
        self.assertNotEqual(self.repo.create_job("audit", payload)["job_id"], first["job_id"])


if __name__ == "__main__":
    unittest.main()
