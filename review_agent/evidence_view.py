"""Human/export view of the SAME immutable evidence package used by reviewers."""
from copy import deepcopy
from evidence.units import can_support, classify_quote


def evidence_view(bundle, votes=(), *, authoritative=False):
    items = {x['evidence_id']: x for x in bundle.get('items', [])}
    support, counter, assessments = set(), set(), {}
    for vote in votes:
        if vote.get('response_status') != 'ok': continue
        support.update(vote.get('supporting_evidence_ids') or [])
        counter.update(vote.get('counter_evidence_ids') or [])
        for a in vote.get('evidence_assessments') or []:
            assessments.setdefault(a['evidence_id'], set()).add(a['role'])
    # Legacy support arrays do not nominate a complete supporting sentence/set.
    # Do not manufacture that judgment by ranking short/lexically similar text.
    nominations = {eid for v in votes if authoritative and v.get('response_status')=='ok' and
        v.get('expression_status')=='faithful' and v.get('source_support')=='supported'
        for eid in v.get('primary_evidence_ids') or []}
    primary_items = [deepcopy(x) for eid,x in items.items() if eid in nominations and eid not in counter and can_support(x)]
    if nominations - {x['evidence_id'] for x in primary_items}: primary_items=[]
    primary = primary_items[0] if primary_items else None
    fragments = [deepcopy(x) for x in items.values() if can_support(x)]
    return {'evidence_hash':bundle.get('evidence_hash'), 'main_evidence_status':'reviewed_support' if primary else 'pending_verification',
            'primary_evidence':primary, 'primary_evidence_items':primary_items,
            'body_fragments':fragments, 'supporting_evidence_ids':sorted(support & items.keys()),
            'counter_evidence':[deepcopy(items[eid]) for eid in sorted(counter & items.keys())],
            'context_evidence':[deepcopy(x) for x in items.values() if not primary or x['evidence_id'] != primary['evidence_id']],
            'full_bundle':deepcopy(bundle), 'full_bundle_available':True, 'context_expansion_is_new_evidence':True}
