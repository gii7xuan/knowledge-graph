"""Nine source checks with explicit representation findings and four server axes."""
from copy import deepcopy
from pathlib import Path
import hashlib
from . import response_contract_v6_3 as previous
from . import response_contract_v6 as v6
from .schema_validation import schema_errors, unfilled_template_errors
from .review_axes import EXPRESSION_CHECKS, QUALIFIER_FIELDS, vote_axes, representation_checks

CONTRACT_VERSION = 'validator_response_v7'
PROMPT_VERSION = 'triple_validator_v7_four_axes_v1'
RESPONSE_SCHEMA = deepcopy(previous.RESPONSE_SCHEMA)
RESPONSE_SCHEMA['required'].append('expression_findings')
NORMALIZATION = {'type': 'object', 'additionalProperties': False, 'required': ['operation', 'field', 'value'],
    'properties': {'operation': {'enum': ['retain_qualifier', 'remove_duplicate_qualifier']},
                   'field': {'enum': list(QUALIFIER_FIELDS)}, 'value': {'type': 'string', 'minLength': 1}}}
RESPONSE_SCHEMA['properties']['expression_findings'] = {'type': 'array', 'items': {
    'type': 'object', 'additionalProperties': False,
    'required': ['check', 'kind', 'note', 'evidence_ids', 'normalization'],
    'properties': {'check': {'enum': list(EXPRESSION_CHECKS)},
        'kind': {'enum': ['field_placement', 'duplicate_qualifier', 'compound_entity', 'semantic_error', 'unresolved']},
        'note': {'type': 'string', 'minLength': 1}, 'evidence_ids': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}},
        'normalization': {'anyOf': [deepcopy(NORMALIZATION), {'type': 'null'}]}}}}
SUMMARY_POLICY_VERSION = 'review_four_axes_v1'
SUMMARY_POLICY = {**deepcopy(previous.SUMMARY_POLICY), 'version': SUMMARY_POLICY_VERSION,
    'source': 'semantic evidence independent of representation and mapping',
    'expression': 'structured findings and candidate-bound qualifier preservation proof',
    'normalization': 'retain current qualifier or remove an exact duplicate boundary token; never apply automatically',
    'routing': 'independent source/expression/mapping/scope with all pending actions retained'}
OUTPUT_DELIVERY_VERSION = previous.OUTPUT_DELIVERY_VERSION
OUTPUT_DELIVERY_POLICY = deepcopy(previous.OUTPUT_DELIVERY_POLICY)
SERVER_DERIVED_SUMMARY = True
PRESERVE_RAW_RESPONSE = True
TARGETED_REPAIRS = True
DYNAMIC_ONTOLOGY = True
CHECK_NAMES = previous.CHECK_NAMES
SOURCE_CHECKS = previous.SOURCE_CHECKS
NORMALIZED_COMPARE_FIELDS = (*previous.NORMALIZED_COMPARE_FIELDS, 'expression_findings', 'review_axes', 'representation_checks')
SYSTEM_PROMPT = (Path(__file__).resolve().parents[1] / 'prompts/triple_validator_v7.txt').read_text(encoding='utf-8')
PROMPT_HASH = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()


def legacy_raw(raw):
    return {k: deepcopy(v) for k, v in raw.items() if k in previous.RESPONSE_SCHEMA['properties']}


def normalize(raw):
    result = previous.normalize(legacy_raw(raw))
    result['expression_findings'] = deepcopy(raw['expression_findings'])
    result['review_axes'] = vote_axes(raw)
    result['representation_checks'] = sorted(representation_checks(raw))
    status = result['review_axes']['expression']['status']
    result['expression_status'] = {'standard': 'faithful', 'lossless_normalization': 'normalization_required',
                                   'semantic_revision': 'needs_revision', 'unresolved': 'unresolved'}[status]
    codes = {'qualifiers': 'QUALIFIER_MISMATCH', 'entity_reference': 'ENTITY_MISMATCH',
             'relation_semantics': 'RELATION_MISMATCH', 'relation_direction': 'DIRECTION_MISMATCH'}
    removed = {codes[n] for n in result['representation_checks']}
    result['reason_codes'] = [c for c in result['reason_codes'] if c not in removed]
    if status != 'standard': result['verdict'] = 'uncertain'
    result['summary_derivation']['version'] = SUMMARY_POLICY_VERSION
    from .contracts import sha256_json
    result['summary_derivation']['policy_hash'] = sha256_json(SUMMARY_POLICY)
    return result


def validate_response(raw):
    errors = schema_errors(raw, RESPONSE_SCHEMA) or unfilled_template_errors(raw)
    if errors: return errors
    errors = previous.validate_response(legacy_raw(raw))
    errors += finding_errors(raw)
    if vote_axes(raw)['source']['status'] == 'supported' and not raw['primary_evidence_ids']:
        errors.append('supported source axis requires primary evidence IDs, including representation-only findings')
    return errors


def finding_errors(raw):
    """Semantic checks on expression fields, reusable when another axis is malformed."""
    errors = []; seen = set()
    for f in raw['expression_findings']:
        if f['check'] in seen: errors.append('one structured finding per expression check is required')
        seen.add(f['check'])
        representational = f['kind'] in {'field_placement', 'duplicate_qualifier'}
        if representational != (f['normalization'] is not None):
            errors.append('only representation findings require a bounded normalization plan')
        if representational and raw['dimensions'][f['check']].get('issue_code') in {'NEGATION_IGNORED', 'CONDITION_MISSING'}:
            errors.append('material negation or condition loss cannot be treated as representation')
        if f['check'] == 'relation_direction' and representational:
            errors.append('direction changes cannot be lossless normalization')
        if f['kind'] == 'semantic_error' and raw['dimensions'][f['check']]['status'] != 'fail':
            errors.append('semantic findings must agree with the failed expression check')
        if f['kind'] == 'unresolved' and raw['dimensions'][f['check']]['status'] != 'unknown':
            errors.append('unresolved findings must agree with the unresolved expression check')
    for n in EXPRESSION_CHECKS:
        if raw['dimensions'][n]['status'] in {'fail', 'unknown'} and n not in seen:
            errors.append(n + ': failed or unresolved checks require an explicit finding')
    return errors


def raw_from_vote(vote):
    return {k: deepcopy(vote[k]) for k in RESPONSE_SCHEMA['properties'] if k in vote}


format_metadata = previous.format_metadata
correction_constraints = previous.correction_constraints
validate_correction_trace = previous.validate_correction_trace
