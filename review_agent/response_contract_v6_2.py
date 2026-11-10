"""Frozen V6 review opinions with a separate, bounded field-repair protocol."""
from copy import deepcopy
from pathlib import Path
import hashlib
from . import response_contract_v6_1 as v61
from . import response_corrections as correction

CONTRACT_VERSION='validator_response_v6_2'
PROMPT_VERSION='triple_validator_v6_2_targeted_repairs_v1'
RESPONSE_SCHEMA=deepcopy(v61.RESPONSE_SCHEMA)
SUMMARY_POLICY_VERSION=v61.SUMMARY_POLICY_VERSION
SUMMARY_POLICY=deepcopy(v61.SUMMARY_POLICY)
OUTPUT_DELIVERY_VERSION=v61.OUTPUT_DELIVERY_VERSION
OUTPUT_DELIVERY_POLICY=deepcopy(v61.OUTPUT_DELIVERY_POLICY)
SERVER_DERIVED_SUMMARY=True
PRESERVE_RAW_RESPONSE=True
TARGETED_REPAIRS=True
CHECK_NAMES=v61.CHECK_NAMES
SOURCE_CHECKS=v61.SOURCE_CHECKS
NORMALIZED_COMPARE_FIELDS=v61.NORMALIZED_COMPARE_FIELDS
SYSTEM_PROMPT=(Path(__file__).resolve().parents[1]/'prompts/triple_validator_v6_2.txt').read_text(encoding='utf-8')
PROMPT_HASH=hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
normalize=v61.normalize
raw_from_vote=v61.raw_from_vote
format_metadata=v61.format_metadata
validate_response=v61.validate_response
correction_constraints=v61.correction_constraints


def validate_correction_trace(vote):
    if vote.get('response_origin')!='field_patch_merge':return []
    try:
        from .validators import _parse_content
        from .contracts import sha256_json
        plan=vote['correction_trace']['plan'];original=_parse_content(vote['raw_response'])
        patch=_parse_content(vote['raw_correction_response'])
        if plan['policy']['version']!=correction.POLICY_VERSION or sha256_json(plan['policy'])!=vote['correction_policy_hash']:
            return ['field correction policy mismatch']
        if sha256_json({k:v for k,v in plan.items() if k!='plan_hash'})!=plan['plan_hash']:return ['field correction plan hash mismatch']
        bound=vote.get('cache_source',{}).get('proposal_digest',vote.get('proposal_digest'))
        if plan['proposal_digest']!=bound or plan['evidence_hash']!=vote.get('evidence_hash'):return ['field correction source binding mismatch']
        merged=correction.apply_repairs(original,plan,patch)
        if merged!=vote['corrected_response']:return ['field correction merged response mismatch']
        from .response_contract_v6 import canonicalize
        from .validator_contracts import contract_for
        reader=contract_for(vote.get('response_contract_version') or CONTRACT_VERSION).raw_from_vote
        recorded=reader(vote)
        # Rejected envelopes retain raw/corrected bodies, not a normalized model
        # projection. The immutable merge above is still verified; semantic/schema
        # rejection is checked separately and must never become an eligible vote.
        if vote.get('response_status') in {'invalid_contract','invalid_reference'} and not recorded:return []
        if canonicalize(merged)[0]!=canonicalize(recorded)[0]:return ['field correction differs from stored model fields']
    except (ValueError,KeyError,TypeError,IndexError,AttributeError):return ['invalid field correction provenance']
    return []
