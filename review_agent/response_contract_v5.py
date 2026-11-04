"""Source, expression, mapping and scope are independent review dimensions."""
from copy import deepcopy
from pathlib import Path
import hashlib
from . import response_contract_v4 as v4
from . import response_contract as v3

CONTRACT_VERSION = 'validator_response_v5'
PROMPT_VERSION = 'triple_validator_v5_semantic_units_v1'
CHECK_NAMES = ('triple_support','entity_reference','relation_semantics','relation_direction',
               'qualifiers','evidence_sufficiency','mapping','knowledge_scope','counterevidence')
SOURCE_CHECKS = ('triple_support','entity_reference','relation_semantics','relation_direction','qualifiers','evidence_sufficiency')
SCOPES = ('general','conditional','local_definition','example','derivation','unresolved')
RESPONSE_SCHEMA = deepcopy(v4.RESPONSE_SCHEMA)
RESPONSE_SCHEMA['required'] += ['expression_status','knowledge_scope','mapping','counterevidence_reviewed_ids','evidence_assessments','primary_evidence_ids']
RESPONSE_SCHEMA['properties'].update({
    'expression_status':{'enum':['faithful','needs_revision','unresolved']},
    'knowledge_scope':{'enum':list(SCOPES)},
    'counterevidence_reviewed_ids':deepcopy(v3.STRING_ARRAY),
    'primary_evidence_ids':deepcopy(v3.STRING_ARRAY),
    'mapping':{'type':'object','additionalProperties':False,'required':['status','relation_id','competing_relation_ids','note'],
        'properties':{'status':{'enum':['matched','ambiguous','unassigned','not_covered','constraint_conflict']},
            'relation_id':{'type':['string','null']},'competing_relation_ids':deepcopy(v3.STRING_ARRAY),'note':{'type':'string'}}}})
checks = RESPONSE_SCHEMA['properties']['checks']
checks.update(minItems=len(CHECK_NAMES), maxItems=len(CHECK_NAMES))
checks['items']['required'].append('issue_code')
checks['items']['properties'].update(check={'enum':list(CHECK_NAMES)}, issue_code={'type':'string'})
SYSTEM_PROMPT = (Path(__file__).resolve().parents[1]/'prompts/triple_validator_v5.txt').read_text(encoding='utf-8')
PROMPT_HASH = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()


def _legacy_raw(value):
    result = {k:deepcopy(value[k]) for k in v4.RESPONSE_SCHEMA['properties'] if k in value}
    result['checks'] = [{k:c[k] for k in ('check','status','evidence_ids','note')} for c in value['checks']
                        if c['check'] in {'triple_support','qualifiers','relation_direction','counterevidence'}]
    return result


def normalize(value):
    result = deepcopy(value)
    a = result.pop('schema_assessment')
    result.update(schema_status=a['status'],schema_issue=a['issue'])
    result['checks'].append({'check':'ontology',**{k:a[k] for k in ('status','evidence_ids','note')},'issue_code':a['issue']})
    return result


def raw_from_vote(vote):
    raw = {k:deepcopy(vote[k]) for k in RESPONSE_SCHEMA['properties'] if k in vote}
    raw['checks'] = [c for c in raw.get('checks',[]) if c.get('check') != 'ontology']
    return raw


def validate_response(value):
    if not isinstance(value,dict): return ['response must be an object']
    missing = set(RESPONSE_SCHEMA['required']) - value.keys()
    extra = value.keys() - RESPONSE_SCHEMA['properties'].keys()
    if missing or extra: return ['v5 missing or server-owned fields: '+str(sorted(missing|extra))]
    checks = value.get('checks')
    if not isinstance(checks,list) or any(not isinstance(c,dict) or set(c)!={'check','status','evidence_ids','note','issue_code'}
            or not isinstance(c.get('status'),str) or c['status'] not in v3.VALID_SCHEMA or not v3._strings(c.get('evidence_ids'))
            or not isinstance(c.get('note'),str) or not c['note'].strip() or not isinstance(c.get('issue_code'),str)
            or not isinstance(c.get('check'),str) for c in checks):
        return ['checks require check/status/evidence_ids/issue_code/nonempty note']
    if len(checks)!=len(CHECK_NAMES) or {c['check'] for c in checks}!=set(CHECK_NAMES):
        return ['v5 requires each of the nine named checks exactly once']
    errors = v4.validate_response(_legacy_raw(value))
    if errors: return errors
    by = {c['check']:c for c in checks}
    for name in (*SOURCE_CHECKS,'knowledge_scope','counterevidence'):
        if by[name]['status'] in {'pass','not_applicable'} and not by[name]['evidence_ids']:
            errors.append(name+': resolved source check requires verified evidence_ids')
    if not isinstance(value.get('expression_status'),str) or value['expression_status'] not in {'faithful','needs_revision','unresolved'}: errors.append('invalid expression_status')
    if not isinstance(value.get('knowledge_scope'),str) or value['knowledge_scope'] not in SCOPES: errors.append('invalid knowledge_scope')
    if value.get('expression_status')=='faithful' and any(by[n]['status'] not in {'pass','not_applicable'} for n in SOURCE_CHECKS):
        errors.append('faithful requires all source/expression checks resolved')
    if value.get('verdict')=='valid' and (value.get('expression_status')!='faithful' or
            any(c['status'] not in {'pass','not_applicable'} for c in checks)):
        errors.append('valid requires faithful expression and all checks resolved')
    mapping = value.get('mapping')
    if not isinstance(mapping,dict) or set(mapping)!={'status','relation_id','competing_relation_ids','note'}:
        errors.append('mapping requires status/relation_id/competing_relation_ids/note')
    elif (not isinstance(mapping['status'],str) or mapping['status'] not in {'matched','ambiguous','unassigned','not_covered','constraint_conflict'} or
            not isinstance(mapping['note'],str) or not mapping['note'].strip() or
            not v3._strings(mapping['competing_relation_ids']) or
            mapping['relation_id'] is not None and not isinstance(mapping['relation_id'],str)):
        errors.append('invalid mapping fields')
    elif mapping['status']=='matched' and (not mapping['relation_id'] or by['mapping']['status']!='pass'):
        errors.append('matched mapping requires formal relation_id and passing mapping check')
    if value.get('verdict')=='valid' and isinstance(mapping,dict) and mapping.get('status')!='matched':
        errors.append('valid requires a confirmed formal mapping')
    if not v3._strings(value.get('counterevidence_reviewed_ids')): errors.append('invalid counterevidence_reviewed_ids')
    if not v3._strings(value.get('primary_evidence_ids')): errors.append('invalid primary_evidence_ids')
    elif value.get('source_support')=='supported' and value.get('expression_status')=='faithful' and not value['primary_evidence_ids']:
        errors.append('faithful supported expression requires a complete nominated primary evidence set')
    if v3._strings(value.get('primary_evidence_ids')) and set(value['primary_evidence_ids'])&set(value.get('counter_evidence_ids') or []):
        errors.append('primary support set cannot contain unresolved counterevidence')
    if value.get('counter_evidence_ids') and by['counterevidence']['status']=='pass':
        errors.append('unresolved material counter_evidence_ids conflict with counterevidence=pass; clarify roles')
    assessments = value.get('evidence_assessments') or []
    if len({a['evidence_id'] for a in assessments})!=len(assessments): errors.append('duplicate/conflicting evidence role assessments')
    roles = {a.get('evidence_id'):a.get('role') for a in assessments if isinstance(a,dict) and isinstance(a.get('evidence_id'),str)}
    for field,role in (('supporting_evidence_ids','support'),('counter_evidence_ids','counter')):
        if v3._strings(value.get(field)):
            for eid in value[field]:
                if roles.get(eid)!=role: errors.append(field+': missing consistent evidence_assessments role for '+eid)
    for eid,role in roles.items():
        if role=='counter' and eid not in value['counter_evidence_ids']: errors.append('material counter role must be retained in counter_evidence_ids')
    if set(value.get('counterevidence_reviewed_ids') or [])&set(value['counter_evidence_ids']):
        errors.append('dismissed counterexamples and unresolved material counterevidence have different roles')
    return errors
