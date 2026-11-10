"""Technical completion stays distinct from collecting independent model ballots."""
from copy import deepcopy


def minimum_status(complete,guard,bundle,consensus):
    missing=[]
    if not complete:missing.append('independent_committee_incomplete')
    if not bundle.get('items'):missing.append('verified_source_missing')
    if guard.get('status')=='unknown':missing.append('ontology_check_incomplete')
    axes=consensus.get('review_axes') or {}
    for name,unknown in [('source',{'insufficient','unresolved'}),('expression',{'unresolved'}),
                         ('mapping',{'context_incomplete'}),('scope',{'unclear'})]:
        if axes.get(name,{}).get('status') in unknown:missing.append(name+'_information_incomplete')
    return {'minimum_review_status':'incomplete' if missing else 'completed',
        'missing_information':missing,'independent_ballot_complete':bool(complete)}


def failure_display(context):
    if context.get('failure_handling'):return deepcopy(context['failure_handling'])
    if context.get('fallback_reason'):
        return {'version':'historical_planner_failure','reason':context['fallback_reason'],
            'historical':True,'minimum_steps':0,'independent_review_completed':bool(context.get('independent_review_completed'))}
    return None
