"""Allow absent auxiliary codes without repairing substantive review judgments."""
from copy import deepcopy
from pathlib import Path
import hashlib
from . import response_contract_v5_1 as v51

CONTRACT_VERSION = 'validator_response_v5_2'
PROMPT_VERSION = 'triple_validator_v5_2_auxiliary_format_v1'
FORMAT_NORMALIZATION_VERSION = 'auxiliary_fields_v1'
PRESERVE_RAW_RESPONSE = True
CHECK_NAMES = v51.CHECK_NAMES
SOURCE_CHECKS = v51.SOURCE_CHECKS
RESPONSE_SCHEMA = deepcopy(v51.RESPONSE_SCHEMA)
check_schema = RESPONSE_SCHEMA['properties']['checks']['items']
check_schema['required'].remove('issue_code')
check_schema['properties']['issue_code'] = {'type':['string','null']}
RESPONSE_SCHEMA['properties']['schema_assessment']['properties']['issue_code'] = {'type':['string','null']}
SYSTEM_PROMPT = (Path(__file__).resolve().parents[1]/'prompts/triple_validator_v5_2.txt').read_text(encoding='utf-8')
PROMPT_HASH = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()


def canonicalize(value):
    """Whitelist only auxiliary differences; an empty code means unspecified."""
    result=deepcopy(value);changes=[]
    if not isinstance(result,dict): return result,changes

    def auxiliary_code(item,path,*,optional=False):
        if not isinstance(item,dict): return
        missing='issue_code' not in item
        if missing and optional: return
        code=item.get('issue_code')
        if missing or code is None or isinstance(code,str) and code and not code.strip():
            change={'path':path+'/issue_code','operation':'add' if missing else 'replace','to':''}
            if not missing: change['from']=deepcopy(code)
            changes.append(change);item['issue_code']=''

    checks=result.get('checks')
    if isinstance(checks,list):
        for i,check in enumerate(checks): auxiliary_code(check,'/checks/'+str(i))
    auxiliary_code(result.get('schema_assessment'),'/schema_assessment',optional=True)
    if 'schema_change_hint' not in result:
        result['schema_change_hint']=None
        changes.append({'path':'/schema_change_hint','operation':'add','to':None})
    return result,changes


def format_metadata(value):
    return {'format_normalization':{'version':FORMAT_NORMALIZATION_VERSION,'changes':canonicalize(value)[1]}}


def validate_response(value):
    return v51.validate_response(canonicalize(value)[0])


def normalize(value):
    canonical,changes=canonicalize(value)
    return {**v51.normalize(canonical),
        'format_normalization':{'version':FORMAT_NORMALIZATION_VERSION,'changes':changes}}


raw_from_vote = v51.raw_from_vote
