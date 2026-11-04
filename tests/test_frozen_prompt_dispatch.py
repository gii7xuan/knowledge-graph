import hashlib
import unittest
from pathlib import Path
from review_agent.validator_contracts import prompt_for
from review_agent.contracts import sha256_json
from ontology_review.validators import prompt_for as ontology_prompt_for

ROOT = Path(__file__).resolve().parents[1]


class FrozenPromptDispatchTests(unittest.TestCase):
    def test_source_old_and_new_prompt_versions_are_separate(self):
        old = (ROOT/'prompts/triple_validator_v4_legacy.txt').read_text(encoding='utf-8')
        new = (ROOT/'prompts/triple_validator_v4.txt').read_text(encoding='utf-8')
        self.assertNotEqual(old, new)
        for version, text in [('triple_validator_v4', old), ('triple_validator_v4_direction_scope_v1', new)]:
            self.assertEqual(prompt_for({'response_contract_version': 'validator_response_v4', 'prompt_version': version,
                                         'prompt_hash': hashlib.sha256(text.encode()).hexdigest()}), text)

    def test_ontology_legacy_prompt_remains_available(self):
        old = (ROOT/'prompts/ontology_extension_validator_v2_legacy.txt').read_text(encoding='utf-8')
        self.assertEqual(ontology_prompt_for({'versions': {'prompt_version': 'ontology_extension_validator_v2',
                                                          'prompt_hash': sha256_json(old)}}), old)

    def test_historical_hash_mismatch_cannot_silently_use_a_new_prompt(self):
        with self.assertRaises(ValueError):
            prompt_for({'response_contract_version': 'validator_response_v4', 'prompt_version': 'triple_validator_v4', 'prompt_hash': 'wrong'})
        with self.assertRaises(ValueError):
            ontology_prompt_for({'versions': {'prompt_version': 'ontology_extension_validator_v2', 'prompt_hash': 'wrong'}})
