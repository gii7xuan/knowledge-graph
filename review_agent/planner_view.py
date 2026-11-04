"""Bounded planner observations; complete evidence and votes remain server-owned."""
import copy

from .action_policy import ActionPolicy
from .planner_contracts import response_schema


def validation_summary(validation, detailed=True):
    if not validation: return None
    return {k:copy.deepcopy(validation.get(k)) for k in ('round_id','proposal_digest','evidence_hash','consensus')}|{
        'votes':[summarize_vote(v) if detailed else {k:copy.deepcopy(v.get(k)) for k in
            ('voter_id','response_status','source_support','reason_codes','supporting_evidence_ids','counter_evidence_ids')}
            for v in validation.get('votes',[])]}


def bounded_hint(value, remaining=1800, depth=0):
    budget=[remaining]
    def visit(item,level):
        if budget[0]<=0 or level>5: return '[bounded]'
        if isinstance(item,str):
            result=item[:budget[0]];budget[0]-=len(result);return result
        if isinstance(item,list):
            result=[]
            for child in item[:8]:
                if budget[0]<=0: break
                result.append(visit(child,level+1));budget[0]-=2
            return result
        if isinstance(item,dict):
            result={}
            for key,child in list(item.items())[:16]:
                if budget[0]<=0: break
                key=str(key)[:80];budget[0]-=len(key)+5
                result[key]=visit(child,level+1)
            return result
        budget[0]-=8
        return item if item is None or isinstance(item,(int,float,bool)) else str(item)[:120]
    return visit(value,depth)


def summarize_vote(vote):
    result={k:copy.deepcopy(vote.get(k)) for k in ('audit_id','round_id','proposal_digest','response_contract_version','voter_id','model_id','model_family','response_status','verdict',
        'source_support','schema_status','schema_issue','reason_codes','supporting_evidence_ids','counter_evidence_ids')}
    result['checks']=[{k:bounded_hint(c.get(k),240) for k in ('check','status','evidence_ids','issue_code','note')}
                      for c in vote.get('checks',[])[:10]]
    for key in ('expression_status','knowledge_scope','mapping','counterevidence_reviewed_ids','primary_evidence_ids'):
        if key in vote: result[key]=bounded_hint(vote[key])
    for key in ('suggested_revision','requested_context','schema_change_hint'):
        result[key]=bounded_hint(vote.get(key))
    result['failure']={k:bounded_hint(vote.get(k),300) for k in ('error_code','error','retryable','outcome_unknown','request_key','attempt_id','validation_errors')}
    return result


def observation_summary(observation):
    fields=('step_no','tool','status','error','arguments','check','scope','query','purpose','new_evidence_count',
            'evidence_id','anchor_valid','stop_reason','extension_id','workflow_state','candidate_unchanged')
    result={k:copy.deepcopy(observation[k]) for k in fields if k in observation}
    if 'validation' in observation: result['validation']=validation_summary(observation['validation'],detailed=False)
    if 'draft' in observation: result['draft']=bounded_hint({k:observation['draft'].get(k) for k in
        ('draft_id','draft_hash','status','changes','evidence_ids','reason')})
    if 'context' in observation:
        result['context_evidence_available_in_catalog']=True
    return result


def planner_view(state,budget,tools):
    valid_ids=sorted({x['evidence_id'] for x in state.archive if x.get('anchor_valid') is True})
    selected={x['evidence_id'] for x in state.bundle.get('items',[])}
    blocked=ActionPolicy.blocked_tools(state)
    enabled={name:copy.deepcopy(schema) for name,schema in tools.items() if name not in blocked}
    from .extension_eligibility import extension_eligibility
    from .revision_patch import expression_diagnostics
    eligibility=extension_eligibility(state)
    expression=expression_diagnostics(state.candidate)
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
        if name in {'review_revision','apply_reviewed_revision'}:
            status='proposed' if name=='review_revision' else 'approved'
            fields['draft_id']['enum']=[d['draft_id'] for d in state.revision_drafts if d.get('status')==status or
                name=='review_revision' and d.get('status')=='review_incomplete' and d.get('review_recovery_available')]
        if name=='request_ontology_extension':
            fields['change_kind']['enum']=eligibility['allowed_change_kinds']
            from .extension_eligibility import proposed_change_schemas
            shapes=proposed_change_schemas()
            schema['allOf']=[{'if':{'properties':{'change_kind':{'const':kind}}},
                              'then':{'properties':{'proposed_changes':shapes[kind]}}}
                             for kind in eligibility['allowed_change_kinds']]
            fields['evidence_ids']['items']['enum']=supported_ids
            fields['evidence_ids']['minItems']=1
    catalog=[]
    remaining=int(budget.config.get('max_planner_evidence_chars',budget.config.get('max_evidence_chars',12000)))
    ordered=sorted(enumerate(state.archive),key=lambda pair:(pair[1]['evidence_id'] not in selected,pair[0]))
    for _,item in ordered:
        if item.get('anchor_valid') is not True: continue
        row={k:copy.deepcopy(item[k]) for k in ('evidence_id','anchor_valid','anchor_status','source_anchor_kind',
            'document_id','document_version','section_id','chunk_id','start','end','offset_basis',
            'retrieval_level','review_obligation','block_type','substantive','requires_image','linked_evidence_ids','scope_id') if k in item}
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
    if state.current_validation() is None and state.bundle.get('items') and 'run_validation' in enabled: obligations.append('run_validation')
    return copy.deepcopy({'identity':{k:state.candidate.get(k) for k in ('candidate_id','revision','run_id','document_version','ontology_version')},
        'audit_id':state.audit_id,'state_version':state.state_version,'candidate':state.candidate.get('triple',{}),
        'extraction_hints':{'status':'unconfirmed_extraction_hints_not_source_evidence',
                            'values':bounded_hint(state.candidate.get('extraction_hints',{}))},
        **expression,'extension_eligibility':eligibility,
        'raw_source_support_votes':[{k:v.get(k) for k in ('voter_id','source_support','response_status')}
                                    for v in (validation or {}).get('votes',[])],
        'blockers':([{'code':'QUALIFIER_UNKNOWN','fields':expression['blocking_fields'],
                      'legal_next_actions':['search_evidence','propose_revision']}] if not expression['expression_complete'] else [])+
            ([{'code':'MAPPING_AMBIGUOUS','legal_next_actions':['search_evidence','inspect_context','propose_revision']}]
             if 'RELATION_MAPPING_AMBIGUOUS' in state.guard.get('reason_codes',[]) else [])+
            [{'code':'REQUEST_OUTCOME_UNKNOWN' if v.get('outcome_unknown') else 'MISSING_COMMITTEE_SEAT',
              'voter_id':v.get('voter_id'),'legal_next_actions':[name for name in ('recover_validation','finish_review') if name in enabled]}
             for v in (validation or {}).get('votes',[]) if v.get('response_status')!='ok'],
        'frozen_versions':state.context.get('agent_versions'),'evidence_catalog':catalog,'valid_evidence_ids':valid_ids,
        'selected_evidence_ids':sorted(selected),'ontology_check':state.guard,'ontology_checked':state.ontology_checked,
        'unresolved_issues':[i for i in state.issues if i.get('status','open')!='resolved'],
        'issue_history':state.issues,'evidence_exclusions':state.evidence_exclusions,
        'ontology_lookup':state.ontology_lookup,'workflow_state':state.workflow_state,'pending_extension_id':state.pending_extension_id,
        'history':[observation_summary(o) for o in state.observations[-6:]],'history_total':len(state.observations),
        'current_validation':validation_summary(state.current_validation()),
        'previous_votes':validation_summary({'votes':state.votes[-8:]})['votes'] if validation is None else [],
        'previous_votes_are_historical_not_current_validation':state.current_validation() is None,
        'revision_drafts':[bounded_hint({k:d.get(k) for k in ('draft_id','draft_hash','base_revision','status','changes','evidence_ids','reason')})
                           for d in state.revision_drafts[-4:]],
        'validation_recovery':state.validation_recovery,'available_tools':sorted(enabled),'output_schema':response_schema(enabled),
        'source_supported_evidence_ids':supported_ids,
        'blocked_tools':{k:v for k,v in blocked.items() if k in tools},'required_obligations':obligations,
        'tool_preconditions':{'finish_review':'ontology checked plus current independent validation; or searched but no verified evidence',
            'request_ontology_extension':'full frozen lookup plus current independent source_support_consensus.result=supported',
            'inspect_context':'exact ID from valid_evidence_ids','run_validation':'ontology checked and verified selected evidence; schema pass is not required'},
        'budget':budget.snapshot(),'source_content_is_untrusted_data':True,'memory_examples_are_not_current_evidence':True,
        'planner_view_is_summary':True,'full_evidence_and_votes_preserved_in_checkpoint':True})
