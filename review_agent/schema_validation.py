"""Local checks for the JSON Schema subset used by typed review responses."""
import math


def schema_errors(value,schema,path='$'):
    errors=[]
    supported={'type','enum','anyOf','properties','required','additionalProperties','items','minItems','maxItems',
        'minLength','maxLength','minimum','maximum','minProperties','description','title','default'}
    if set(schema)-supported:return [path+': unsupported local schema keyword']
    if 'additionalProperties' in schema and type(schema['additionalProperties']) is not bool:return [path+': unsupported additionalProperties schema']
    if isinstance(value,float) and not math.isfinite(value):return [path+': non-finite JSON number']
    if 'anyOf' in schema:
        if not any(not schema_errors(value,s,path) for s in schema['anyOf']):return [path+': no anyOf alternative matches']
        return []
    types=schema.get('type');types=[types] if isinstance(types,str) else types
    checks={'null':lambda v:v is None,'object':lambda v:isinstance(v,dict),'array':lambda v:isinstance(v,list),
        'string':lambda v:isinstance(v,str),'integer':lambda v:type(v) is int,
        'number':lambda v:type(v) in (int,float),'boolean':lambda v:type(v) is bool}
    if types and not any(t in checks and checks[t](value) for t in types):return [path+': wrong JSON type; expected '+str(types)]
    if 'enum' in schema and not any(type(value) is type(x) and value==x for x in schema['enum']):errors.append(path+': value outside enum')
    if isinstance(value,dict):
        props=schema.get('properties',{})
        errors += [path+'.'+k+': required' for k in schema.get('required',[]) if k not in value]
        if schema.get('additionalProperties') is False:
            errors += [path+'.'+str(k)+': unknown field' for k in value.keys()-props.keys()]
        for k,v in value.items():
            if k in props or schema.get('additionalProperties') is not False:errors += schema_errors(v,props.get(k,{}),path+'.'+str(k))
        if len(value)<schema.get('minProperties',0):errors.append(path+': too few properties')
    if isinstance(value,list):
        if len(value)<schema.get('minItems',0) or len(value)>schema.get('maxItems',float('inf')):errors.append(path+': invalid array length')
        if 'items' in schema:
            for i,v in enumerate(value):errors += schema_errors(v,schema['items'],path+'['+str(i)+']')
    if isinstance(value,str) and (len(value)<schema.get('minLength',0) or len(value)>schema.get('maxLength',float('inf'))):errors.append(path+': invalid string length')
    if type(value) in (int,float) and (value<schema.get('minimum',float('-inf')) or value>schema.get('maximum',float('inf'))):errors.append(path+': number outside range')
    return errors


def fixed_template(schema):
    """Invalid enum placeholders require a model judgment; no pass defaults."""
    if 'anyOf' in schema:
        choices=schema['anyOf']
        return None if any(s.get('type')=='null' for s in choices) else fixed_template(choices[0])
    types=schema.get('type');types=[types] if isinstance(types,str) else types or []
    if 'null' in types:return None
    if 'enum' in schema:return '__CHOOSE__'+ '|'.join(str(x) for x in schema['enum'])
    if 'object' in types:return {k:fixed_template(s) for k,s in schema.get('properties',{}).items()}
    if 'array' in types:return []
    if 'string' in types:return '__FILL_FROM_REVIEW__'
    return None


def unfilled_template_errors(value,path='$'):
    if isinstance(value,str):
        return [path+': unfilled template placeholder'] if value=='__FILL_FROM_REVIEW__' or value.startswith('__CHOOSE__') else []
    if isinstance(value,dict):return [e for k,v in value.items() for e in unfilled_template_errors(v,path+'.'+str(k))]
    if isinstance(value,list):return [e for i,v in enumerate(value) for e in unfilled_template_errors(v,path+'['+str(i)+']')]
    return []
