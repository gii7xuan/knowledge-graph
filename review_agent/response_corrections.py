"""Small field repairs preserve every existing substantive opinion."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from .contracts import sha256_json
from .schema_validation import schema_errors,fixed_template
from . import response_contract_v6 as v6

POLICY_VERSION='targeted_review_repairs_v1'
PROMPT=(Path(__file__).resolve().parents[1]/'prompts/triple_review_field_repair_v1.txt').read_text(encoding='utf-8')
PROMPT_HASH=hashlib.sha256(PROMPT.encode()).hexdigest()
DEFAULT_LIMITS={'max_corrections_per_seat':1,'max_calls_per_candidate_version':3,'max_input_bytes':16000,
    'max_output_tokens':1536,'max_reserved_tokens_per_candidate_version':54000,'timeout_seconds':60,
    'max_initial_attempts_per_seat':2}


def policy_for(config):
    limits={**DEFAULT_LIMITS}
    incoming=config.get('response_correction') or {}
    if set(incoming)-set(limits):raise ValueError('unknown response correction limit')
    for key,value in incoming.items():
        if type(value) is not int or value<0 or value>DEFAULT_LIMITS[key]:raise ValueError('correction limits may only tighten defaults: '+key)
        limits[key]=value
    policy={'version':POLICY_VERSION,'prompt_hash':PROMPT_HASH,'limits':limits,
        'mode':'field_patches_only','existing_judgments':'immutable','valid_evidence_and_counter_roles':'immutable',
        'unparseable_or_semantic_conflict':'manual_review','partial_ontology_cannot_confirm_gap':True}
    if (config.get('semantic_clarification') or {}).get('enabled'):
        from .semantic_clarification import policy as clarification_policy
        policy['semantic_clarification']=clarification_policy()
    return policy


def pointer(parts):return '/'+ '/'.join(str(p).replace('~','~0').replace('/','~1') for p in parts)


def get_at(value,parts):
    for part in parts:value=value[part]
    return value


def schema_issues(value,schema,parts=()):
    """Return precise locations, preserving the schema used to constrain a repair."""
    if 'anyOf' in schema:
        if not schema_errors(value,schema):return []
        return [{'parts':list(parts),'kind':'type','schema':deepcopy(schema)}]
    types=schema.get('type');types=[types] if isinstance(types,str) else types
    tests={'null':lambda v:v is None,'object':lambda v:isinstance(v,dict),'array':lambda v:isinstance(v,list),
        'string':lambda v:isinstance(v,str),'integer':lambda v:type(v) is int,'boolean':lambda v:type(v) is bool,
        'number':lambda v:type(v) in (int,float)}
    if types and not any(t in tests and tests[t](value) for t in types):return [{'parts':list(parts),'kind':'type','schema':deepcopy(schema)}]
    if 'enum' in schema and value not in schema['enum']:return [{'parts':list(parts),'kind':'enum','schema':deepcopy(schema)}]
    issues=[]
    if isinstance(value,dict):
        props=schema.get('properties',{})
        issues += [{'parts':list(parts)+( [k]),'kind':'missing','schema':deepcopy(props[k])} for k in schema.get('required',[]) if k not in value]
        if schema.get('additionalProperties') is False:
            issues += [{'parts':list(parts)+[k],'kind':'extra','schema':{}} for k in value.keys()-props.keys()]
        for key in value.keys() & props.keys():issues += schema_issues(value[key],props[key],(*parts,key))
    if isinstance(value,list) and 'items' in schema:
        for i,item in enumerate(value):issues += schema_issues(item,schema['items'],(*parts,i))
    if isinstance(value,str) and len(value)<schema.get('minLength',0):issues.append({'parts':list(parts),'kind':'missing_text','schema':deepcopy(schema)})
    return issues


def _ids(value):
    if isinstance(value,str):return {value}
    if isinstance(value,list):return set().union(*(_ids(v) for v in value)) if value else set()
    if isinstance(value,dict):return set().union(*(_ids(v) for v in value.values())) if value else set()
    return set()


def _conflicts(raw):
    dims=raw.get('dimensions') or {};roles=raw.get('evidence_assessments') or []
    if not isinstance(dims,dict) or not isinstance(roles,list):return []
    usable=[a for a in roles if isinstance(a,dict) and isinstance(a.get('evidence_id'),str) and isinstance(a.get('role'),str)]
    counter={a['evidence_id'] for a in usable if a['role']=='counter'}
    seen=[a['evidence_id'] for a in usable]
    reasons=[]
    for i,finding in enumerate(raw.get('expression_findings') or []):
        if not isinstance(finding,dict):continue
        if finding.get('meaning_effect')=='changed' and finding.get('kind')!='semantic_error':
            reasons.append('expression_findings/'+str(i)+': changed meaning conflicts with non-semantic kind; requires semantic clarification')
    if len(seen)!=len(set(seen)):reasons.append('duplicate or conflicting evidence roles require semantic review')
    c=dims.get('counterevidence') or {}
    if isinstance(c,dict) and c.get('status')=='pass' and counter:reasons.append('material counterevidence conflicts with counterevidence=pass')
    for key in ('primary_evidence_ids','counterevidence_reviewed_ids'):
        if isinstance(raw.get(key),list) and counter & {x for x in raw[key] if isinstance(x,str)}:reasons.append(key+' overlaps material counterevidence')
    return reasons


def make_plan(raw,proposal,policy):
    """Only missing data, invalid non-judgment types and citations can be filled."""
    from .validator_contracts import contract_for
    contract=contract_for(proposal['response_contract_version'])
    if not isinstance(raw,dict):return None,['response is not reliably parseable as an object']
    if proposal.get('response_contract_version') in {'validator_response_v7','validator_response_v8'}:
        findings_schema = contract.RESPONSE_SCHEMA['properties']['expression_findings']
        if schema_errors(raw.get('expression_findings'), findings_schema):
            return None, ['representation findings are substantive judgments and cannot be filled by field correction']
    blocked=_conflicts(raw);issues=schema_issues(raw,contract.RESPONSE_SCHEMA)
    dims=raw.get('dimensions',{});evidence=proposal.get('evidence_bundle',{}).get('items',[])
    from .ontology_context import selectable_properties
    available={x['evidence_id'] for x in evidence};properties=selectable_properties(proposal)
    known={p['id'] for p in properties}
    refs=available|known
    targets=[]
    for issue in issues:
        parts=issue['parts']
        semantic=(len(parts)>=3 and parts[0]=='dimensions' and parts[-1]=='status' or
                  len(parts)>=3 and parts[0]=='evidence_assessments' and parts[-1]=='role')
        whole_existing=(parts==['dimensions'] or len(parts)<=2 and parts and parts[0]=='dimensions' and issue['kind']!='missing'
            or parts==['evidence_assessments'] and issue['kind']=='type')
        if issue['kind']=='extra' or not parts or semantic and issue['kind']!='missing' or whole_existing:
            blocked.append(pointer(parts)+': existing judgment or ambiguous structure cannot be rewritten')
        else:targets.append(issue)
    # Add proof obligations absent from a structurally valid response.
    if isinstance(dims,dict):
        for name in (*v6.SOURCE_CHECKS,'knowledge_scope','counterevidence'):
            d=dims.get(name)
            resolved=(isinstance(d,dict) and isinstance(d.get('status'),str) and (d.get('status') in {'supported','pass','not_applicable'}
                if name!='knowledge_scope' else d.get('status') in set(v6.v5.SCOPES)-{'unresolved'}))
            if resolved and d.get('evidence_ids')==[]:
                targets.append({'parts':['dimensions',name,'evidence_ids'],'kind':'missing_citations','schema':{'type':'array','minItems':1,'items':{'type':'string','enum':sorted(available)}}})
            if isinstance(d,dict) and isinstance(d.get('note'),str) and (not d['note'].strip() or d['note']=='__FILL_FROM_REVIEW__'):
                targets.append({'parts':['dimensions',name,'note'],'kind':'missing_reason','schema':{'type':'string','minLength':1}})
        mapping=dims.get('mapping')
        if isinstance(mapping,dict) and mapping.get('status')=='matched' and mapping.get('relation_id') is None:
            targets.append({'parts':['dimensions','mapping','relation_id'],'kind':'missing_formal_id','schema':{'type':'string','enum':sorted(known)}})
        source=dims.get('triple_support')
        if isinstance(source,dict) and isinstance(source.get('status'),str) and source.get('status') in {'supported','contradicted'} and raw.get('evidence_assessments')==[]:
            targets.append({'parts':['evidence_assessments'],'kind':'missing_roles','schema':deepcopy(contract.RESPONSE_SCHEMA['properties']['evidence_assessments'])})
        if isinstance(source,dict) and source.get('status')=='supported' and raw.get('primary_evidence_ids')==[] and all(
                isinstance(dims.get(n),dict) and isinstance(dims[n].get('status'),str) and dims[n].get('status') in {'supported','pass','not_applicable'} for n in v6.SOURCE_CHECKS):
            targets.append({'parts':['primary_evidence_ids'],'kind':'missing_primary_set','schema':{'type':'array','minItems':1,'items':{'type':'string','enum':sorted(available)}}})
    # Invalid references can be re-anchored; existing valid IDs cannot change.
    def walk_ids(value,parts=()):
        if isinstance(value,dict):
            for k,v in value.items():walk_ids(v,(*parts,k))
        elif isinstance(value,list):
            for i,v in enumerate(value):walk_ids(v,(*parts,i))
        elif isinstance(value,str):
            is_evidence=any(x in {'evidence_ids','primary_evidence_ids','counterevidence_reviewed_ids','evidence_id'} for x in parts)
            is_relation=any(x in {'relation_id','competing_relation_ids'} for x in parts)
            ids=available if is_evidence else known if is_relation else None
            if ids is not None and value not in ids:
                targets.append({'parts':list(parts),'kind':'invalid_reference','schema':{'type':'string','enum':sorted(ids)}})
    walk_ids(raw)
    if proposal.get('response_contract_version') in {'validator_response_v7','validator_response_v8'} and any(
            t['parts'] and t['parts'][0] == 'expression_findings' for t in targets):
        return None, ['representation findings cannot be rewritten by field correction']
    # Never permit a whole-object repair to replace an already returned dimension.
    by_path={}
    for target in sorted(targets,key=lambda t:(len(t['parts']),pointer(t['parts']))):
        parts=target['parts']
        if any(parts[:len(t['parts'])]==t['parts'] for t in by_path.values()):continue
        by_path[pointer(parts)]=target
    targets=list(by_path.values())
    if not targets or blocked:return None,blocked or ['semantic inconsistency has no safe field-only repair']
    if len(targets)>6:return None,['too many unresolved fields for a bounded repair']
    for i,target in enumerate(targets):
        target['key']='field_'+str(i);target['path']=pointer(target['parts'])
        try:value=get_at(raw,target['parts'])
        except (KeyError,IndexError,TypeError):value=None
        target['actual_type']=('missing' if target['kind']=='missing' else 'null' if value is None else
            'boolean' if type(value) is bool else 'array' if isinstance(value,list) else 'object' if isinstance(value,dict) else 'string' if isinstance(value,str) else 'number')
        if target['parts']==['dimensions','mapping']:
            target['schema']['properties']['status']['enum']=[s for s in target['schema']['properties']['status']['enum'] if s!='not_covered']
        spec=target['schema']
        if spec.get('enum')==[] or spec.get('minItems',0)>0 and spec.get('items',{}).get('enum')==[]:
            return None,[target['path']+': no frozen reference can satisfy the proof obligation']
    plan={'version':POLICY_VERSION,'original_response_hash':sha256_json(raw),'proposal_digest':proposal['proposal_digest'],
        'evidence_hash':proposal['evidence_bundle']['evidence_hash'],'targets':targets,'policy':deepcopy(policy),
        'valid_reference_ids':sorted(refs)}
    plan['plan_hash']=sha256_json(plan)
    return plan,[]


def repair_schema(plan):
    fields={t['key']:deepcopy(t['schema']) for t in plan['targets']}
    return {'type':'object','additionalProperties':False,'required':['repairs'],'properties':{
        'repairs':{'type':'object','additionalProperties':False,'required':list(fields),'properties':fields}}}


def apply_repairs(original,plan,patch):
    errors=schema_errors(patch,repair_schema(plan))
    if errors:raise ValueError('; '.join(errors))
    if sha256_json(original)!=plan['original_response_hash']:raise ValueError('original response hash changed')
    result=deepcopy(original)
    for target in plan['targets']:
        parts=target['parts'];value=deepcopy(patch['repairs'][target['key']])
        try:old=get_at(original,parts)
        except (KeyError,IndexError,TypeError):old=None
        if (_ids(old)&set(plan['valid_reference_ids'])) - _ids(value):raise ValueError(target['path']+': valid references cannot be removed')
        parent=get_at(result,parts[:-1]);parent[parts[-1]]=value
    # This check is independent of the supplied patch paths.
    before=original.get('dimensions') or {};after=result.get('dimensions') or {}
    if isinstance(before,dict):
        for name,d in before.items():
            if isinstance(d,dict) and 'status' in d and after.get(name,{}).get('status')!=d['status']:
                raise ValueError('existing dimension judgment changed: '+name)
    roles=original.get('evidence_assessments')
    if isinstance(roles,list):
        changed=result.get('evidence_assessments',[])
        for i,entry in enumerate(roles):
            if isinstance(entry,dict) and 'role' in entry and (i>=len(changed) or changed[i].get('role')!=entry['role']):
                raise ValueError('existing evidence role changed')
    if original.get('expression_findings') != result.get('expression_findings'):
        raise ValueError('existing representation findings changed')
    return result


def repair_payload(original,plan,proposal):
    candidate=proposal['candidate'];mapping_needed=any(t['parts'][:2]==['dimensions','mapping'] for t in plan['targets'])
    payload={'task_type':'triple_review_field_correction','original_response':deepcopy(original),
        'original_response_hash':plan['original_response_hash'],'proposal_digest':proposal['proposal_digest'],
        'evidence_hash':plan['evidence_hash'],'candidate':deepcopy(candidate.get('triple',{})),
        'errors':[{'key':t['key'],'path':t['path'],'kind':t['kind'],'actual_type':t['actual_type'],'expected_schema':t['schema']} for t in plan['targets']],
        'evidence':[{k:deepcopy(e[k]) for k in ('evidence_id','quote','substantive','anchor_valid','block_type','scope_id') if k in e}
            for e in proposal['evidence_bundle']['items']],
        'output_contract':repair_schema(plan),'output_template':fixed_template(repair_schema(plan)),
        'rule':'Return only repairs for the listed fields; all existing judgments, notes, valid references and evidence roles remain locked.'}
    if mapping_needed:
        triple=candidate.get('triple',{});rid=triple.get('relation_id');surface=triple.get('surface_relation')
        from .ontology_context import selectable_properties
        props=selectable_properties(proposal)
        selected=[p for p in props if p.get('id')==rid or p.get('label')==surface][:8]
        payload['ontology_context']={'scope':'selected_frozen_properties_only','full_catalog_reviewed_in_this_call':False,
            'catalog_hash':proposal.get('ontology_catalog_hash'),'properties':deepcopy(selected),'program_check':proposal.get('ontology_check'),
            'constraint':'Partial lookup cannot justify not_covered. Preserve uncertainty when formal mapping cannot be established.'}
        if proposal.get('response_contract_version')in {'validator_response_v6_3','validator_response_v7','validator_response_v8'}:
            from .ontology_context import frozen_context
            context=frozen_context(proposal)
            # Reuse frozen definitions; no fresh retrieval or full-catalog resend.
            # Exact competitors remain complete, even when that exhausts the small
            # repair envelope (the existing correction budget then stops the call).
            needed=set(context['retrieval']['required_property_ids'])
            mapping=(original.get('dimensions') or {}).get('mapping') or {}
            needed.update(mapping.get('competing_relation_ids') or [])
            if mapping.get('relation_id'):needed.add(mapping['relation_id'])
            if not needed:needed.update(p['id'] for p in context['properties'][:3])
            props=[deepcopy(p) for p in context['properties'] if p['id'] in needed]
            classes={c['id']:c for c in context['classes']}
            wanted={x.get('class_id') for x in context['retrieval']['endpoint_queries'].values() if x.get('class_id')}
            for prop in props:
                for field in ('domain','range'):
                    values=prop.get(field) or []
                    wanted.update(v for v in ([values] if isinstance(values,str) else values) if v in classes)
            stack=list(wanted)
            while stack:
                for parent in classes.get(stack.pop(),{}).get('parents',[]):
                    if parent in classes and parent not in wanted:wanted.add(parent);stack.append(parent)
            payload['ontology_context']={'scope':'selected_frozen_properties_only','properties':props,
                'classes':[deepcopy(classes[x]) for x in sorted(wanted)],
                'source_context_hash':proposal['ontology_context_hash'],'full_catalog_reviewed_in_this_call':False,
                'absence_confirmed':False,'constraint':'A partial repair lookup cannot confirm a global ontology gap.'}
            payload['ontology_context_hash']=proposal['ontology_context_hash']
    return payload
