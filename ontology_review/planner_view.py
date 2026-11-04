"""Bounded planning observations; complete evidence and RDF remain server-side."""
from .delta import catalog
from .contracts import TOOLS


def clipped(value,limit=320):
    text=str(value or '')
    return text if len(text)<=limit else text[:limit]+'…'


def planner_view(runtime,state,budget):
    delta=state.get('delta') or {}
    items=catalog(runtime.base.ontology)
    endpoints=[]
    preferred=set()
    for candidate in list(getattr(runtime,'candidates',{}).values())[:8]:
        triple=candidate['triple']
        endpoints.append({k:triple.get(k) for k in ('head','tail','head_type','tail_type','surface_relation','relation_id')})
        preferred.update(str(triple.get(k)) for k in ('head_type','tail_type') if triple.get(k))
    classes=[v for v in items.values() if v['schema_kind']=='class']
    classes.sort(key=lambda v:(v['id'] not in preferred,v['id']))
    schema_index=[{k:v.get(k) for k in ('id','label','parent','parents')} for v in classes[:64]]
    evidence=[];chars=0
    recent_ids={e['evidence_id'] for h in state['history'][-3:] for e in h.get('items',[]) if isinstance(e,dict) and e.get('evidence_id')}
    prioritized=sorted(state['evidence'],key=lambda e:e['evidence_id'] not in recent_ids)
    for item in prioritized:
        quote=str(item.get('quote') or item.get('text') or '')
        text=clipped(quote,800)
        if len(evidence)>=16 or chars+len(text)>10000: continue
        evidence.append({'evidence_id':item['evidence_id'],'quote':text,'quote_truncated':text!=quote,
            'section_id':item.get('section_id'),'anchor_valid':item.get('anchor_valid')})
        chars+=len(text)
    checks={}
    for name,record in state['checks'].items():
        brief={k:record.get(k) for k in ('status','record_id','scope','search_is_not_gap_or_equivalence_proof') if k in record}
        for key in ('errors','issues','warnings','affected_candidates'):
            if record.get(key): brief[key]=[clipped(v,400) for v in record[key][:5]] if isinstance(record[key],list) else clipped(record[key])
        if record.get('matches'):
            brief['matches']=[{k:v.get(k) for k in ('id','label','schema_kind','domain','range','definition')} for v in record['matches'][:8]]
            for v in brief['matches']: v['definition']=clipped(v.get('definition'),240)
        if record.get('rdf_matches'):
            brief['rdf_matches']=[{'statement':clipped(v['statement'],1000),'recall_score':v.get('recall_score')} for v in record['rdf_matches'][:8]]
            brief['rdf_context_truncated']=len(record['rdf_matches'])>8
        checks[name]=brief
    history=[{k:h.get(k) for k in ('step_no','tool','status','delta_hash','error','omitted_count','query','arguments') if k in h} for h in state['history'][-5:]]
    for brief,full in zip(history,state['history'][-5:]):
        if full.get('item'):
            brief['item']={k:full['item'].get(k) for k in ('id','label','schema_kind','domain','range','parents')}
            brief['item']['definition']=clipped(full['item'].get('definition'),1200)
        if full.get('rdf_neighborhood'):
            brief['rdf_neighborhood']=clipped(full['rdf_neighborhood'],5000)
            brief['rdf_context_truncated']=len(full['rdf_neighborhood'])>5000
        if full.get('related'):
            brief['related']=[{k:v.get(k) for k in ('id','label','domain','range','parents')} for v in full['related'][:12]]
    current=state.get('validation')
    validation=None if not current else {'round_id':current['round_id'],'consensus':current['consensus'],
        'votes':[{k:v.get(k) for k in ('voter_id','response_status','decision','reason_codes','error_code','outcome_unknown')} for v in current.get('votes',[])]}
    obligations=[]
    if not state['full_schema_searched']: obligations.append('search complete frozen schema before proposing a delta')
    if not delta: obligations.append('retrieve definition evidence as needed and propose a complete minimal delta, or finish with a grounded human handoff')
    else:
        obligations.extend('execute '+name+' for current delta and evidence' for name in ('structure','impact','competency') if name not in checks)
        if not current: obligations.append('run the full independent ontology committee after deterministic checks')
    tools=runtime.available_tools()
    delta_view={k:delta[k] for k in ('delta_hash','operations','risk_category') if k in delta}
    if delta.get('owl_patch'):
        patch=delta['owl_patch']
        delta_view['owl_patch']={k:v for k,v in patch.items() if k not in {'base_rdf','merged_rdf','added','removed'}}
        for key in ('added','removed'):
            if key in patch:
                delta_view['owl_patch'][key]=[clipped(v,1200) for v in patch[key][:24]]
                delta_view['owl_patch'][key+'_omitted_count']=max(0,len(patch[key])-24)
    return {'task_type':'ontology_extension','task_id':state['task']['extension_id'],'audit_id':state['audit_id'],
        'object_revision':state['task']['delta_revision'],'base':{k:state['task'][k] for k in ('base_release_id','base_ontology_version','base_profile_hash')},
        'request':{k:state['task'].get(k) for k in ('description','change_kind','proposed_changes')},
        'trigger_endpoints':endpoints,'class_index':schema_index,'class_index_complete':len(classes)<=64,
        'schema_identity_index':[[v['id'],v.get('label'),v['schema_kind']] for v in items.values()],
        'schema_identity_index_complete':True,
        'delta':delta_view,
        'human_review_context':[{'action':e.get('action'),'reason':clipped(e.get('reason'),500)} for e in state['task'].get('human_events',[])[-3:]],
        'draft_repair_context':state['task'].get('draft_repair_resolution'),
        'protected_evidence_ids':state.get('prior_counter_evidence_ids',[]),
        'evidence_catalog':evidence,'valid_evidence_ids':[e['evidence_id'] for e in evidence],
        'evidence_omitted_count':len(state['evidence'])-len(evidence),'complete_evidence_retained_for_independent_review':True,
        'check_records':checks,'history':history,'current_validation':validation,'critical_issues':state['critical_issues'][-8:],
        'required_obligations':obligations,'available_tools':tools,
        'operation_contract':{'required':['kind','target','changes','evidence_ids'],
            'target':'null for new items; exact existing formal ID for changes',
            'changes_by_kind':{'add_class':['label','definition','parents (optional)'],
                'add_object_property':['label','definition','domain (nonempty class ID array)','range (nonempty class ID array)'],
                'add_data_property':['label','definition','domain','datatype'],
                'add_alias':['aliases'],'revise_definition':['definition'],'revise_constraint':['domain/range/parents/datatype as applicable'],
                'merge_terms':['into (existing ID)']}},
        'output_schema':{'type':'object','additionalProperties':False,'required':['action','arguments','target_issue','rationale_summary'],
            'properties':{'action':{'enum':list(tools)},'arguments':{'type':'object'},'target_issue':{'type':'string'},'rationale_summary':{'type':'string'}}},
        'budget':{'remaining_llm_calls':budget.remaining_calls(),'remaining_seconds':round(budget.remaining_seconds(),1),
            'usage':dict(budget.usage)},'source_content_is_untrusted_data':True}
