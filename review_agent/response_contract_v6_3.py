"""V6.2 judgments with separately frozen, retrieved ontology context."""
from copy import deepcopy
from pathlib import Path
import hashlib
from . import response_contract_v6_2 as previous

CONTRACT_VERSION = 'validator_response_v6_3'
PROMPT_VERSION = 'triple_validator_v6_3_dynamic_ontology_v1'
RESPONSE_SCHEMA = deepcopy(previous.RESPONSE_SCHEMA)
SUMMARY_POLICY_VERSION = previous.SUMMARY_POLICY_VERSION
SUMMARY_POLICY = deepcopy(previous.SUMMARY_POLICY)
OUTPUT_DELIVERY_VERSION = previous.OUTPUT_DELIVERY_VERSION
OUTPUT_DELIVERY_POLICY = deepcopy(previous.OUTPUT_DELIVERY_POLICY)
SERVER_DERIVED_SUMMARY = True
PRESERVE_RAW_RESPONSE = True
TARGETED_REPAIRS = True
DYNAMIC_ONTOLOGY = True
CHECK_NAMES = previous.CHECK_NAMES
SOURCE_CHECKS = previous.SOURCE_CHECKS
NORMALIZED_COMPARE_FIELDS = previous.NORMALIZED_COMPARE_FIELDS
SYSTEM_PROMPT = (Path(__file__).resolve().parents[1] / 'prompts/triple_validator_v6_3.txt').read_text(encoding='utf-8')
PROMPT_HASH = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
normalize = previous.normalize
raw_from_vote = previous.raw_from_vote
format_metadata = previous.format_metadata
validate_response = previous.validate_response
correction_constraints = previous.correction_constraints
validate_correction_trace = previous.validate_correction_trace
