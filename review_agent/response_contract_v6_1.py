"""V6 judgments, delivered through verified schema constraints or fixed templates."""
from copy import deepcopy
from pathlib import Path
import hashlib
from . import response_contract_v6 as v6
from .output_structure import DELIVERY_POLICY,DELIVERY_VERSION
from .schema_validation import schema_errors,unfilled_template_errors

CONTRACT_VERSION='validator_response_v6_1'
PROMPT_VERSION='triple_validator_v6_1_structured_output_v1'
RESPONSE_SCHEMA=deepcopy(v6.RESPONSE_SCHEMA)
OUTPUT_DELIVERY_VERSION=DELIVERY_VERSION
OUTPUT_DELIVERY_POLICY=deepcopy(DELIVERY_POLICY)
SUMMARY_POLICY_VERSION=v6.SUMMARY_POLICY_VERSION
SUMMARY_POLICY=deepcopy(v6.SUMMARY_POLICY)
SERVER_DERIVED_SUMMARY=True
PRESERVE_RAW_RESPONSE=True
CHECK_NAMES=v6.CHECK_NAMES
SOURCE_CHECKS=v6.SOURCE_CHECKS
NORMALIZED_COMPARE_FIELDS=v6.NORMALIZED_COMPARE_FIELDS
SYSTEM_PROMPT=(Path(__file__).resolve().parents[1]/'prompts/triple_validator_v6_1.txt').read_text(encoding='utf-8')
PROMPT_HASH=hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()


def validate_response(value):
    return schema_errors(value,RESPONSE_SCHEMA) or unfilled_template_errors(value) or v6.validate_response(value)


normalize=v6.normalize
raw_from_vote=v6.raw_from_vote
format_metadata=v6.format_metadata
correction_constraints=v6.correction_constraints
