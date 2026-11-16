"""Bounded next steps depend on concrete information gain, not planner completion prose."""
from .contracts import sha256_json


def attempt_targets(action,state):
    name=action['action'];args=action['arguments']
    if name=='review_dispute':return [args['dispute_id']]
    if name=='review_semantic_contract':return ['semantic:'+args['voter_id']]
    if name in {'review_revision','apply_reviewed_revision'}:return [('draft:' if name=='review_revision' else 'apply:')+args['draft_id']]
    if name in {'review_split_revision','apply_reviewed_split'}:return ['split:'+args['group_id']]
    from .revision_patch import expression_diagnostics
    expression_id='expression:'+sha256_json(state.candidate)[:12]
    incomplete=not expression_diagnostics(state.candidate)['expression_complete']
    if name=='propose_revision' and incomplete:return [expression_id]
    if name in {'search_evidence','inspect_context'}:
        known={d['dispute_id'] for d in (state.context.get('dispute_report') or {}).get('disputes',[]) if d['status']=='open'}
        known|={i['issue_id'] for i in state.issues if i.get('status','open')!='resolved' and i['code']=='EVIDENCE_INSUFFICIENT'}
        if incomplete:known.add(expression_id)
        return [action['target_issue']] if action['target_issue'] in known else sorted(known) or ['source_supplement']
    return []


def resolution_obligations(state,budget=None):
    if not state.context.get('resolution_snapshots_enabled'):return []
    from .semantic_recheck import candidates
    result=[];policy=state.context.get('agent_resolution_policy') or {}
    maximum=int(policy.get('max_attempts_per_issue',2))
    attempts=state.context.get('resolution_attempts') or {}
    def add(identity,kind,tool,reason,*,ready=False,arguments=None):
        n=attempts.get(identity,0)
        reserve=int(policy.get('reserve_final_committee_calls',3))
        remaining=budget.remaining_calls() if budget else state.context.get('resolution_remaining_calls',10**9)
        funded=remaining>=reserve+(3 if tool in {'review_dispute','review_revision'} else 1)
        legal=bool(tool and n<maximum and funded and
                   (not policy.get('final_review_only') or tool in {'recover_validation','review_semantic_contract','review_dispute','search_evidence'}))
        result.append({'obligation_id':identity,'kind':kind,'reason':reason,'attempts':n,'attempt_limit':maximum,
            'legal_next_tool':tool if legal else None,'ready':ready and legal,'arguments':arguments,
            'expected_information_gain':reason,'terminal_reason':None if legal else
                'budget_reserved_for_final_committee' if not funded else 'bounded_attempts_exhausted' if n>=maximum else 'external_or_domain_decision_required'})
    for voter in candidates(state):
        add('semantic:'+voter,'semantic_contract','review_semantic_contract','补全缺失判断依据，允许维持未知',ready=True,arguments={'voter_id':voter})
    recoverable=state.validation_recovery.get('recoverable_voter_ids',[])
    covered=set((state.context.get('resolution_snapshot') or {}).get('whole_vote_eligible_voter_ids',[]))
    recoverable=[v for v in recoverable if v not in covered]
    if recoverable:add('recovery:'+','.join(sorted(recoverable)),'technical_recovery','recover_validation','只恢复已知可恢复席位，复用原回执',ready=True,arguments={})
    for draft in state.revision_drafts:
        if draft.get('status')=='proposed':
            add('draft:'+draft['draft_id'],'revision_review','review_revision','独立审核明确的修订草案',ready=True,arguments={'draft_id':draft['draft_id']})
        if draft.get('status')=='approved':
            add('apply:'+draft['draft_id'],'revision_application','apply_reviewed_revision','隔离应用批准草案并对新版本重新审核',ready=True,
                arguments={'draft_id':draft['draft_id'],'draft_hash':draft['draft_hash']})
    for group in state.context.get('split_revision_groups',[]):
        tool='apply_reviewed_split' if group.get('ready_to_apply') else 'review_split_revision'
        add('split:'+group['group_id'],'split_revision',tool,'每个子候选独立审核并重新核验',ready=True,arguments={'group_id':group['group_id']})
    original=set(state.context.get('dispute_source_evidence_ids',[]))
    fresh={e['evidence_id'] for e in state.bundle.get('items',[])}-original
    for dispute in (state.context.get('dispute_report') or {}).get('disputes',[]):
        if dispute['status']!='open':continue
        if fresh or dispute['check']=='mapping' and state.ontology_lookup.get('mapping_preflight',{}).get('judgment_ready'):
            add(dispute['dispute_id'],'focused_dispute','review_dispute','新增原文可核验该具体争议',ready=True,arguments={'dispute_id':dispute['dispute_id']})
        elif dispute['dispute_id'] not in state.context.get('resolution_no_gain_targets',[]):
            add(dispute['dispute_id'],'source_supplement','search_evidence','读取尚未核查的相关上下文后复核该争议')
        else:add(dispute['dispute_id'],'semantic_dispute',None,'补证没有新增材料，保留原争议')
    if not state.context.get('dispute_baseline'):
        for issue in state.issues:
            if issue.get('status','open')!='resolved' and issue['code']=='EVIDENCE_INSUFFICIENT':
                active=issue['issue_id'] not in state.context.get('resolution_no_gain_targets',[]) and not state.context.get('resolution_no_further_validation')
                add(issue['issue_id'],'source_supplement','search_evidence' if active else None,
                    '针对尚未核查的来源补证，新增材料后独立复核')
    validation=state.current_validation() or {}
    from .revision_patch import expression_diagnostics
    if not expression_diagnostics(state.candidate)['expression_complete']:
        identity='expression:'+sha256_json(state.candidate)[:12]
        n=attempts.get(identity,0)
        tool=None if identity in state.context.get('resolution_no_gain_targets',[]) or state.revision_drafts else 'search_evidence' if not n else 'propose_revision'
        add(identity,'expression_completion',tool,'从原文核验未知限定，再提出有依据的新版本；不得填默认值')
    axes=(state.context.get('resolution_snapshot') or {}).get('dimension_results') or validation.get('consensus',{}).get('review_axes') or {}
    if not state.revision_drafts and axes.get('mapping',{}).get('status') in {'unassigned','context_incomplete'}:
        prep=state.ontology_lookup.get('mapping_preflight') or {}
        missing=[r['id'] for r in prep.get('relation_candidates',[]) if 'definition_missing_in_bound_release' in r.get('missing_information',[])]
        if missing:add('definitions:'+sha256_json(missing)[:12],'shared_definition',None,'等待共用正式定义的集中审核和发布')
        elif prep.get('relation_candidates'):
            add('mapping:'+sha256_json(state.candidate)[:12],'mapping_proposal','propose_mapping','依据实体来源和完整定义提出可独立审核的映射')
    return result


def ready_action(state,budget):
    for obligation in resolution_obligations(state,budget):
        if obligation['ready']:
            return {'action':obligation['legal_next_tool'],'arguments':obligation['arguments'],
                'target_issue':obligation['obligation_id'],'rationale_summary':obligation['reason']}
    snapshot=state.context.get('resolution_snapshot') or {}
    dimensions=snapshot.get('dimension_results') or {}
    from .action_policy import ActionPolicy
    from .revision_patch import expression_diagnostics
    gaps=ActionPolicy({}).final_gaps(state)
    blocked=any(gaps.get(k) for k in ('critical_issues_unresolved','protected_evidence_omitted','unreviewed_discovered_evidence',
        'validation_missing','version_stale','ontology_not_checked','need_more_evidence','ontology_disagreement'))
    identity=state.candidate.get('contract_version')!='extraction_candidate_v4' or state.candidate.get('fact_key_state')=='canonical'
    if not blocked and identity and expression_diagnostics(state.candidate)['expression_complete'] and snapshot.get('semantic_obligations_complete') and all(dimensions.get(k,{}).get('status')==v for k,v in
        {'source':'supported','expression':'standard','mapping':'valid'}.items()) and dimensions.get('scope',{}).get('status') in {'include','exclude'}:
        return {'action':'finish_review','arguments':{'stop_reason':'program_verified_all_review_obligations',
            'unresolved_issues':[],'evidence_ids':[]},'target_issue':'program_completion',
            'rationale_summary':'完整判定和对应义务已经核验，由最终策略决定后续动作'}
    return None


def human_cards(state,obligations):
    sources={e['evidence_id']:e for e in state.archive}
    counters={eid for v in state.votes for eid in v.get('counter_evidence_ids',[])}
    supports={eid for v in state.votes for eid in v.get('supporting_evidence_ids',[])}
    return [{'issue_id':i['issue_id'],'question':i.get('description'),'target':i.get('target'),
        'candidate':state.candidate.get('triple'),'evidence_ids':sorted(set(i.get('evidence_ids',[]))),
        'source_material':[dict({k:sources[eid].get(k) for k in ('evidence_id','quote','section_id','chunk_id','start','end')},
            role='counter' if eid in counters else 'support' if eid in supports else 'context')
            for eid in sorted(set(i.get('evidence_ids',[]))) if eid in sources],
        'shared_definitions':[{'task_id':t['task_id'],'term_id':t['term_id'],'affected_candidates':t['affected_candidates']}
                              for t in state.context.get('shared_definition_tasks',[]) if t.get('priority')!='weak_recall'],
        'unresolved_reason':next((o.get('terminal_reason') or o['reason'] for o in obligations if o['obligation_id']==i['issue_id']),None),
        'attempted_steps':[{'tool':o.get('tool'),'status':o.get('status'),'new_evidence_count':o.get('new_evidence_count')}
                           for o in state.observations if o.get('tool')],
        'resolution_snapshot_id':(state.context.get('resolution_snapshot') or {}).get('snapshot_id')}
        for i in state.issues if i.get('status','open')!='resolved']
