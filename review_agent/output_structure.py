"""Endpoint capabilities select a frozen delivery mode before review dispatch."""
from copy import deepcopy
import json
from pathlib import Path
from .contracts import sha256_json
from .schema_validation import fixed_template

ROOT=Path(__file__).resolve().parents[1]
DELIVERY_VERSION='typed_review_output_v1'
DELIVERY_POLICY={'version':DELIVERY_VERSION,'schema_mode':'verified_named_strict_json_schema',
    'fallback':'fixed_template_json_object','local_validation':'schema_then_review_contract',
    'capability_binding':['protocol','base_url','model','stream_responses','thinking_mode','schema_hash'],
    'runtime_error_fallback':False,'unknown_outcome_resend':False}


def endpoint_binding(voter):
    c=voter['client']
    return {'protocol':'chat_completions','base_url':str(getattr(c,'base_url','')).rstrip('/'),
        'model':str(voter.get('model_id') or getattr(c,'model_id','')),
        'stream_responses':bool(getattr(c,'stream_responses',False)),
        'thinking_mode':getattr(c,'thinking_mode',None)}


def resolve_profile(voter,config,schema):
    binding=endpoint_binding(voter);wanted={**binding,'schema_hash':sha256_json(schema)}
    configured=(config.get('output_structure') or {}).get('capabilities_path','config/review_output_capabilities.json')
    path=Path(configured);path=path if path.is_absolute() else ROOT/path
    matched=None;reason='capability_not_verified'
    try:
        registry=json.loads(path.read_text(encoding='utf-8'))
        if registry.get('version')=='review_output_capabilities_v1':
            matched=next((r for r in registry.get('entries',[]) if all(r.get(k)==v for k,v in wanted.items())),None)
        if matched:reason=matched.get('verification') or reason
    except (OSError,ValueError,TypeError,AttributeError):reason='capability_registry_unavailable'
    strict=bool(matched and matched.get('strict_supported') is True and matched.get('verification')=='strict_schema_observed')
    return {**binding,'version':DELIVERY_VERSION,'policy_hash':sha256_json(DELIVERY_POLICY),
        'mode':'json_schema' if strict else 'json_template','schema_hash':wanted['schema_hash'],
        'template_hash':None if strict else sha256_json(fixed_template(schema)),
        'capability_record_hash':sha256_json(matched) if matched else None,'verification':reason}


def frozen_profile(voter,proposal,schema):
    if voter is None:raise ValueError('structured review delivery requires a frozen voter slot')
    generation=next((g for g in proposal.get('validator_generation',[]) if g.get('voter_id')==voter['id']),None)
    profile=deepcopy((generation or {}).get('output_transport'))
    if not isinstance(profile,dict):raise ValueError('frozen output transport missing')
    if profile.get('version')!=DELIVERY_VERSION or profile.get('policy_hash')!=sha256_json(DELIVERY_POLICY):
        raise ValueError('frozen output delivery policy changed')
    if any(profile.get(k)!=v for k,v in endpoint_binding(voter).items()):raise ValueError('client differs from frozen output capability binding')
    if profile.get('schema_hash')!=sha256_json(schema):raise ValueError('frozen output schema changed')
    if profile.get('mode')=='json_template':
        if profile.get('template_hash')!=sha256_json(fixed_template(schema)):raise ValueError('frozen output template changed')
    elif profile.get('mode')!='json_schema' or not profile.get('capability_record_hash'):
        raise ValueError('invalid frozen structured output mode')
    return profile


def response_format(profile,schema):
    findings=schema.get('properties',{}).get('expression_findings',{}).get('items',{}).get('properties',{})
    name='triple_review_v8' if 'rule_id' in findings else 'triple_review_v7' if findings else 'triple_review_v6_1'
    return ({'type':'json_schema','json_schema':{'name':name,'strict':True,'schema':deepcopy(schema)}}
            if profile['mode']=='json_schema' else {'type':'json_object'})
