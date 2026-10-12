import json
import unittest
from pathlib import Path
from unittest.mock import patch

import ontology_build_app
import ontologyBuild.config as config_module
from ontologyBuild import pipeline
from ontologyBuild.config import OntologyPipelineConfig


ROOT = Path(__file__).resolve().parents[1]


class OntologyPipelineTests(unittest.TestCase):
    def test_mock_pipeline_keeps_current_business_json_untouched(self):
        sample = ROOT / "tests" / "fixtures" / "ontology_sample.md"
        legacy_path = pipeline.LEGACY_CANDIDATES_PATH
        before = legacy_path.read_text(encoding="utf-8") if legacy_path.exists() else None
        test_runs = ROOT / "runs" / "_test_pipeline"
        test_runs.mkdir(parents=True, exist_ok=True)
        config = OntologyPipelineConfig.from_env(
            {
                "ONTOLOGY_EXTRACTION_ENGINE": "mock",
                "ONTOLOGY_MODEL": "mock-model",
                "ONTOLOGY_BASE_POLICY": "draft",
                "ONTOLOGY_MAX_CHUNKS": "5",
            }
        )
        with (
            patch.object(pipeline, "RUNS_DIR", test_runs),
            patch.object(pipeline, "CURRENT_POINTER_PATH", test_runs / "current.json"),
        ):
            manifest = pipeline.run_ontology_pipeline(
                sample,
                config,
                write_compatibility=False,
            )

        after = legacy_path.read_text(encoding="utf-8") if legacy_path.exists() else None
        self.assertEqual(after, before)
        self.assertIn(manifest["status"], {"succeeded", "succeeded_empty"})
        self.assertEqual(manifest["candidate_counts"]["candidate_classes"], 1)
        self.assertNotIn("legacy_candidates", manifest["outputs"])

    def test_app_run_extraction_uses_unified_pipeline(self):
        with patch.object(ontology_build_app, "run_ontology_pipeline", return_value={"run_id": "run_x"}) as run:
            result = ontology_build_app.run_extraction(
                ROOT / "tests" / "fixtures" / "ontology_sample.md",
                review_mode="manual",
                base_policy="draft",
            )
        self.assertEqual(result["run_id"], "run_x")
        run.assert_called_once()
        config = run.call_args.args[1]
        self.assertEqual(config.base_policy, "draft")

    def test_config_falls_back_to_legacy_qwen_settings(self):
        legacy = {
            "api_key": "test-key",
            "base_url": "https://qwen.example.test/v1",
            "model": "qwen-test",
        }
        with patch.object(config_module, "_legacy_candidate_defaults", return_value=legacy):
            config = OntologyPipelineConfig.from_env({})
            api_key = config_module.configured_api_key({})

        self.assertEqual(config.model, "qwen-test")
        self.assertEqual(config.qwen_base_url, "https://qwen.example.test/v1")
        self.assertEqual(api_key, "test-key")


if __name__ == "__main__":
    unittest.main()
