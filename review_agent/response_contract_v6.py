"""One model opinion per dimension; deterministic, auditable server summaries."""
from copy import deepcopy
from pathlib import Path
import hashlib
from . import response_contract as v3,response_contract_v5 as v5,response_contract_v5_2 as v52
from .contracts import sha256_json

CONTRACT_VERSION = 'validator_response_v6'
PROMPT_VERSION = 'triple_validator_v6_dimensions_v1'
SUMMARY_POLICY_VERSION = 'review_dimensions_v1'
FORMAT_NORMALIZATION_VERSION = 'dimension_auxiliary_fields_v1'
PRESERVE_RAW_RESPONSE = True
SERVER_DERIVED_SUMMARY = True
CHECK_NAMES = v5.CHECK_NAMES
SOURCE_CHECKS = v5.SOURCE_CHECKS
MAPPING_STATES = ('matched','ambiguous','unassigned','not_covered','constraint_conflict',
                  'candidate_mapping_error','suspected_schema_error')
MAPPING_SUMMARY = {
    'matched':('matched','pass','none','pass'),
    'ambiguous':('ambiguous','unknown','unknown','unknown'),
    'unassigned':('unassigned','unknown','unknown','unknown'),
    'not_covered':('not_covered','unknown','not_covered','unknown'),
    'constraint_conflict':('constraint_conflict','fail','incompatible','fail'),
    'candidate_mapping_error':('unassigned','unknown','candidate_mapping_error','fail'),
    'suspected_schema_error':('unassigned','unknown','suspected_schema_error','unknown'),
}
SUMMARY_POLICY={'version':SUMMARY_POLICY_VERSION,'mapping_rules':MAPPING_SUMMARY,
    'expression_rules':{'needs_revision':'source contradiction or failed entity/meaning/direction/qualifier check',
        'faithful':'all six source/expression checks resolved','otherwise':'unresolved'},
    'verdict_rules':{'invalid':'source contradiction with validated material counterevidence',
        'valid':'supported, faithful, matched, schema pass, nine checks resolved, no material counterevidence',
        'otherwise':'uncertain'},'evidence_collections':'derived from unique evidence role assessments',
    'final_admission':'fixed independent quorum, program guard and project scope policy required'}
NORMALIZED_COMPARE_FIELDS = ('verdict','source_support','expression_status','knowledge_scope','mapping',
    'mapping_diagnosis','schema_status','schema_issue','schema_assessment','checks','reason_codes',
    'supporting_evidence_ids','counter_evidence_ids','summary_derivation')


def _dimension_schema(name):
    statuses=(list(v3.VALID_SUPPORT) if name=='triple_support' else list(MAPPING_STATES) if name=='mapping'
              else list(v5.SCOPES) if name=='knowledge_scope' else sorted(v3.VALID_SCHEMA))
    # Sort sets to keep frozen contract hashes stable across spawned processes.
    if name=='triple_support': statuses=sorted(statuses)
    properties={'status':{'enum':statuses},'evidence_ids':deepcopy(v3.STRING_ARRAY),
        'note':{'type':'string','minLength':1},'issue_code':{'type':['string','null']}}
    required=['status','evidence_ids','note']
    if name=='mapping':
        properties.update(relation_id={'type':['string','null']},competing_relation_ids=deepcopy(v3.STRING_ARRAY))
        required+=['relation_id','competing_relation_ids']
    return {'type':'object','additionalProperties':False,'required':required,'properties':properties}


RESPONSE_SCHEMA={'type':'object','additionalProperties':False,
    'required':['dimensions','evidence_assessments','primary_evidence_ids','counterevidence_reviewed_ids',
                'requested_context','suggested_revision'],
    'properties':{
        'dimensions':{'type':'object','additionalProperties':False,'required':list(CHECK_NAMES),
                      'properties':{n:_dimension_schema(n) for n in CHECK_NAMES}},
        **{k:deepcopy(v52.RESPONSE_SCHEMA['properties'][k]) for k in
            ('evidence_assessments','primary_evidence_ids','counterevidence_reviewed_ids',
             'requested_context','suggested_revision','schema_change_hint')}}}
SYSTEM_PROMPT=(Path(__file__).resolve().parents[1]/'prompts/triple_validator_v6.txt').read_text(encoding='utf-8')
PROMPT_HASH=hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()


def canonicalize(value):
    result=deepcopy(value);changes=[]
    if not isinstance(result,dict): return result,changes
    dims=result.get('dimensions')
    if isinstance(dims,dict):
        for name,item in dims.items():
            if not isinstance(item,dict):continue
            missing='issue_code' not in item;code=item.get('issue_code')
            if missing or code is None or isinstance(code,str) and code and not code.strip():
                change={'path':'/dimensions/'+name+'/issue_code','operation':'add' if missing else 'replace','to':''}
                if not missing:change['from']=deepcopy(code)
                changes.append(change);item['issue_code']=''
    if 'schema_change_hint' not in result:
        result['schema_change_hint']=None;changes.append({'path':'/schema_change_hint','operation':'add','to':None})
    return result,changes


def format_metadata(value):
    return {'format_normalization':{'version':FORMAT_NORMALIZATION_VERSION,'changes':canonicalize(value)[1]}}


def _derive(canonical):
    result=deepcopy(canonical);dims=result['dimensions'];mapping=dims['mapping'];checks=[]
    mapped,schema_status,schema_issue,mapping_check=MAPPING_SUMMARY[mapping['status']]
    for name in CHECK_NAMES:
        dimension=dims[name];status=dimension['status']
        if name=='triple_support':status={'supported':'pass','contradicted':'fail','insufficient':'unknown'}[status]
        elif name=='mapping':status=mapping_check
        elif name=='knowledge_scope':status='unknown' if status=='unresolved' else 'pass'
        checks.append({'check':name,'status':status,**{k:deepcopy(dimension[k]) for k in ('evidence_ids','note','issue_code')}})
    by={c['check']:c for c in checks}
    source=dims['triple_support']['status']
    expression=('needs_revision' if source=='contradicted' or any(by[n]['status']=='fail' for n in
        ('entity_reference','relation_semantics','relation_direction','qualifiers')) else
        'faithful' if all(by[n]['status'] in {'pass','not_applicable'} for n in SOURCE_CHECKS) else 'unresolved')
    support=[a['evidence_id'] for a in result['evidence_assessments'] if a['role']=='support']
    counter=[a['evidence_id'] for a in result['evidence_assessments'] if a['role']=='counter']
    verdict=('invalid' if source=='contradicted' else 'valid' if source=='supported' and expression=='faithful'
        and mapped=='matched' and schema_status=='pass' and not counter
        and all(c['status'] in {'pass','not_applicable'} for c in checks) else 'uncertain')
    reasons=[]
    if source=='contradicted':reasons.append('SOURCE_CONTRADICTS')
    if source=='insufficient' or by['evidence_sufficiency']['status'] in {'fail','unknown'}:reasons.append('EVIDENCE_INSUFFICIENT')
    for name,code in (('entity_reference','ENTITY_MISMATCH'),('relation_semantics','RELATION_MISMATCH'),
                      ('relation_direction','DIRECTION_MISMATCH'),('qualifiers','QUALIFIER_MISMATCH')):
        if by[name]['status']=='fail':reasons.append(code)
    qualifier_code=dims['qualifiers']['issue_code']
    if by['qualifiers']['status']=='fail' and qualifier_code in {'NEGATION_IGNORED','CONDITION_MISSING'}:reasons.append(qualifier_code)
    if mapping['status']=='not_covered':reasons.append('SCHEMA_GAP')
    if mapping['status']=='constraint_conflict':reasons.append('CONSTRAINT_VIOLATION')
    assessment={'status':schema_status,'issue':schema_issue,'evidence_ids':deepcopy(mapping['evidence_ids']),'note':mapping['note']}
    checks.append({'check':'ontology','status':schema_status,'issue_code':schema_issue,
                   'evidence_ids':deepcopy(mapping['evidence_ids']),'note':mapping['note']})
    result.update(verdict=verdict,source_support=source,expression_status=expression,
        knowledge_scope=dims['knowledge_scope']['status'],mapping_diagnosis=mapping['status'],
        mapping={'status':mapped,**{k:deepcopy(mapping[k]) for k in ('relation_id','competing_relation_ids','note')}},
        schema_status=schema_status,schema_issue=schema_issue,schema_assessment=assessment,checks=checks,
        reason_codes=reasons,supporting_evidence_ids=support,counter_evidence_ids=counter,
        summary_derivation={'version':SUMMARY_POLICY_VERSION,'model_input_fields':['dimensions','evidence_assessments'],
            'policy_hash':sha256_json(SUMMARY_POLICY),
            'final_admission_requires_frozen_committee_and_program_guards':True})
    return result


def normalize(value):
    canonical,changes=canonicalize(value)
    return {**_derive(canonical),'format_normalization':{'version':FORMAT_NORMALIZATION_VERSION,'changes':changes}}


def raw_from_vote(vote):
    return {k:deepcopy(vote[k]) for k in RESPONSE_SCHEMA['properties'] if k in vote}


def correction_constraints():
    return {'required_fields':list(RESPONSE_SCHEMA['required']),'dimensions':list(CHECK_NAMES),
        'server_summaries':'Do not return verdict, source_support, expression_status, schema_assessment, checks or top-level mapping/knowledge_scope/reason_codes/supporting_evidence_ids/counter_evidence_ids.',
        'mapping':'Return one dimensions.mapping status, a real relation_id or null, competing_relation_ids, evidence_ids and note.',
        'roles':'Evidence roles generate support/counter collections; preserve material counterevidence and original substantive judgments.',
        'auxiliary':'issue_code may be a string, null or omitted; never invent evidence IDs or missing judgments.'}


def validate_response(value):
    if not isinstance(value,dict):return ['response must be an object']
    errors=[k+': required' for k in RESPONSE_SCHEMA['required'] if k not in value]
    errors += [str(k)+': unknown/server-owned field' for k in value.keys()-RESPONSE_SCHEMA['properties'].keys()]
    dims=value.get('dimensions')
    if not isinstance(dims,dict):errors.append('dimensions: expected object')
    else:
        errors += ['dimensions.'+n+': required' for n in CHECK_NAMES if n not in dims]
        errors += ['dimensions.'+str(n)+': unknown field' for n in dims.keys()-set(CHECK_NAMES)]
        for name in CHECK_NAMES:
            if name not in dims:continue
            item=dims[name];spec=RESPONSE_SCHEMA['properties']['dimensions']['properties'][name];path='dimensions.'+name
            if not isinstance(item,dict):errors.append(path+': expected object');continue
            errors += [path+'.'+k+': required' for k in spec['required'] if k not in item]
            errors += [path+'.'+str(k)+': unknown field' for k in item.keys()-spec['properties'].keys()]
            if not isinstance(item.get('status'),str) or item['status'] not in spec['properties']['status']['enum']:errors.append(path+'.status: invalid')
            if not v3._strings(item.get('evidence_ids')):errors.append(path+'.evidence_ids: expected string array')
            if not isinstance(item.get('note'),str) or not item['note'].strip():errors.append(path+'.note: nonempty text required')
            if item.get('issue_code') is not None and not isinstance(item['issue_code'],str):errors.append(path+'.issue_code: expected string or null')
            if name=='mapping':
                rid=item.get('relation_id')
                if rid is not None and (not isinstance(rid,str) or not rid.strip()):errors.append(path+'.relation_id: expected nonempty string or null')
                if not v3._strings(item.get('competing_relation_ids')):errors.append(path+'.competing_relation_ids: expected string array')
                if item.get('status')=='matched' and not rid:errors.append(path+'.relation_id: matched mapping requires formal ID')
    for field in ('primary_evidence_ids','counterevidence_reviewed_ids'):
        if not v3._strings(value.get(field)):errors.append(field+': expected string array')
    assessments=value.get('evidence_assessments')
    if not isinstance(assessments,list) or any(not isinstance(a,dict) or set(a)!={'evidence_id','role','note'}
            or not isinstance(a.get('evidence_id'),str) or not a['evidence_id'].strip()
            or not isinstance(a.get('role'),str) or a['role'] not in {'support','counter','context','irrelevant'}
            or not isinstance(a.get('note'),str) or not a['note'].strip() for a in assessments):errors.append('evidence_assessments: invalid evidence role assessment')
    if errors:return errors
    derived=normalize(value)
    # Reuse all established source, expression, counterevidence and citation rules.
    return v52.validate_response(v52.raw_from_vote(derived))
