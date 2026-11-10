"""Read-only process diagnostics; recall and constraints never prove semantic mapping."""
from collections import Counter
from copy import deepcopy
from .contracts import sha256_json

VERSION='mapping_execution_trace_v1'


def process(proposal, votes=()):
    preflight=proposal.get('mapping_preflight') or {}
    trace=preflight.get('search_trace') or []
    relations=preflight.get('relation_candidates') or []
    entities=preflight.get('entities') or {}
    guard=proposal.get('ontology_check') or {}
    stages=[]
    def stage(name,status,**detail):stages.append({'stage':name,'status':status,**detail})
    subset=next((t for t in trace if t.get('stage')=='review_subset'),None)
    global_search=next((t for t in trace if t.get('stage')=='full_bound_release_and_aliases'),None)
    failure=next((t for t in trace if t.get('stage')=='supplementary_budget' and t.get('complete') is False),None)
    stage('local_retrieval','completed' if subset and subset.get('property_count') else 'not_found' if subset else 'not_executed',
        recalled_ids=[r['id'] for r in relations if r.get('retrieved')],source=deepcopy(subset))
    stage('global_retrieval','failed' if failure else 'completed' if global_search else 'not_executed',
        source=deepcopy(global_search),failure=deepcopy(failure),recall_is_semantic_proof=False)
    complete_definitions=[r for r in relations if r.get('retrieved') and not r.get('missing_information')]
    stage('relation_definitions','completed' if complete_definitions else 'not_found' if relations else 'not_executed',
        complete_ids=[r['id'] for r in complete_definitions],candidates=deepcopy(relations),
        release_id=proposal.get('ontology_release_id'),ontology_context_hash=proposal.get('ontology_context_hash'))
    resolved=bool(entities) and all(not e.get('missing_information') for e in entities.values())
    stage('entity_identity','completed' if resolved else 'not_found' if entities else 'not_executed',
        entities=deepcopy(entities),candidate_ids_are_formal_entity_ids=False)
    mappings=[]
    for v in votes:
        dimensions=v.get('dimensions')
        if not isinstance(dimensions,dict):
            from .validators import _parse_content
            try:dimensions=_parse_content(v.get('corrected_response') or v.get('raw_response')).get('dimensions')
            except (TypeError,ValueError):dimensions=None
        mapping=dimensions.get('mapping') if isinstance(dimensions,dict) else None
        if isinstance(mapping,dict):mappings.append({'voter_id':v.get('voter_id'),'response_status':v.get('response_status'),**deepcopy(mapping)})
    stage('mapping_attempt','completed' if mappings else 'not_executed',
        opinions=mappings,formal_assignment=deepcopy(preflight.get('formal_mapping')),
        original_expression=deepcopy(preflight.get('original_expression')),semantic_equivalence_confirmed=False)
    stage('constraint_check','completed' if guard.get('status')=='pass' else 'failed' if guard.get('status')=='fail' else 'not_found' if guard else 'not_executed',
        result=guard.get('status'),reason_codes=guard.get('reason_codes',[]))
    return {'version':VERSION,'proposal_digest':proposal.get('proposal_digest'),
        'preflight_hash':sha256_json(preflight) if preflight else None,'stages':stages,
        'blocking_information':deepcopy(preflight.get('blocking_information') or []),
        'global_gap_confirmed':False,'mapping_accuracy':'requires_current_packet_human_annotation'}


def statistics(reports):
    reports=list(reports);names=('local_retrieval','global_retrieval','relation_definitions','entity_identity','mapping_attempt','constraint_check')
    return {'version':VERSION,'population':len(reports),'unit':'one_selected_candidate_version',
        'stages':{name:{'denominator':len(reports),'counts':dict(Counter(
            next((s['status'] for s in r['stages'] if s['stage']==name),'not_executed') for r in reports))} for name in names},
        'constraints_are_mapping_accuracy':False}
