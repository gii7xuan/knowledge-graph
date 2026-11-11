"""Four review axes with explicit, versioned field-rule findings."""
from copy import deepcopy
from pathlib import Path
import hashlib
from . import response_contract_v7 as previous
from .schema_validation import schema_errors, unfilled_template_errors
from .field_spec import specification
from .review_axes import vote_axes

CONTRACT_VERSION='validator_response_v8'
PROMPT_VERSION='triple_validator_v8_citations_v4'
RESPONSE_SCHEMA=deepcopy(previous.RESPONSE_SCHEMA)
finding=RESPONSE_SCHEMA['properties']['expression_findings']['items']
finding['required'] += ['rule_id','meaning_effect','suggested_change']
finding['properties'].update(rule_id={'enum':[r['id'] for r in specification()['rules']]},
    meaning_effect={'enum':['preserved','changed','unresolved']},suggested_change={'type':'string','minLength':1})
finding['properties']['normalization']={'anyOf':[{'type':'object','additionalProperties':False,
    'required':['operation','plan_id'],'properties':{'operation':{'enum':['apply_field_plan']},
    'plan_id':{'type':'string','minLength':1}}},{'type':'null'}]}
RESPONSE_SCHEMA['properties']['expression_findings']['maxItems']=12
SUMMARY_POLICY_VERSION='review_field_rules_v1'
SUMMARY_POLICY={**deepcopy(previous.SUMMARY_POLICY),'version':SUMMARY_POLICY_VERSION,
    'field_rules':'versioned rule ID, meaning effect and suggested change are substantive judgments',
    'normalization':'source-bound deterministic derived expression; original candidate retained; no automatic admission'}
OUTPUT_DELIVERY_VERSION=previous.OUTPUT_DELIVERY_VERSION
OUTPUT_DELIVERY_POLICY=deepcopy(previous.OUTPUT_DELIVERY_POLICY)
SERVER_DERIVED_SUMMARY=True
PRESERVE_RAW_RESPONSE=True
TARGETED_REPAIRS=True
DYNAMIC_ONTOLOGY=True
CHECK_NAMES=previous.CHECK_NAMES
SOURCE_CHECKS=previous.SOURCE_CHECKS
NORMALIZED_COMPARE_FIELDS=previous.NORMALIZED_COMPARE_FIELDS
SYSTEM_PROMPT=(Path(__file__).resolve().parents[1]/'prompts/triple_validator_v8_citations_v4.txt').read_text(encoding='utf-8')
PROMPT_HASH=hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
USER_SUFFIX=SYSTEM_PROMPT.split('\nFINAL_RESPONSE_INSTRUCTION\n',1)[1].strip()


def _previous(raw):
    result=deepcopy(raw);grouped={}
    priority={'semantic_error':4,'compound_entity':3,'unresolved':2,'field_placement':1,'duplicate_qualifier':1}
    for value in raw['expression_findings']:
        old={k:deepcopy(value[k]) for k in previous.RESPONSE_SCHEMA['properties']['expression_findings']['items']['properties']}
        if old['normalization'] is not None:
            old['normalization']={'operation':'retain_qualifier','field':'applicability','value':'legacy_validation_only'}
        if old['check'] not in grouped or priority[old['kind']]>priority[grouped[old['check']]['kind']]:grouped[old['check']]=old
    result['expression_findings']=list(grouped.values())
    return result


def validate_response(raw):
    errors=schema_errors(raw,RESPONSE_SCHEMA) or unfilled_template_errors(raw)
    if errors:return errors
    errors=previous.validate_response(_previous(raw))
    return errors + finding_errors(raw, include_previous=False)


def finding_errors(raw, *, include_previous=True):
    errors=previous.finding_errors(_previous(raw)) if include_previous else []
    seen=set()
    rules={r['id']:r for r in specification()['rules']}
    for f in raw['expression_findings']:
        identity=(f['check'],f['rule_id'],(f['normalization'] or {}).get('plan_id'))
        if identity in seen:errors.append('duplicate field-rule finding')
        seen.add(identity)
        if f['check'] not in rules[f['rule_id']]['checks']:errors.append('rule does not apply to this expression check')
        representation=f['kind'] in {'field_placement','duplicate_qualifier'}
        if representation and (not rules[f['rule_id']]['automatic'] or f['meaning_effect']!='preserved'):
            errors.append('normalization requires an executable rule and preserved meaning')
        if representation and raw['dimensions'][f['check']].get('issue_code') in {'NEGATION_IGNORED','CONDITION_MISSING'}:
            errors.append('material negation or condition loss is never representation normalization')
        if f['meaning_effect']=='changed' and f['kind']!='semantic_error':errors.append('changed meaning requires a substantive semantic finding')
        if f['kind']=='compound_entity' and f['rule_id'] not in {'PAIR-001','SPLIT-001'}:errors.append('compound review requires a pairing or split rule')
        if f['rule_id']=='FREQ-001' and representation and f['kind']!='field_placement':errors.append('frequency migration requires field placement')
        if f['rule_id']=='DUP-001' and representation and f['kind']!='duplicate_qualifier':errors.append('duplicate cleanup requires duplicate qualifier')
        if f['kind']=='semantic_error' and (f['meaning_effect']!='changed' or raw['dimensions'][f['check']]['status']!='fail'):
            errors.append('semantic change requires changed meaning and failed check')
        if f['kind']=='unresolved' and (f['meaning_effect']!='unresolved' or raw['dimensions'][f['check']]['status']!='unknown'):
            errors.append('unresolved meaning requires an unresolved check')
    return errors


def normalize(raw):
    result=previous.normalize(_previous(raw))
    result['expression_findings']=deepcopy(raw['expression_findings'])
    result['review_axes']=vote_axes(raw)
    from .contracts import sha256_json
    result['summary_derivation'].update(version=SUMMARY_POLICY_VERSION,policy_hash=sha256_json(SUMMARY_POLICY))
    return result


raw_from_vote=previous.raw_from_vote
format_metadata=previous.format_metadata
correction_constraints=previous.correction_constraints
validate_correction_trace=previous.validate_correction_trace
