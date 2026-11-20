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
        from .action_availability import search_key
        return ['supplement:'+search_key(action,state)]
    return []


def targeted_supplement(state,identity,*,qualifiers=False):
    """Arguments come from verified anchors; no planner-invented offsets or IDs."""
    from .action_availability import search_key
    evidence=[e for e in state.bundle.get('items',[]) if e.get('anchor_valid')]
    choices=[]
    if qualifiers:
        choices += [('inspect_context',{'evidence_id':e['evidence_id'],'before':1,'after':1}) for e in evidence[:1]]
    triple=state.candidate['triple']
    query=' '.join(str(triple.get(k) or '') for k in ('head','surface_relation','tail')).strip()[:600]
    if query:
        for scope in ('same_section','parent_section'):
            choices.append(('search_evidence',{'scope':scope,'query':query,
                'purpose':'check_qualifiers' if qualifiers else 'check_support','limit':6}))
    for tool,args in choices:
        action={'action':tool,'arguments':args,'target_issue':identity,'rationale_summary':'核查此问题尚未读取的原文范围'}
        if search_key(action,state) not in state.context.get('resolution_search_history',{}):return action
    return None


def resolution_obligations(state,budget=None,allowed_tools=None):
    if not state.context.get('resolution_snapshots_enabled'):return []
    from .semantic_recheck import candidates
    from .action_availability import unavailable_reason,required_budget,mapping_source_ready
    from .planner_contracts import TOOL_SCHEMAS,RESOLUTION_TOOLS
    result=[];policy=state.context.get('agent_resolution_policy') or {}
    allowed=set(allowed_tools if allowed_tools is not None else state.context.get('resolution_allowed_tools',
        [*TOOL_SCHEMAS,*RESOLUTION_TOOLS,'review_dispute']))
    maximum=int(policy.get('max_attempts_per_issue',2));attempts=state.context.get('resolution_attempts') or {}
    def add(identity,kind,tool,reason,*,ready=False,arguments=None):
        action={'action':tool,'arguments':arguments or {},'target_issue':identity,'rationale_summary':reason}
        error=unavailable_reason(tool,state,budget,allowed,action=action) if tool else 'external_or_domain_decision_required'
        result.append({'obligation_id':identity,'target_issue':identity,'kind':kind,'reason':reason,
            'attempts':attempts.get(identity,0),'attempt_limit':maximum,
            'legal_next_tool':tool if not error else None,'ready':ready and not error,'arguments':arguments,
            'required_budget':required_budget(tool,state) if tool else None,
            'expected_information_gain':reason,'terminal_reason':error})
    def supplement(identity,reason,*,qualifiers=False):
        action=targeted_supplement(state,identity,qualifiers=qualifiers)
        add(identity,'expression_completion' if qualifiers else 'source_supplement',
            action['action'] if action else None,reason,ready=bool(action),arguments=action['arguments'] if action else None)
        return action
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
    snapshot_axes=(state.context.get('resolution_snapshot') or {}).get('dimension_results') or {}
    for dispute in (state.context.get('dispute_report') or {}).get('disputes',[]):
        if dispute['status']!='open':continue
        if dispute['check']=='mapping' and snapshot_axes.get('scope',{}).get('status')=='exclude':continue
        if fresh or dispute['check']=='mapping' and state.ontology_lookup.get('mapping_preflight',{}).get('judgment_ready'):
            add(dispute['dispute_id'],'focused_dispute','review_dispute','新增原文可核验该具体争议',ready=True,arguments={'dispute_id':dispute['dispute_id']})
        else:supplement(dispute['dispute_id'],'读取尚未核查的相关上下文后复核该争议',qualifiers=dispute['check'] in {'qualifiers','knowledge_scope'})
    if not state.context.get('dispute_baseline'):
        for issue in state.issues:
            if issue.get('status','open')!='resolved' and issue['code']=='EVIDENCE_INSUFFICIENT':
                supplement(issue['issue_id'],'针对尚未核查的来源补证，新增材料后独立复核')
    from .revision_patch import expression_diagnostics
    expression=expression_diagnostics(state.candidate)
    prep=state.ontology_lookup.get('mapping_preflight') or {}
    validation=state.current_validation() or {}
    axes=(state.context.get('resolution_snapshot') or {}).get('dimension_results') or validation.get('consensus',{}).get('review_axes') or {}
    if (policy.get('expression_grounding_required') and not state.revision_drafts
            and axes.get('expression',{}).get('status') in {'semantic_revision','unresolved'}
            and state.bundle.get('items')):
        add('semantic_expression:'+sha256_json(state.candidate)[:12],'expression_revision','propose_revision',
            '依据原文提出头实体、尾实体、关系方向或限定信息的统一修订草案，逐字段说明证据；独立审核后再复审新版本')
    mapping_needed=axes.get('mapping',{}).get('status') in {'unassigned','context_incomplete'}
    if not expression['expression_complete'] and not state.revision_drafts:
        identity='expression:'+sha256_json(state.candidate)[:12]
        if not supplement(identity,'从完整陈述核验未知限定；不得填默认值',qualifiers=True):
            if not mapping_needed:
                add(identity,'expression_completion','propose_revision','成对填写有逐字段证据的限定值和状态；未述明须记录核查范围')
    if not state.revision_drafts and mapping_needed and axes.get('scope',{}).get('status')!='exclude':
        missing=[r['id'] for r in prep.get('relation_candidates',[]) if 'definition_missing_in_bound_release' in r.get('missing_information',[])]
        if missing:add('definitions:'+sha256_json(missing)[:12],'shared_definition',None,'等待共用正式定义的集中审核和发布')
        elif prep.get('proposal_ready') and mapping_source_ready(state,axes) and axes.get('scope',{}).get('status')=='include' and (
                axes.get('expression',{}).get('status')=='standard' or expression['blocking_fields']):
            add('mapping:'+sha256_json(state.candidate)[:12],'mapping_proposal','propose_mapping',
                '依据来源、定义与方向提出映射；未知限定须在同一草案内附逐字段证据补齐')
        elif prep and not prep.get('relation_candidates') and axes.get('source',{}).get('status')=='supported' and not state.context.get('coverage_assessment'):
            add('coverage:'+sha256_json(state.candidate)[:12],'ontology_coverage','assess_ontology_coverage','先核查完整绑定本体；召回为空不等于本体缺口')
        else:add('mapping:'+sha256_json(state.candidate)[:12],'mapping_information',None,
            '缺少来源支持、范围判断、端点类型证据或可表达该陈述的冻结本体结构')
    return result


def ready_action(state,budget):
    obligations=resolution_obligations(state,budget)
    ready=[o for o in obligations if o['ready']]
    for obligation in ready:
        if (obligation['legal_next_tool'] in {'search_evidence','inspect_context'} and len([o for o in obligations if o['legal_next_tool'] and o['ready']])>1
                and not state.context.get('resolution_local_supplements_only') and budget.usage['planner_steps']<int(budget.config.get('max_planner_steps',6))):
            continue
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
    if not blocked and snapshot.get('scope_exclusion_complete'):
        return {'action':'finish_review','arguments':{'stop_reason':'program_verified_scope_exclusion','unresolved_issues':[],
            'evidence_ids':[]},'target_issue':'scope_exclusion','rationale_summary':'来源、表达与排除类别已独立核验，主图映射不再需要'}
    if not blocked and identity and expression_diagnostics(state.candidate)['expression_complete'] and snapshot.get('semantic_obligations_complete') and all(dimensions.get(k,{}).get('status')==v for k,v in
        {'source':'supported','expression':'standard','mapping':'valid'}.items()) and dimensions.get('scope',{}).get('status') in {'include','exclude'}:
        return {'action':'finish_review','arguments':{'stop_reason':'program_verified_all_review_obligations',
            'unresolved_issues':[],'evidence_ids':[]},'target_issue':'program_completion',
            'rationale_summary':'完整判定和对应义务已经核验，由最终策略决定后续动作'}
    return None


def human_cards(state,obligations):
    from .revision_patch import expression_diagnostics
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
        'blocking_qualifier_fields':expression_diagnostics(state.candidate)['blocking_fields'],
        'mapping_missing_information':(state.ontology_lookup.get('mapping_preflight') or {}).get('proposal_blocking_information',[]),
        'unresolved_reason':next((o.get('terminal_reason') or o['reason'] for o in obligations if o['obligation_id']==i['issue_id']),None),
        'attempted_steps':[{'tool':o.get('tool'),'status':o.get('status'),'new_evidence_count':o.get('new_evidence_count')}
                           for o in state.observations if o.get('tool')],
        'resolution_snapshot_id':(state.context.get('resolution_snapshot') or {}).get('snapshot_id')}
        for i in state.issues if i.get('status','open')!='resolved']
