import unittest
from unittest.mock import patch

import workbench_app as workbench


class WorkbenchPagingTests(unittest.TestCase):
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

    def test_static_path_stays_inside_frontend(self):
        self.assertIsNone(workbench.frontend_file_for("/assets/../../workbench_app.py"))


if __name__ == "__main__":
    unittest.main()
