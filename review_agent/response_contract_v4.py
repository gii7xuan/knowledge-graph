"""One model-authored schema assessment; normalized fields are server generated."""
from copy import deepcopy
from pathlib import Path
import hashlib
from . import response_contract as legacy

CONTRACT_VERSION='validator_response_v4'
PROMPT_VERSION='triple_validator_v4_direction_scope_v1'
RESPONSE_SCHEMA=deepcopy(legacy.RESPONSE_SCHEMA)
for key in ('schema_status','schema_issue'):
    RESPONSE_SCHEMA['required'].remove(key)
    RESPONSE_SCHEMA['properties'].pop(key)
RESPONSE_SCHEMA['required'].append('schema_assessment')
RESPONSE_SCHEMA['properties']['schema_assessment']={'type':'object','additionalProperties':False,
    'required':['status','issue','evidence_ids','note'],'properties':{'status':{'enum':sorted(legacy.VALID_SCHEMA)},
    'issue':{'enum':sorted(legacy.SCHEMA_ISSUES)},'evidence_ids':deepcopy(legacy.STRING_ARRAY),'note':{'type':'string'}}}
RESPONSE_SCHEMA['properties']['checks'].update(minItems=4,maxItems=4)
RESPONSE_SCHEMA['properties']['checks']['items']['properties']['check']['enum']=[k for k in legacy.CHECK_NAMES if k!='ontology']
SYSTEM_PROMPT=(Path(__file__).resolve().parents[1]/'prompts/triple_validator_v4.txt').read_text(encoding='utf-8')
PROMPT_HASH=hashlib.sha256(SYSTEM_PROMPT.encode('utf-8')).hexdigest()


def normalize(value):
    result=deepcopy(value)
    assessment=result.pop('schema_assessment')
    result['schema_status']=assessment['status'];result['schema_issue']=assessment['issue']
    result['checks'].append({'check':'ontology',**{k:assessment[k] for k in ('status','evidence_ids','note')}})
    return result


def validate_response(value):
    if not isinstance(value,dict): return ['response must be an object']
    missing=set(RESPONSE_SCHEMA['required'])-set(value)
    extra=set(value)-set(RESPONSE_SCHEMA['properties'])
    if missing or extra: return ['v4 response has missing or server-owned fields: '+str(sorted(missing|extra))]
    assessment=value.get('schema_assessment')
    if not isinstance(assessment,dict) or set(assessment)!={'status','issue','evidence_ids','note'}:
        return ['schema_assessment requires status/issue/evidence_ids/note']
    if (not isinstance(assessment.get('status'),str) or assessment['status'] not in legacy.VALID_SCHEMA or
            not isinstance(assessment.get('issue'),str) or assessment['issue'] not in legacy.SCHEMA_ISSUES):
        return ['invalid schema assessment enum']
    checks=value.get('checks')
    if not isinstance(checks,list) or len(checks)!=4 or any(not isinstance(c,dict) or c.get('check')=='ontology' for c in checks):
        return ['v4 has four semantic checks; ontology is generated from schema_assessment']
    return legacy.validate_response(normalize(value))


def raw_from_vote(vote):
    raw={k:deepcopy(vote[k]) for k in RESPONSE_SCHEMA['properties'] if k in vote}
    raw['checks']=[c for c in raw.get('checks',[]) if c.get('check')!='ontology']
    return raw
