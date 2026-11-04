"""Explicit version dispatch; historical contracts are never silently corrected."""
from .contracts import sha256_json


def contract_for(version=None):
    from . import response_contract
    if version in (None,response_contract.CONTRACT_VERSION): return response_contract
    from . import response_contract_v4
    if version==response_contract_v4.CONTRACT_VERSION: return response_contract_v4
    raise ValueError('unsupported validator response contract: '+str(version))


def versions_for(config):
    contract=contract_for(config.get('validator_contract_version'))
    return {'response_contract_version':contract.CONTRACT_VERSION,'response_contract_hash':sha256_json(contract.RESPONSE_SCHEMA),
            'prompt_version':contract.PROMPT_VERSION,'prompt_hash':contract.PROMPT_HASH}


def prompt_for(proposal):
    """Frozen v4 tickets retain their original prompt and request identity."""
    from pathlib import Path
    import hashlib
    contract = contract_for(proposal.get('response_contract_version'))
    version = proposal.get('prompt_version')
    if contract.CONTRACT_VERSION == 'validator_response_v4' and version == 'triple_validator_v4':
        prompt = (Path(__file__).resolve().parents[1] / 'prompts/triple_validator_v4_legacy.txt').read_text(encoding='utf-8')
    else:
        prompt = contract.SYSTEM_PROMPT
    if version in {'triple_validator_v4', 'triple_validator_v4_direction_scope_v1'} and proposal.get('prompt_hash'):
        if hashlib.sha256(prompt.encode()).hexdigest() != proposal['prompt_hash']:
            raise ValueError('frozen source prompt hash mismatch')
    return prompt


def validate_vote(vote,version=None):
    contract=contract_for(version or vote.get('response_contract_version'))
    raw=contract.raw_from_vote(vote) if hasattr(contract,'raw_from_vote') else {k:vote[k] for k in contract.RESPONSE_SCHEMA['properties'] if k in vote}
    errors=contract.validate_response(raw)
    if not errors and hasattr(contract,'normalize'):
        normalized=contract.normalize(raw)
        if any(vote.get(k)!=normalized.get(k) for k in ('schema_status','schema_issue','checks')):
            errors.append('server-normalized schema assessment mismatch')
    return errors
