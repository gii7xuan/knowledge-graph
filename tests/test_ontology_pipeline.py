import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ontology_build_app
import ontologyBuild.config as config_module
from ontologyBuild import pipeline
from ontologyBuild import ontology_candidate
from ontologyBuild.config import OntologyPipelineConfig


ROOT = Path(__file__).resolve().parents[1]


class OntologyPipelineTests(unittest.TestCase):
    def test_empty_installation_bootstraps_only_a_run_scoped_draft(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=ROOT/'runs') as directory:
            folder=Path(directory);missing=folder/'no_published_ontology.json'
            config=OntologyPipelineConfig.from_env({'ONTOLOGY_EXTRACTION_ENGINE':'mock','ONTOLOGY_MODEL':'mock-model','ONTOLOGY_BASE_POLICY':'draft'})
            with patch.object(pipeline,'RUNS_DIR',folder/'ontology_runs'),patch.object(pipeline,'CURRENT_POINTER_PATH',folder/'current.json'):
                manifest=pipeline.run_ontology_pipeline(ROOT/'tests/fixtures/ontology_sample.md',config,base_ontology_path=missing,write_compatibility=False)
            self.assertFalse(missing.exists())
            self.assertFalse(manifest['published_base_used'])
            self.assertEqual(manifest['base_origin'],'empty_run_scoped_bootstrap')
            self.assertEqual(manifest['extraction_stats']['failed_chunks'],0)
            self.assertTrue(Path(manifest['input_snapshot']['ontology']['path']).is_file())

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

    def test_default_client_timeout_is_180_seconds(self):
        config = OntologyPipelineConfig.from_env({
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "https://example.test/v1",
            "ONTOLOGY_MODEL": "qwen-test",
        })

        self.assertEqual(config.timeout_seconds, 180)
        self.assertEqual(config.context_max_chars, 8000)
        self.assertEqual(config.chunk_size, 600)
        self.assertEqual(config.chunk_overlap, 80)
        self.assertEqual(config.max_output_tokens, 1500)

    def test_retry_backoff_is_exponential_and_capped(self):
        with patch.object(ontology_candidate.random, "uniform", return_value=0.0):
            delays = [
                ontology_candidate.retry_backoff_seconds(attempt)
                for attempt in range(1, 6)
            ]

        self.assertEqual(delays, [10.0, 20.0, 40.0, 60.0, 60.0])

    def test_timeout_fallback_splits_large_chunk_and_limits_output(self):
        empty_result = {field: [] for field in ontology_candidate.RESULT_FIELDS}

        class FakeCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    raise RuntimeError("504 Gateway Time-out")
                message = SimpleNamespace(content=json.dumps(empty_result, ensure_ascii=False))
                return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        completions = FakeCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        chunk = {"chunk_id": "chunk_timeout", "content": "火炮结构。" * 120}

        with patch.object(ontology_candidate, "wait_before_retry") as wait:
            result = ontology_candidate.extract_candidates_from_chunk(
                chunk,
                retry_count=2,
                ontology_context={"document_id": "test"},
                llm_client=client,
                model="qwen-test",
                max_output_tokens=1500,
                adaptive_retry_chunk_size=400,
                adaptive_retry_chunk_overlap=40,
            )

        self.assertEqual(result, empty_result)
        self.assertGreater(len(completions.calls), 2)
        self.assertTrue(all(call["max_tokens"] == 1500 for call in completions.calls))
        wait.assert_called_once_with("chunk_timeout", 1)


if __name__ == "__main__":
    unittest.main()
