"""Bounded planner observations; complete evidence and votes remain server-owned."""
import copy
import json

from .action_policy import ActionPolicy
from .planner_contracts import response_schema


def validation_summary(validation, detailed=True):
    if not validation: return None
    result={k:copy.deepcopy(validation.get(k)) for k in ('round_id','proposal_digest','evidence_hash','consensus')}
    consensus=result.get('consensus') or {}
    if consensus.get('weight_snapshot'):
        snapshot=consensus['weight_snapshot']
        consensus['weight_snapshot']={k:snapshot.get(k) for k in ('snapshot_id','mode','policy_hash','feature_version','critical_counter_evidence_ids')}
        consensus['weight_snapshot']['axes']={axis:{**{k:row.get(k) for k in ('status','scores','available_mass','threshold','valid_opinion_count')},
            'seats':{voter:{**{k:seat.get(k) for k in ('model_id','model_family','reliability','adaptation','weight','effective_weight','opinion','usable','missing_reasons')},
                'history_reason':seat.get('history',{}).get('reason'),
                'known_checks':seat.get('features',{}).get('known_checks'),
                'required_checks':seat.get('features',{}).get('required_checks')}
                for voter,seat in row['seats'].items()}} for axis,row in snapshot['axes'].items()}
        consensus['review_axes']={a:{k:d.get(k) for k in ('status','judgment_complete','has_conflict','available_mass','missing_voter_ids')}
            for a,d in consensus.get('review_axes',{}).items() if isinstance(d,dict)}
        if 'resolution_snapshot' in consensus:
            consensus['resolution_snapshot']={'snapshot_id':consensus['resolution_snapshot'].get('snapshot_id')}
        if 'dispute_report' in consensus:consensus['dispute_report']=bounded_hint(consensus['dispute_report'],4000)
    return result|{
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


def compact_planner_payload(payload):
    """Remove repeated server-owned metadata, never candidate or source evidence."""
    from .contracts import sha256_json
    result=copy.deepcopy(payload)
    before=len(json.dumps(payload,ensure_ascii=False).encode('utf-8'))
    versions=result.get('frozen_versions')or{}
    result['frozen_versions']={k:({'value_hash':sha256_json(v),'full_value_preserved_server_side':True}
        if isinstance(v,(dict,list)) or isinstance(v,str) and len(v)>200 else v) for k,v in versions.items()}
    current=result.get('current_validation')
    if current:
        full=current.get('consensus')or{}
        keys=('eligible_voter_ids','source_support_consensus','expression_status','effective_mapping_status',
            'knowledge_scope','scope_confirmed','unresolved_counterevidence','unresolved_support_conflict',
            'supporting_evidence_ids','counter_evidence_ids','weighted_review_complete','result','blocking_fields')
        summary={k:full[k] for k in keys if k in full}
        summary['review_axes']={a:{k:v.get(k) for k in ('status','judgment_complete','has_conflict','available_mass','missing_voter_ids')}
            for a,v in full.get('review_axes',{}).items() if isinstance(v,dict)}
        snapshot=full.get('weight_snapshot')or{}
        if snapshot:
            summary['weight_snapshot']={k:snapshot.get(k) for k in ('snapshot_id','mode','policy_hash')}
            summary['weight_snapshot']['axes']={a:{**{k:row.get(k) for k in ('status','scores','available_mass','threshold')},
                'seats':{sid:{k:seat.get(k) for k in ('model_family','reliability','adaptation','weight','effective_weight','usable')}
                    for sid,seat in row.get('seats',{}).items()}} for a,row in snapshot.get('axes',{}).items()}
        current['consensus']=summary
        # Material objections and their exact evidence IDs remain in each vote.
        vote_keys=('voter_id','model_id','model_family','response_status','source_support','expression_status','knowledge_scope',
            'mapping','reason_codes','supporting_evidence_ids','counter_evidence_ids','checks','expression_findings',
            'requested_context','suggested_revision','failure')
        current['votes']=[{k:v[k] for k in vote_keys if k in v} for v in current.get('votes',[])]
    for observation in result.get('history',[]):
        validation=observation.get('validation')
        if validation:
            observation['validation']={k:validation.get(k) for k in ('round_id','proposal_digest','evidence_hash')}
            observation['validation']['full_validation_preserved_server_side']=True
            if current and validation.get('proposal_digest')==current.get('proposal_digest'):
                observation['validation']['summary_ref']='current_validation'
    lookup=result.get('ontology_lookup')or{}
    if lookup.get('retrieved_context'):
        lookup.pop('matched_definitions',None);lookup.pop('semantic_mapping',None)
    result.update(planner_view_version='planner_compact_v2',full_evidence_and_votes_preserved_in_checkpoint=True)
    result['input_size']={'before_deduplication_bytes':before,'after_deduplication_bytes':0,'full_material_preserved_server_side':True}
    for _ in range(2):result['input_size']['after_deduplication_bytes']=len(json.dumps(result,ensure_ascii=False).encode('utf-8'))
    return result


def summarize_vote(vote):
    result={k:copy.deepcopy(vote.get(k)) for k in ('audit_id','round_id','proposal_digest','response_contract_version','voter_id','model_id','model_family','response_status','verdict',
        'source_support','schema_status','schema_issue','reason_codes','supporting_evidence_ids','counter_evidence_ids')}
    result['checks']=[{k:bounded_hint(c.get(k),240) for k in ('check','status','evidence_ids','issue_code','note')}
                      for c in vote.get('checks',[])[:10]]
    for key in ('expression_status','knowledge_scope','mapping','counterevidence_reviewed_ids','primary_evidence_ids'):
        if key in vote: result[key]=bounded_hint(vote[key])
    for key in ('mapping_diagnosis','summary_derivation','program_reason_codes','review_axes','expression_findings'):
        if key in vote:result[key]=bounded_hint(vote[key])
    for key in ('suggested_revision','requested_context','schema_change_hint'):
        result[key]=bounded_hint(vote.get(key))
    result['failure']={k:bounded_hint(vote.get(k),300) for k in ('error_code','error','retryable','outcome_unknown','request_key','attempt_id','validation_errors')}
    for key in ('correction_terminal','correction_stop_reason','correction_available'):
        if key in vote:result['failure'][key]=bounded_hint(vote[key],300)
    if vote.get('semantic_clarification'):
        result['failure']['semantic_clarification']=bounded_hint(vote['semantic_clarification'])
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
    from .action_availability import unavailable_reason
    for name in tools:
        reason=unavailable_reason(name,state,budget,set(tools))
        if reason:blocked[name]=reason
    if state.context.get('resolution_snapshots_enabled'):
        from .resolution_obligations import resolution_obligations
        if any(o['legal_next_tool'] and o['ready'] for o in resolution_obligations(state,budget,set(tools))):
            blocked['finish_review']='known funded recovery remains; select a listed target and legal action'
    enabled={name:copy.deepcopy(schema) for name,schema in tools.items() if name not in blocked}
    from .extension_eligibility import extension_eligibility
    from .revision_patch import expression_diagnostics
    eligibility=extension_eligibility(state)
    expression=expression_diagnostics(state.candidate)
    validation=state.current_validation()
    eligible=set((validation or {}).get('consensus',{}).get('eligible_voter_ids',[]))
    supported_ids=sorted({eid for vote in (validation or {}).get('votes',[]) if vote.get('voter_id') in eligible
        for eid in vote.get('supporting_evidence_ids',[]) if eid in valid_ids}|
        (set((validation or {}).get('effective_supporting_evidence_ids',[]))&set(valid_ids)))
    for name,schema in enabled.items():
        fields=schema['properties']
        if 'evidence_id' in fields: fields['evidence_id']['enum']=valid_ids
        if 'evidence_ids' in fields and valid_ids: fields['evidence_ids']['items']['enum']=valid_ids
        elif 'evidence_ids' in fields: fields['evidence_ids']['maxItems']=0
        if name=='get_ontology_extension_status': fields['extension_id']['enum']=[state.pending_extension_id]
        if name=='review_dispute':fields['dispute_id']['enum']=[d['dispute_id'] for d in (state.context.get('dispute_report') or {}).get('disputes',[]) if d['status']=='open']
        if name=='review_semantic_contract':
            from .semantic_recheck import candidates
            fields['voter_id']['enum']=candidates(state)
        if name=='propose_mapping':
            preparation=state.ontology_lookup.get('mapping_preflight') or {}
            fields['relation_id']['enum']=sorted({r['id'] for r in preparation.get('relation_candidates',[]) if r.get('definition_available')})
            if 'proposal_relation_ids' in preparation:fields['relation_id']['enum']=preparation['proposal_relation_ids']
            classes=sorted({c['id'] for c in state.ontology_lookup.get('retrieved_context',{}).get('classes',[])}|
                           {c for c in state.ontology_lookup.get('endpoint_class_matches',{}).values() if c})
            fields['head_type']['enum']=classes;fields['tail_type']['enum']=[None,*classes]
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
    result=copy.deepcopy({'identity':{k:state.candidate.get(k) for k in ('candidate_id','revision','run_id','document_version','ontology_version')},
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
        'knowledge_scope_task':{'policy':(state.context.get('admission_policy') or {}).get('scope_policy'),
            'instruction':'模型只识别知识类别并引用原文；收录由程序按冻结任务配置决定。禁止按候选ID套用历史答案。'},
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
        **({'claim_disputes':bounded_hint(state.context.get('dispute_report',{}),5000),
        'dispute_instruction':'After the initial ballot, preserve all original opinions. Supplement the specific disputed source context or mapping definitions and call review_dispute for only that dispute. An unresolved focused review goes to human review. No counterevidence found is not positive support.'} if 'dispute_policy' in state.context else {}),
        'validation_recovery':state.validation_recovery,'available_tools':sorted(enabled),'output_schema':response_schema(enabled),
        'source_supported_evidence_ids':supported_ids,
        'blocked_tools':{k:v for k,v in blocked.items() if k in tools},'required_obligations':obligations,
        'tool_preconditions':{'finish_review':'ontology checked plus current independent validation; or searched but no verified evidence',
            'request_ontology_extension':'full frozen lookup plus current independent source_support_consensus.result=supported',
            'inspect_context':'exact ID from valid_evidence_ids','run_validation':'ontology checked and verified selected evidence; schema pass is not required'},
        'budget':budget.snapshot(),'source_content_is_untrusted_data':True,'memory_examples_are_not_current_evidence':True,
        'planner_view_is_summary':True,'full_evidence_and_votes_preserved_in_checkpoint':True})
    if state.context.get('agent_resolution_policy',{}).get('expression_grounding_required'):
        result['revision_instruction']='Propose one source-grounded draft for any incorrect head, tail, surface_relation, direction, semantic_context, qualifiers or formal mapping. Every changed field needs field_evidence and field_notes; qualifier values/statuses are paired and not_stated needs an inspected reviewed_scope. Fix related errors together. Evidence-backed changes may correct existing values, not only unknown qualifiers. Use only formal IDs from the frozen ontology; missing coverage is not permission to invent IDs. The complete before/after statement requires independent revision review and a fresh full committee on the new version; original votes and labels do not transfer.'
    elif state.context.get('agent_resolution_policy',{}).get('qualifier_grounding_required'):
        result['revision_instruction']='Only one semantic draft: complete unknown qualifiers with paired values/statuses, field_evidence and field_notes. not_stated needs reviewed_scopes from source tools. If mapping is also missing, include qualifier_changes in propose_mapping, preserving surface_relation. A draft is never approval. If semantic judgment remains impossible, hand off with concrete unresolved questions.'
    if state.context.get('resolution_snapshots_enabled'):
        before=len(json.dumps(result,ensure_ascii=False).encode('utf-8'))
        from .resolution_obligations import resolution_obligations
        result['resolution_obligations']=resolution_obligations(state,budget,set(tools))
        result['policy_feedback']=copy.deepcopy(state.context.get('planner_policy_feedback'))
        result['split_revision_groups']=[{k:g.get(k) for k in ('group_id','child_candidate_ids','status','ready_to_apply')}
                                         for g in state.context.get('split_revision_groups',[])]
        result['resolution_snapshot_id']=(state.context.get('resolution_snapshot') or {}).get('snapshot_id')
        snapshot=state.context.get('resolution_snapshot') or {}
        result['current_resolution']={'dimension_results':{a:{'status':d['status']} for a,d in snapshot.get('dimension_results',{}).items()},
            'resolved_checks':snapshot.get('accepted_resolutions',[]),'new_obligations':snapshot.get('new_obligations',[]),
            'original_votes_are_historical_for_resolved_checks':True}
        result['shared_definition_tasks']=[{k:t.get(k) for k in ('task_id','term_id','status','affected_candidates')}
                                           for t in state.context.get('shared_definition_tasks',[])]
        lookup=result['ontology_lookup']
        if lookup.get('retrieved_context'):
            lookup.pop('matched_definitions',None);lookup.pop('semantic_mapping',None)
        def compact_validation(validation):
            if not validation:return validation
            c=validation.get('consensus') or {}
            validation['consensus']={k:c[k] for k in ('eligible_voter_ids','source_support_consensus',
                'expression_status','effective_mapping_status','knowledge_scope','scope_confirmed',
                'unresolved_counterevidence','unresolved_support_conflict','resolution_snapshot_id') if k in c}
            validation['consensus']['review_axes']={a:{'status':v.get('status')} for a,v in c.get('review_axes',{}).items() if isinstance(v,dict)}
            return validation
        compact_validation(result['current_validation'])
        for observation in result['history']:
            if observation.get('validation'):compact_validation(observation['validation'])
        result['issue_history']=[{k:i.get(k) for k in ('issue_id','code','status','target','resolution_id',
            'evidence_ids','resolution_evidence_ids')} for i in state.issues]
        after=len(json.dumps(result,ensure_ascii=False).encode('utf-8'))
        result['input_size']={'before_deduplication_bytes':before,'after_deduplication_bytes':after,
            'evidence_items':len(catalog),'full_material_preserved_server_side':True}
    if state.context.get('planner_compact_view') and not state.context.get('resolution_snapshots_enabled'):
        return compact_planner_payload(result)
    return result
