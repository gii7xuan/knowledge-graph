"""Operator outcomes include semantic uncertainty and technical failure in human review."""
from copy import deepcopy

FAILURES={'failed','timeout','interrupted','paused','cancelled','technical_incomplete','skipped_unknown'}


def technical_failure(row):
    if 'technical_failure' in row:return bool(row['technical_failure'])
    status=row.get('execution_status',row.get('status'))
    return bool(row.get('prediction_missing') or status in FAILURES or
        'budget' in str(row.get('stop_reason') or '').casefold())


def outcome(row):
    if row.get('status')=='not_started' and not row.get('human_review_required') and not technical_failure(row):return 'pending'
    if technical_failure(row) or row.get('human_review_required'):return 'manual'
    decision=row.get('predicted_decision')
    return decision if decision in {'accept','reject'} else 'manual'


def normalize(row,*,reason=None,technical=None):
    result=deepcopy(row)
    if technical is not None:result['technical_failure']=technical
    final=outcome(result)
    result.update(final_outcome=final,human_review_required=final=='manual')
    if final=='manual':
        failed=technical_failure(result)
        result.update(execution_status=row.get('execution_status',row.get('status','completed')),
            status='human_handoff',predicted_decision='defer',technical_failure=failed,
            handoff_reason=reason or row.get('handoff_reason') or
                ('technical_failure' if failed else 'semantic_unresolved_or_revision_or_mapping'))
    return result
