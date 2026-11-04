"""Explicit referents and qualifier attachment, retained across revisions."""
from copy import deepcopy

VERSION = 'candidate_semantics_v1'
SCOPES = {'general','conditional','local_definition','example','derivation','unresolved'}
SCHEMA = {'type':'object','additionalProperties':False,'required':['entity_mentions','relation_meaning','qualifier_attachments','knowledge_scope'],
    'properties':{
        'entity_mentions':{'type':'object','additionalProperties':False,'required':['head','tail'],
            'properties':{side:{'type':'object','additionalProperties':False,'required':['entity_surface','canonical_entity','referent_scope'],
                'properties':{k:{'type':['string','null']} for k in ('entity_surface','canonical_entity','referent_scope')}} for side in ('head','tail')}},
        'relation_meaning':{'type':'object','additionalProperties':False,'required':['semantic_equivalence','note'],
            'properties':{'semantic_equivalence':{'enum':['equivalent','needs_review','unresolved']},'note':{'type':'string'}}},
        'qualifier_attachments':{'type':'array','items':{'type':'object','additionalProperties':False,
            'required':['qualifier_kind','attachment','value','scope_id'], 'properties':{
                'qualifier_kind':{'enum':['polarity','condition','time','applicability','modality','quantity','approximation','degree','discourse','unresolved']},
                'attachment':{'type':'string'},'value':{'type':'string'},'scope_id':{'type':['string','null']}}}},
        'knowledge_scope':{'enum':sorted(SCOPES)}}}


def validate_context(value):
    if not isinstance(value,dict): raise ValueError('semantic_context must be an object')
    allowed={'version','entity_mentions','relation_meaning','qualifier_attachments','knowledge_scope','scope_id','evidence_ids','assessment_status','origin_mentions'}
    if set(value)-allowed: raise ValueError('unknown semantic_context fields')
    if not isinstance(value.get('knowledge_scope','unresolved'),str) or value.get('knowledge_scope','unresolved') not in SCOPES: raise ValueError('invalid knowledge scope')
    if value.get('scope_id') is not None and not isinstance(value['scope_id'],str): raise ValueError('invalid scope_id')
    meaning=value.get('relation_meaning') or {}
    if not isinstance(meaning,dict) or set(meaning)-{'semantic_equivalence','note'} or meaning.get('semantic_equivalence','unresolved') not in {'equivalent','needs_review','unresolved'}:
        raise ValueError('invalid relation meaning')
    mentions=value.get('entity_mentions') or {}
    if not isinstance(mentions,dict) or set(mentions)-{'head','tail'}: raise ValueError('invalid entity mentions')
    for m in mentions.values():
        if not isinstance(m,dict) or set(m)-{'entity_surface','canonical_entity','referent_scope'} or any(v is not None and not isinstance(v,str) for v in m.values()):
            raise ValueError('invalid entity reference fields')
    attachments=value.get('qualifier_attachments') or []
    if not isinstance(attachments,list): raise ValueError('qualifier_attachments must be an array')
    for a in attachments:
        if not isinstance(a,dict) or set(a)!={'qualifier_kind','attachment','value','scope_id'} or not all(isinstance(a.get(k),str) and a[k].strip() for k in ('qualifier_kind','attachment','value')):
            raise ValueError('each qualifier requires kind, explicit attachment, value and scope_id')
        if a['qualifier_kind'] not in SCHEMA['properties']['qualifier_attachments']['items']['properties']['qualifier_kind']['enum']:
            raise ValueError('invalid qualifier kind')
        if a['scope_id'] is not None and not isinstance(a['scope_id'],str): raise ValueError('invalid qualifier scope_id')
    return deepcopy(value)


def attach_context(raw, anchors, scope):
    result=validate_context(raw)
    result.update(version=VERSION,scope_id=scope['section_id'],evidence_ids=[x['evidence_id'] for x in anchors],
                  assessment_status='unassessed',origin_mentions=deepcopy(result.get('entity_mentions') or {}))
    return result


def identity_scope(context):
    return {**{k:deepcopy(context.get(k)) for k in ('scope_id','knowledge_scope','qualifier_attachments')},
        **({'scope_id':None} if context.get('knowledge_scope')=='general' else {}),
        'referents':{side:{k:m.get(k) for k in ('canonical_entity','referent_scope')}
                    for side,m in (context.get('entity_mentions') or {}).items()}}
