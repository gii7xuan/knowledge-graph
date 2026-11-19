"""Predeclared action selection using the exact same runtime tools as the Agent."""
from .action_policy import ActionPolicy

VERSION='fixed_candidate_workflow_v1'


def action_counts(state):
    names=[o.get('tool') for o in state.observations]
    return {'supplements':sum(n in {'search_evidence','inspect_context'} for n in names),
            'focused_reviews':names.count('review_dispute')}


def next_action(state):
    """Full lookup, initial ballot, two ordered searches, first open dispute, stop."""
    blocked=ActionPolicy.blocked_tools(state)
    triple=state.candidate.get('triple') or {}
    counts=action_counts(state)
    def action(name,**args):
        return {'action':name,'arguments':args,'target_issue':VERSION,
                'rationale_summary':'Predeclared fixed action order; no planner model request.'}
    if not state.ontology_checked:
        return action('inspect_ontology',relation_id=triple.get('relation_id'),
                      surface_relation=triple.get('surface_relation',''),issue='Check common frozen ontology')
    if not state.validations and not state.context.get('dispute_baseline') and 'run_validation' not in blocked:
        return action('run_validation')
    unresolved=any(i.get('critical') and i.get('status','open')!='resolved' for i in state.issues)
    needs_context=unresolved or bool(state.gaps.get('need_more_evidence')) or not state.bundle.get('items')
    if needs_context and counts['supplements']<2:
        query=' '.join(str(triple.get(k) or '') for k in ('head','surface_relation','tail')).strip() or '核对原文'
        return action('search_evidence',scope=('current_chunk','same_section')[counts['supplements']],
                      query=query[:600],purpose='check_support',limit=6)
    disputes=sorted((d for d in (state.context.get('dispute_report') or {}).get('disputes',[])
                     if d.get('status')=='open'),key=lambda d:d['dispute_id'])
    if disputes and counts['focused_reviews']<1 and 'review_dispute' not in blocked:
        return action('review_dispute',dispute_id=disputes[0]['dispute_id'])
    return action('finish_review',stop_reason='fixed_workflow_completed',unresolved_issues=[],evidence_ids=[])
