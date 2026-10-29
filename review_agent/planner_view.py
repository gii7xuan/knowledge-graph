"""Bounded planner observations; complete evidence and votes remain server-owned."""
import copy

from .action_policy import ActionPolicy
from .planner_contracts import response_schema


def validation_summary(validation):
    if not validation: return None
    return {k:copy.deepcopy(validation.get(k)) for k in ('round_id','proposal_digest','evidence_hash','consensus')}|{
        'votes':[{k:v.get(k) for k in ('voter_id','model_id','model_family','response_status','verdict',
            'source_support','schema_status','schema_issue','reason_codes','supporting_evidence_ids','counter_evidence_ids')}
            for v in validation.get('votes',[])]}


def observation_summary(observation):
    fields=('step_no','tool','status','error','arguments','check','scope','query','purpose','new_evidence_count',
            'evidence_id','anchor_valid','stop_reason','extension_id','workflow_state','candidate_unchanged')
    result={k:copy.deepcopy(observation[k]) for k in fields if k in observation}
    if 'validation' in observation: result['validation']=validation_summary(observation['validation'])
    if 'draft' in observation: result['draft']=copy.deepcopy(observation['draft'])
    if 'context' in observation:
        result['context_evidence_available_in_catalog']=True
    return result


def planner_view(state,budget,tools):
    valid_ids=sorted({x['evidence_id'] for x in state.archive if x.get('anchor_valid') is True})
    selected={x['evidence_id'] for x in state.bundle.get('items',[])}
    blocked=ActionPolicy.blocked_tools(state)
    enabled={name:copy.deepcopy(schema) for name,schema in tools.items() if name not in blocked}
    validation=state.current_validation()
    eligible=set((validation or {}).get('consensus',{}).get('eligible_voter_ids',[]))
    supported_ids=sorted({eid for vote in (validation or {}).get('votes',[]) if vote.get('voter_id') in eligible
        for eid in vote.get('supporting_evidence_ids',[]) if eid in valid_ids})
    for name,schema in enabled.items():
        fields=schema['properties']
        if 'evidence_id' in fields: fields['evidence_id']['enum']=valid_ids
        if 'evidence_ids' in fields and valid_ids: fields['evidence_ids']['items']['enum']=valid_ids
        elif 'evidence_ids' in fields: fields['evidence_ids']['maxItems']=0
        if name=='get_ontology_extension_status': fields['extension_id']['enum']=[state.pending_extension_id]
        if name=='request_ontology_extension':
            fields['evidence_ids']['items']['enum']=supported_ids
            fields['evidence_ids']['minItems']=1
    catalog=[]
    remaining=int(budget.config.get('max_planner_evidence_chars',budget.config.get('max_evidence_chars',12000)))
    ordered=sorted(enumerate(state.archive),key=lambda pair:(pair[1]['evidence_id'] not in selected,pair[0]))
    for _,item in ordered:
        if item.get('anchor_valid') is not True: continue
        row={k:copy.deepcopy(item[k]) for k in ('evidence_id','anchor_valid','anchor_status','source_anchor_kind',
            'document_id','document_version','section_id','chunk_id','start','end','offset_basis',
            'retrieval_level','review_obligation') if k in item}
        quote=str(item.get('quote') or '')
        if len(quote)<=remaining:
            row['quote']=quote;remaining-=len(quote)
        else:
            row.update(quote_preview=quote[:160],quote_omitted_from_planner=True,full_quote_characters=len(quote))
        row['selected_for_committee']=item['evidence_id'] in selected
        catalog.append(row)
    obligations=[]
    if not state.ontology_checked: obligations.append('inspect_ontology')
    if not state.bundle.get('items') and not state.searched_scopes: obligations.append('search_evidence')
    if state.current_validation() is None and state.bundle.get('items'): obligations.append('run_validation')
    return copy.deepcopy({'identity':{k:state.candidate.get(k) for k in ('candidate_id','revision','run_id','document_version','ontology_version')},
        'audit_id':state.audit_id,'state_version':state.state_version,'candidate':state.candidate.get('triple',{}),
        'frozen_versions':state.context.get('agent_versions'),'evidence_catalog':catalog,'valid_evidence_ids':valid_ids,
        'selected_evidence_ids':sorted(selected),'ontology_check':state.guard,'ontology_checked':state.ontology_checked,
        'unresolved_issues':[i for i in state.issues if i.get('status','open')!='resolved'],
        'issue_history':state.issues,'evidence_exclusions':state.evidence_exclusions,
        'ontology_lookup':state.ontology_lookup,'workflow_state':state.workflow_state,'pending_extension_id':state.pending_extension_id,
        'history':[observation_summary(o) for o in state.observations[-6:]],'history_total':len(state.observations),
        'current_validation':validation_summary(state.current_validation()),
        'previous_votes':validation_summary({'votes':state.votes})['votes'],
        'revision_drafts':state.revision_drafts,'available_tools':sorted(enabled),'output_schema':response_schema(enabled),
        'source_supported_evidence_ids':supported_ids,
        'blocked_tools':{k:v for k,v in blocked.items() if k in tools},'required_obligations':obligations,
        'tool_preconditions':{'finish_review':'ontology checked plus current independent validation; or searched but no verified evidence',
            'request_ontology_extension':'full frozen lookup plus current independent source_support_consensus.result=supported',
            'inspect_context':'exact ID from valid_evidence_ids','run_validation':'ontology checked and verified selected evidence; schema pass is not required'},
        'budget':budget.snapshot(),'source_content_is_untrusted_data':True,'memory_examples_are_not_current_evidence':True,
        'planner_view_is_summary':True,'full_evidence_and_votes_preserved_in_checkpoint':True})
