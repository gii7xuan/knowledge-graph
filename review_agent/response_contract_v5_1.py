"""V5.1 preserves semantic disagreements instead of requesting a new opinion."""
from copy import deepcopy
from pathlib import Path
import hashlib
from . import response_contract_v5 as v5

CONTRACT_VERSION = 'validator_response_v5_1'
PROMPT_VERSION = 'triple_validator_v5_1_complete_output_v1'
CHECK_NAMES = v5.CHECK_NAMES
SOURCE_CHECKS = v5.SOURCE_CHECKS
RESPONSE_SCHEMA = deepcopy(v5.RESPONSE_SCHEMA)
RESPONSE_SCHEMA['required'].remove('schema_change_hint')
RESPONSE_SCHEMA['properties']['schema_assessment']['properties']['issue_code'] = {'type':'string'}
SYSTEM_PROMPT = (Path(__file__).resolve().parents[1]/'prompts/triple_validator_v5_1.txt').read_text(encoding='utf-8')
PROMPT_HASH = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()


def _canonical(value):
    result = deepcopy(value)
    if isinstance(result,dict):
        result.setdefault('schema_change_hint',None)
        assessment=result.get('schema_assessment')
        if isinstance(assessment,dict): assessment.pop('issue_code',None)
    return result


def validate_response(value):
    if isinstance(value,dict) and isinstance(value.get('schema_assessment'),dict):
        if 'issue_code' in value['schema_assessment'] and not isinstance(value['schema_assessment']['issue_code'],str):
            return ['schema_assessment.issue_code must be a string']
    return v5.validate_response(_canonical(value))


def normalize(value):
    return v5.normalize(_canonical(value))


raw_from_vote = v5.raw_from_vote
