"""Source-grounded proposals use the existing independently reviewed revision service."""
from copy import deepcopy
from .planner_contracts import ActionError
from .mapping_preflight import definitions


def propose(runtime, relation_id, head_type, tail_type, identity_evidence, type_evidence,
            competing_relations, evidence_ids, reason):
    from .revision_service import RevisionService
    state=runtime.state;guard=runtime.orchestrator.ontology_guard;triple=state.candidate['triple']
    from schema.relation_registry import RelationRegistry
    registry=RelationRegistry(guard.ontology)
    preflight=state.ontology_lookup.get('mapping_preflight') or {}
    allowed={r['id'] for r in preflight.get('relation_candidates',[])}|set(state.ontology_lookup.get('matched_ids',[]))
    if relation_id not in allowed or relation_id not in registry.properties:
        raise ActionError('select a retrieved legal relation ID; inspect_ontology before proposing mapping')
    if not definitions(registry.properties[relation_id]):
        raise ActionError('the target definition is missing; use the shared definition task before semantic assignment')
    available={e['evidence_id']:e for e in state.bundle.get('items',[]) if e.get('anchor_valid')}
    if not evidence_ids or not set(evidence_ids)<=set(available):raise ActionError('mapping requires current verified evidence')
    sides=['head']+(['tail'] if triple.get('tail_kind','entity')=='entity' else [])
    if set(identity_evidence)!=set(sides) or set(type_evidence)!=set(sides):
        raise ActionError('each entity needs its own identity and source-grounded type justification')
    types={'head':head_type,'tail':tail_type}
    for side in sides:
        if types[side] not in guard.classes:raise ActionError('type must be a legal class ID')
        identity=identity_evidence[side];typing=type_evidence[side]
        if (not isinstance(identity,list) or not identity or not set(identity)<=set(available)
                or not isinstance(typing,dict) or set(typing)!={'evidence_ids','basis','note'}
                or typing['basis']!='source' or not typing['evidence_ids']
                or not set(typing['evidence_ids'])<=set(available) or not str(typing['note']).strip()):
            raise ActionError('domain/range compatibility alone cannot establish an entity type')
        surface=''.join(str(triple[side]).split()).casefold()
        if not any(surface in ''.join(available[eid]['quote'].split()).casefold() for eid in identity):
            raise ActionError('entity identity must be located in cited source; resolve its referent first')
    exact={i for r in preflight.get('confusable_relations',[]) for i in r['candidate_ids']}-{relation_id}
    exclusions={r['relation_id']:r for r in competing_relations}
    if not exact<=set(exclusions):raise ActionError('every exact-alias competing relation needs an explicit exclusion')
    for identity,item in exclusions.items():
        if (identity not in allowed or identity==relation_id or not item.get('note') or
                not item.get('evidence_ids') or not set(item['evidence_ids'])<=set(available)):
            raise ActionError('invalid competing-relation exclusion or evidence')
    changes={'relation_id':relation_id,'head_type':head_type}
    if 'tail' in sides:changes['tail_type']=tail_type
    proposed=deepcopy(state.candidate);proposed['triple'].update(changes)
    if guard.check(proposed).get('status')!='pass':raise ActionError('proposed mapping violates the frozen type/direction/literal constraints')
    rationale={'identity_evidence':identity_evidence,'type_evidence':type_evidence,
               'competing_relations':competing_relations,'target_definition':deepcopy(registry.properties[relation_id])}
    draft=RevisionService(runtime.repository).propose(state.candidate,state.context['execution_row_version'],
        changes,evidence_ids,reason,state.bundle,runtime.document,protected_ids=state.protected_ids,
        source_audit_id=state.audit_id,mapping_rationale=rationale)
    state.revision_drafts.append(draft)
    runtime.issue('revision_proposed','映射提案需独立审核、隔离应用及新版本复核',critical=True)
    return {'status':'draft_proposed','draft':draft,'candidate_unchanged':True,'assignment_validated':False}
