"""Explicit field coverage for new revision requests; lossless wire conversion."""
from copy import deepcopy
from .contracts import sha256_json

VERSION='revision_field_map_v2'


def enabled(proposal):
    version=proposal.get('revision_delivery_version')
    if version not in (None,VERSION):raise ValueError('unknown revision delivery version')
    return version==VERSION


def output_contract(proposal,canonical):
    if not enabled(proposal):return canonical
    fields=proposal['changed_fields']
    if not fields or len(set(fields))!=len(fields):raise ValueError('distinct changed fields required')
    contract=deepcopy(canonical)
    item=deepcopy(canonical['properties']['field_reviews']['items'])
    named=deepcopy(item)
    named['properties'].pop('field');named['required'].remove('field')
    by_name={'type':'object','additionalProperties':False,'properties':{f:deepcopy(named) for f in fields},'required':list(fields)}
    legacy={'type':'array','items':item,'minItems':len(fields),'maxItems':len(fields),
        'allOf':[{'contains':{'type':'object','required':['field'],'properties':{'field':{'const':f}}}} for f in fields]}
    legacy['items']['properties']['field']={'type':'string','enum':list(fields)}
    contract['properties']['field_reviews']={'oneOf':[by_name,legacy]}
    return contract


def request_fields(proposal):
    if not enabled(proposal):return {}
    def value(which,path):
        result=proposal[which]['triple']
        for part in path.split('.'):
            result=result.get(part) if isinstance(result,dict) else None
        return deepcopy(result)
    return {'field_review_checklist':{f:{'before':value('before',f),'after':value('after',f)} for f in proposal['changed_fields']},
        'field_review_delivery':{'version':VERSION,'preferred_shape':'object_keyed_by_exact_changed_field',
            'missing_or_duplicate_fields':'invalid; never copied or inferred',
            'substantive_decisions':'approve, reject and uncertain remain independent of JSON shape'}}


def prompt_suffix(proposal):
    if not enabled(proposal):return ''
    return '''\nUse field_review_checklist as the exact required set, including every qualifier_status.* field.
Prefer field_reviews as an object keyed by those exact names; each value has status, evidence_ids and note.
Review the before/after value and status separately when both appear. No required key may be omitted.
A complete legacy field array is also accepted. JSON completion is not evidence of support: retain reject
or uncertain and source objections when warranted. Do not copy a judgment into an unexamined field.'''


def project(raw,proposal):
    if not enabled(proposal) or not isinstance(raw,dict) or not isinstance(raw.get('field_reviews'),dict):
        return raw,None
    reviews=raw['field_reviews'];fields=proposal['changed_fields']
    if set(reviews)!=set(fields):raise ValueError('named field_reviews must cover exactly the changed fields')
    result=deepcopy(raw);items=[]
    for field in fields:
        value=reviews[field]
        if not isinstance(value,dict) or set(value)!={'status','evidence_ids','note'}:
            raise ValueError('named field review requires exactly status, evidence_ids and note')
        items.append({'field':field,**deepcopy(value)})
    result['field_reviews']=items
    return result,{'version':VERSION,'operation':'named_fields_to_canonical_array_only',
        'wire_hash':sha256_json(raw),'canonical_hash':sha256_json(result),'field_names_hash':sha256_json(fields),
        'semantic_values_changed':False}


def prompt_hash(proposal,prompt,canonical,contract_version):
    if not enabled(proposal):return sha256_json([contract_version,prompt,canonical])
    return sha256_json([contract_version,prompt,output_contract(proposal,canonical),VERSION])
