import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import workbench_app as workbench


class WorkbenchPagingTests(unittest.TestCase):
    def model_config_path(self, name):
        path = workbench.ROOT / "runs" / "_test_model_configs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        for candidate in (path, path.with_suffix(path.suffix + ".tmp")):
            if candidate.exists():
                candidate.unlink()
        return path

    def test_relation_display_name_includes_domain_and_range(self):
        item = {
            "candidate_type": "object_property",
            "name": "具有部件",
            "domain_candidate": "火炮",
            "range_candidate": "身管",
        }
        self.assertEqual(workbench.ontology.display_name(item), "具有部件: 火炮 -> 身管")

    def test_triplet_page_only_enriches_visible_items(self):
        rows = [
            {"review_id": str(index), "relation": "作用于", "confidence": index / 100}
            for index in range(45)
        ]
        with (
            patch.object(workbench.triplets, "list_review_files", return_value=["review.json"]),
            patch.object(workbench.triplets, "load_review_items", return_value=rows),
            patch.object(workbench.triplets, "item_for_response", side_effect=lambda _file, item: item) as enrich,
        ):
            result = workbench.triplet_items_page({
                "file": ["review.json"], "page": ["2"], "page_size": ["20"]
            })
        self.assertEqual(result["total"], 45)
        self.assertEqual([row["review_id"] for row in result["items"]], [str(i) for i in range(20, 40)])
        self.assertEqual(enrich.call_count, 20)

    def test_ontology_page_omits_review_log_and_enriches_visible_items(self):
        result = {
            "pending_review_candidates": [
                {"candidate_id": str(index), "name": f"概念{index}"} for index in range(45)
            ],
            "review_log": [{"large": "log"}],
        }
        with (
            patch.object(workbench, "cached_ontology_result", return_value=result),
            patch.object(workbench.ontology, "enrich_candidate_for_response", side_effect=lambda item: item) as enrich,
        ):
            page = workbench.ontology_items_page({"page": ["3"], "page_size": ["20"]})
        self.assertEqual(page["total"], 45)
        self.assertEqual([row["candidate_id"] for row in page["items"]], [str(i) for i in range(40, 45)])
        self.assertNotIn("review_log", page)
        self.assertEqual(enrich.call_count, 5)

    def test_missing_definition_moves_candidate_to_cleanup_queue(self):
        result = {
            "pending_review_candidates": [
                {"candidate_id": "new", "candidate_type": "class", "name": "火炮", "status": "new_candidate"},
                {"candidate_id": "known", "candidate_type": "class", "name": "弹丸", "status": "existing"},
            ],
            "summary": {"total": 2, "pending_review": 2},
        }
        with patch.object(workbench, "cached_ontology_result", return_value=result):
            overview = workbench.ontology_overview()
            cleanup = workbench.ontology_items_page({"status": ["cleanup_candidates"]})
            actionable = workbench.ontology_items_page({"status": ["pending_review_candidates"]})
        self.assertEqual(overview["summary"]["cleanup_pending"], 1)
        self.assertEqual(overview["summary"]["actionable_pending"], 1)
        self.assertEqual([item["candidate_id"] for item in cleanup["items"]], ["new"])
        self.assertEqual(cleanup["items"][0]["review_quality"]["approval_errors"], ["缺少定义"])
        self.assertEqual([item["candidate_id"] for item in actionable["items"]], ["known"])

    def test_incomplete_candidate_cannot_be_accepted(self):
        result = {
            "pending_review_candidates": [
                {"candidate_id": "new", "candidate_type": "class", "name": "火炮", "status": "new_candidate"}
            ]
        }
        with (
            patch.object(workbench, "cached_ontology_result", return_value=result),
            patch.object(workbench.ontology, "write_json") as write_json,
        ):
            with self.assertRaisesRegex(ValueError, "缺少定义"):
                workbench.decide_ontology({"candidate_id": "new", "decision": "accept"})
        write_json.assert_not_called()

    def test_start_ontology_build_passes_base_policy(self):
        with (
            patch.object(workbench, "safe_markdown_path", return_value=workbench.ROOT / "mdfiles" / "sample.md"),
            patch.object(workbench, "resolve_model_config", return_value={"id": "test", "name": "Test", "api_key": "key", "base_url": "https://example.test/v1", "model": "model"}),
            patch.object(workbench.threading, "Thread") as thread_class,
        ):
            thread = thread_class.return_value
            job = workbench.start_ontology_build({
                "markdown_path": "mdfiles/sample.md",
                "review_mode": "manual",
                "base_policy": "draft",
            })
        self.assertEqual(job["base_policy"], "draft")
        self.assertEqual(thread_class.call_args.kwargs["args"][-2], "draft")
        thread.start.assert_called_once()

    def test_model_config_is_saved_without_exposing_key(self):
        with patch.object(workbench, "MODEL_CONFIG_PATH", self.model_config_path("models_save.json")):
            created = workbench.create_model_config({
                "name": "Qwen Test",
                "base_url": "https://qwen.example.test/v1",
                "api_key": "secret-key",
                "model": "qwen-plus",
            })
            listed = workbench.list_model_configs()["models"]

        self.assertEqual(created["model"]["model"], "qwen-plus")
        self.assertTrue(created["model"]["has_api_key"])
        self.assertNotIn("api_key", created["model"])
        self.assertTrue(any(item["id"] == created["model"]["id"] for item in listed))

    def test_start_ontology_build_passes_selected_model_config(self):
        with patch.object(workbench, "MODEL_CONFIG_PATH", self.model_config_path("models_ontology.json")):
            created = workbench.create_model_config({
                "name": "Qwen Test",
                "base_url": "https://qwen.example.test/v1",
                "api_key": "secret-key",
                "model": "qwen-plus",
            })
            with (
                patch.object(workbench, "ontology_job", {"exists": False, "running": False}),
                patch.object(workbench, "safe_markdown_path", return_value=workbench.ROOT / "mdfiles" / "sample.md"),
                patch.object(workbench.threading, "Thread") as thread_class,
            ):
                thread = thread_class.return_value
                job = workbench.start_ontology_build({
                    "markdown_path": "mdfiles/sample.md",
                    "review_mode": "manual",
                    "base_policy": "draft",
                    "model_id": created["model"]["id"],
                })

        model_config = thread_class.call_args.kwargs["args"][-1]
        self.assertEqual(model_config["api_key"], "secret-key")
        self.assertEqual(model_config["base_url"], "https://qwen.example.test/v1")
        self.assertEqual(model_config["model"], "qwen-plus")
        self.assertEqual(job["model"]["model"], "qwen-plus")
        self.assertNotIn("api_key", job["model"])
        thread.start.assert_called_once()

    def test_start_triplet_extraction_injects_selected_model_env(self):
        process = Mock()
        process.poll.return_value = None
        process.pid = 12345
        run_dir = workbench.ROOT / "runs" / "_test_triplet_model"
        run_dir.mkdir(parents=True, exist_ok=True)
        with patch.object(workbench, "MODEL_CONFIG_PATH", self.model_config_path("models_triplet.json")):
            created = workbench.create_model_config({
                "name": "Qwen Test",
                "base_url": "https://qwen.example.test/v1",
                "api_key": "secret-key",
                "model": "qwen-plus",
            })
            with (
                patch.object(workbench, "WORKBENCH_RUN_DIR", run_dir),
                patch.object(workbench, "triplet_job", None),
                patch.object(workbench, "select_release", return_value=Mock(
                    path=Path("D:/fixture_release"), binding=lambda: {"ontology_release_id": "release_fixture"})),
                patch.object(workbench, "safe_markdown_path", return_value=workbench.ROOT / "mdfiles" / "sample.md"),
                patch.object(workbench.subprocess, "Popen", return_value=process) as popen,
            ):
                job = workbench.start_triplet_extraction({
                    "markdown_path": "mdfiles/sample.md",
                    "output_stem": "sample",
                    "model_id": created["model"]["id"],
                })

        env = popen.call_args.kwargs["env"]
        self.assertEqual(popen.call_args.args[0][-2:], ["--review-mode", "shadow"])
        self.assertEqual(env["OPENAI_API_KEY"], "secret-key")
        self.assertEqual(env["QWEN_BASE_URL"], "https://qwen.example.test/v1")
        self.assertEqual(env["TRIPLET_MODEL"], "qwen-plus")
        self.assertEqual(env["TRIPLET_ONTOLOGY_RELEASE"], str(Path("D:/fixture_release")))
        self.assertNotIn("HTTP_PROXY", env)
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertNotIn("ALL_PROXY", env)
        self.assertEqual(job["model"]["model"], "qwen-plus")
        self.assertNotIn("api_key", job["model"])

    def test_static_path_stays_inside_frontend(self):
        self.assertIsNone(workbench.frontend_file_for("/assets/../../workbench_app.py"))

    def test_v3_runs_route_uses_shared_review_service(self):
        handler = workbench.WorkbenchHandler.__new__(workbench.WorkbenchHandler)
        handler.path = "/api/triplets/runs?page=2&page_size=5"
        handler.send_json = Mock()
        service = Mock()
        service.runs.return_value = {"items": [], "page": 2, "page_size": 5}
        with patch.object(workbench, "get_default_service", return_value=service):
            handler.do_GET()
        service.runs.assert_called_once_with(2, 5)
        handler.send_json.assert_called_once_with({"items": [], "page": 2, "page_size": 5})


if __name__ == "__main__":
    unittest.main()
