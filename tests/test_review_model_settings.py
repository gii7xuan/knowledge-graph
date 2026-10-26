"""Review slot defaults share credentials safely across CLI, HTTP and workers."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review_agent.batch import configured_members, versions_for
from review_agent.cli import _configured_validators
from review_agent.model_settings import validator_settings
from review_agent.repository import ReviewRepository
from review_agent.service import ReviewService


class ReviewModelSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "runs")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "connection.yaml").write_text(
            'llm:\n  api_key: local-secret\n  base_url: https://example.test/v1\n', encoding="utf-8")
        self.slot = {"id": "a", "model": "llama4", "family": "llama",
            "connection_config": str(self.root / "connection.yaml"), "verify_ssl": False,
            "model_env": "TEST_SLOT_MODEL", "family_env": "TEST_SLOT_FAMILY",
            "api_key_env": "TEST_SLOT_KEY", "base_url_env": "TEST_SLOT_URL"}
        clean = patch.dict("os.environ", {"TEST_SLOT_MODEL": "", "TEST_SLOT_FAMILY": "",
            "TEST_SLOT_KEY": "", "TEST_SLOT_URL": ""})
        clean.start()
        self.addCleanup(clean.stop)

    def test_connection_reference_and_explicit_model_are_resolved(self):
        settings = validator_settings(self.slot)
        self.assertEqual(settings["model"], "llama4")
        self.assertEqual(settings["family"], "llama")
        self.assertEqual(settings["api_key"], "local-secret")
        self.assertFalse(settings["verify_ssl"])

    def test_environment_override_does_not_leak_shared_key_to_other_url(self):
        with patch.dict("os.environ", {"TEST_SLOT_URL": "https://other.test/v1"}):
            with self.assertRaisesRegex(ValueError, "explicit API key"):
                validator_settings(self.slot)
        with patch.dict("os.environ", {"TEST_SLOT_URL": "https://other.test/v1", "TEST_SLOT_KEY": "other-key",
                                      "TEST_SLOT_MODEL": "other-model", "TEST_SLOT_FAMILY": "other-family"}):
            settings = validator_settings(self.slot)
            self.assertEqual(settings["api_key"], "other-key")
            self.assertEqual(settings["model"], "other-model")
            self.assertEqual(settings["family"], "other-family")
            self.assertIsNone(settings["verify_ssl"])

    def test_env_only_slots_remain_supported(self):
        slot = {k: v for k, v in self.slot.items() if k not in {"model", "family", "connection_config", "verify_ssl"}}
        with patch.dict("os.environ", {"TEST_SLOT_URL": "https://other.test/v1", "TEST_SLOT_KEY": "other-key",
                                      "TEST_SLOT_MODEL": "other-model", "TEST_SLOT_FAMILY": "other-family"}):
            self.assertEqual(configured_members({"validators": [slot]}), [
            {"id": "a", "model_id": "other-model", "model_family": "other-family", "model_identity": "other-model"}])

    def test_saved_registry_connection_selects_explicit_id_without_copying_credentials(self):
        registry = self.root / "models.json"
        registry.write_text(json.dumps({"models": [
            {"id": "old", "api_key": "old-key", "base_url": "https://old.test/v1"},
            {"id": "deepseek", "api_key": "deepseek-key", "base_url": "https://deepseek.test/v1"}
        ]}), encoding="utf-8")
        slot = {**self.slot, "connection_config": str(registry), "connection_model_id": "deepseek",
                "model": "deepseek-flash", "family": "deepseek"}
        settings = validator_settings(slot)
        self.assertEqual(settings["api_key"], "deepseek-key")
        self.assertEqual(settings["base_url"], "https://deepseek.test/v1")
        self.assertEqual(settings["model"], "deepseek-flash")
        with patch.dict("os.environ", {"TEST_SLOT_URL": "https://other.test/v1"}):
            with self.assertRaisesRegex(ValueError, "explicit API key"):
                validator_settings(slot)
        for selected in (None, "missing"):
            with self.subTest(selected=selected), self.assertRaisesRegex(ValueError, "connection_model_id"):
                validator_settings({**slot, "connection_model_id": selected})
        registry.write_text(json.dumps({"models": [
            {"id": "deepseek"}, {"id": "deepseek"}]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            validator_settings(slot)

    def test_cli_http_and_batch_use_the_same_three_member_configuration(self):
        config = json.loads((Path(__file__).resolve().parents[1] / "config/review_agent.json").read_text(encoding="utf-8"))
        expected = [("Qwen", "qwen"), ("Gemma", "gemma"), ("deepseek-flash", "deepseek")]
        self.assertEqual([(slot["model"], slot["family"]) for slot in config["validators"]], expected)
        self.assertEqual(config["consensus"]["committee_size"], 3)
        self.assertEqual(config["consensus"]["required_votes"], 3)
        self.assertEqual(config["consensus"]["minimum_distinct_families"], 3)
        self.assertEqual(config["validators"][2]["connection_config"], "config/workbench_models.json")
        self.assertTrue(config["validators"][2]["connection_model_id"])
        for slot in config["validators"]:
            slot.update({**self.slot, "id": slot["id"], "model": slot["model"], "family": slot["family"]})
        members = configured_members(config)
        clients = _configured_validators(config)
        self.assertEqual([(m["model_id"], m["model_family"]) for m in members], expected)
        self.assertEqual([(m["model_id"], m["model_family"]) for m in clients], expected)
        self.assertTrue(all(c["client"].verify_ssl is False for c in clients))
        status = ReviewService(ReviewRepository(self.root / "unused.sqlite3"), config=config).config_status()
        self.assertTrue(status["committee_ready"])
        self.assertEqual(status["distinct_model_families"], 3)
        self.assertNotIn("local-secret", json.dumps(status))
        self.assertEqual(versions_for(config)["committee_version"], "committee_qwen_gemma_deepseek_v1")

    def test_invalid_tls_value_is_not_silently_accepted(self):
        with self.assertRaisesRegex(ValueError, "boolean"):
            validator_settings({**self.slot, "verify_ssl": "false"})


if __name__ == "__main__":
    unittest.main()
