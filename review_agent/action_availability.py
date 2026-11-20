"""Shared, read-only execution gates for obligations, schemas and runtime requests."""
from .contracts import sha256_json


def evidence_version(state):
    return sha256_json(sorted((e['evidence_id'], e.get('quote'), e.get('anchor_valid'))
                             for e in state.archive))


def search_key(action, state):
    args = action['arguments']
    scope = (['context', args.get('evidence_id'), args.get('before'), args.get('after')]
             if action['action'] == 'inspect_context' else ['search', args.get('scope'), args.get('purpose'),args.get('query'),args.get('limit')])
    return sha256_json([action['target_issue'], scope, evidence_version(state)])


def required_budget(tool, state):
    seats = len(state.context.get('committee_members') or [])
    # Old checkpoints may omit the roster; never assume a smaller committee.
    seats = seats or 3
    policy = state.context.get('agent_resolution_policy') or {}
    final = int(policy.get('reserve_final_committee_calls', seats))
    calls = seats if tool in {'review_dispute', 'review_revision', 'run_validation', 'recover_validation','assess_ontology_coverage'} else 1 if tool == 'review_semantic_contract' else 0
    reserve = final if tool == 'review_revision' else seats + final if tool in {'propose_mapping', 'propose_revision', 'propose_split_revision'} else 0
    return {'tool_calls': 1, 'model_calls': calls, 'reserved_model_calls': reserve}


def mapping_source_ready(state,axes):
    if axes.get('source',{}).get('status')=='supported':return True
    from .revision_patch import expression_diagnostics
    # Unknown qualifier metadata can block the full source axis even though the
    # independently reviewed core statement and referents are already supported.
    if not expression_diagnostics(state.candidate)['blocking_fields']:return False
    votes=(state.current_validation() or {}).get('votes') or []
    checks=('entity_reference','relation_semantics','relation_direction','evidence_sufficiency','counterevidence')
    return bool(votes) and all(v.get('response_status')=='ok' and
        v.get('dimensions',{}).get('triple_support',{}).get('status')=='supported' and
        all(v.get('dimensions',{}).get(c,{}).get('status') in {'pass','not_applicable'} for c in checks) and
        all(f.get('check')=='qualifiers' and f.get('meaning_effect')!='changed' for f in v.get('expression_findings',[])) for v in votes)


def unavailable_reason(tool, state, budget, allowed, *, action=None, tool_reserved=False):
    from .action_policy import ActionPolicy
    if tool not in allowed:
        return 'tool not permitted by execution policy'
    reason = ActionPolicy.blocked_tools(state).get(tool)
    if reason:
        return reason
    policy = state.context.get('agent_resolution_policy') or {}
    if state.context.get('resolution_snapshots_enabled'):
        if policy.get('final_review_only') and tool in {'propose_mapping', 'propose_revision', 'propose_split_revision'}:
            return 'final review cannot create a second semantic revision'
        if tool in {'propose_mapping', 'propose_revision', 'propose_split_revision'} and (state.revision_drafts or state.context.get('split_revision_groups')):
            return 'one semantic revision version per candidate in this experiment'
        if tool == 'propose_mapping':
            prep = state.ontology_lookup.get('mapping_preflight') or {}
            if not prep.get('proposal_ready', prep.get('judgment_ready', False)):
                return 'source identity, endpoint type evidence and complete recalled definitions required for mapping proposal'
        if action:
            from .resolution_obligations import attempt_targets
            maximum = int(policy.get('max_attempts_per_issue', 2))
            if any(state.context.get('resolution_attempts', {}).get(k, 0) >= maximum for k in attempt_targets(action, state)):
                return 'bounded recovery attempt limit for the exact server issue and source scope'
            if tool in {'search_evidence', 'inspect_context'} and search_key(action, state) in state.context.get('resolution_search_history', {}):
                return 'source scope already checked for this issue and evidence version'
    if policy.get('fixed_candidate_limits'):
        from .fixed_candidate_workflow import action_counts
        counts = action_counts(state)
        if tool in {'search_evidence', 'inspect_context'} and counts['supplements'] >= 2:
            return 'fixed candidate protocol permits at most two evidence supplements'
        if tool == 'review_dispute' and counts['focused_reviews'] >= 1:
            return 'fixed candidate protocol permits at most one focused dispute review'
    if budget:
        if budget.exhausted():
            return 'deadline or token budget exhausted'
        if not tool_reserved and budget.usage['tool_calls'] >= int(budget.config.get('max_tool_calls', 8)):
            return 'tool budget exhausted'
        if not tool_reserved and getattr(budget,'case_ids',None):
            if any((s:=budget.repository.case_status(cid))['usage']['tool_calls']>=s['limits']['tool_calls'] for cid in budget.case_ids):
                return 'root case tool budget exhausted'
        remaining = budget.remaining_calls()
    else:
        remaining = state.context.get('resolution_remaining_calls', 10**9)
    required = required_budget(tool, state)
    if remaining < required['model_calls'] + required['reserved_model_calls']:
        return 'insufficient model calls for action and independent revision chain'
    return None
