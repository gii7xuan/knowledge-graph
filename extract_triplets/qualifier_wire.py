"""Explicit opt-in repair of an absent-value wire sentinel; no inferred semantics."""
from copy import deepcopy
from review_agent.contracts import sha256_json

VERSION='explicit_not_stated_wire_v1'
FIELDS=('polarity','condition','time_scope','applicability','modality','quantifier')


def policy():
    return {'version':VERSION,'when':{'value':'not_stated','original_status':'not_stated'},
        'replacement':None,'fields':list(FIELDS),'original_statuses':'unchanged',
        'other_values_and_source_quotes':'unchanged','semantic_inference':False}


def project(original,frozen_policy):
    if frozen_policy!=policy():raise ValueError('unsupported qualifier wire policy')
    value=deepcopy(original);moves=[]
    q=value.get('qualifiers');s=value.get('qualifier_status')
    if isinstance(q,dict) and isinstance(s,dict):
        for field in FIELDS:
            if q.get(field)=='not_stated' and s.get(field)=='not_stated':
                q[field]=None;moves.append({'path':'/qualifiers/'+field,'from':'not_stated','to':None})
    if not moves:return value,None
    trace={'version':VERSION,'policy':policy(),'policy_hash':sha256_json(policy()),
        'original_raw_hash':sha256_json(original),'projected_raw_hash':sha256_json(value),'moves':moves,
        'original_statuses_unchanged':True,'source_quotes_unchanged':True,'semantic_inference':False}
    return value,trace


def validate(original,projected,trace):
    expected,proof=project(original,policy())
    return [] if proof is not None and expected==projected and proof==trace else ['qualifier wire projection proof mismatch']
