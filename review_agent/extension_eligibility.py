"""One change-kind policy for planner menus, request validation and execution."""
from ontologyBuild.ontology_adapter import normalize_name

KINDS = ('add_class', 'add_object_property', 'add_data_property', 'add_alias',
         'revise_definition', 'revise_constraint', 'merge_terms')


def extension_eligibility(state):
    validation = state.current_validation() or {}
    consensus = validation.get('consensus', {})
    lookup = state.ontology_lookup
    eligible = set(consensus.get('eligible_voter_ids', []))
    votes = [v for v in validation.get('votes', []) if v.get('voter_id') in eligible]
    issues = {v.get('schema_issue') for v in votes}
    blockers = []
    if consensus.get('source_support_consensus', {}).get('result') != 'supported':
        blockers.append('current independent source-support consensus required')
    if (lookup.get('scope') != 'full_frozen_ontology' or
            lookup.get('candidate_revision') != state.candidate['revision']):
        blockers.append('current full frozen ontology lookup required')
    if any(i.get('status', 'open') != 'resolved' and i['code'] in {
        'source_conflict', 'counterevidence', 'CONDITION_MISSING', 'QUALIFIER_MISMATCH',
        'DIRECTION_MISMATCH', 'revision_proposed'} for i in state.issues):
        blockers.append('unresolved candidate/source issue requires revision or source review')
    if state.pending_extension_id:
        blockers.append('candidate already subscribed to an extension')
    triple = state.candidate.get('triple',{})
    matches = lookup.get('matched_ids', [])
    missing = {side: name for side, name in lookup.get('endpoint_class_queries', {}).items()
               if name and not lookup.get('endpoint_class_matches', {}).get(side)}
    definitions = lookup.get('matched_definitions', [])
    targets = sorted(set(matches) | {v for v in lookup.get('endpoint_class_matches', {}).values() if v})
    diagnostics = issues & {'suspected_schema_error', 'incompatible'}
    allowed, reasons = [], {}
    for kind in KINDS:
        reason = '; '.join(blockers)
        if not reason and kind in {'add_object_property', 'add_data_property'}:
            if state.guard.get('relation_known') or matches or issues != {'not_covered'}:
                reason = 'existing/ambiguous mapping must be revised; confirmed absent relation required'
            elif (kind == 'add_data_property') != (triple.get('tail_kind', 'entity') == 'literal'):
                reason = 'property kind must match the frozen tail kind'
        elif not reason and kind == 'add_class':
            if issues != {'not_covered'} or not missing:
                reason = 'a named missing endpoint class from full lookup is required'
        elif not reason and kind == 'add_alias':
            if not targets:
                reason = 'alias requires an existing unambiguous target term'
            elif not any(isinstance(v.get('schema_change_hint'),dict) and
                         (v['schema_change_hint'].get('change_kind')=='add_alias' or
                          set(v['schema_change_hint']) & {'alias','synonym','synonym_evidence'}) for v in votes):
                reason = 'alias requires independent synonym diagnosis'
        elif not reason and kind in {'revise_definition', 'revise_constraint'}:
            if not targets or not diagnostics:
                reason = 'existing target and independent schema-error diagnosis required'
        elif not reason and kind == 'merge_terms':
            if len(targets) < 2 or not diagnostics:
                reason = 'two existing targets and independent equivalence diagnosis required'
        if reason:
            reasons[kind] = reason
        else:
            allowed.append(kind)
    return {'allowed_change_kinds': allowed, 'blocked_change_kinds': reasons,
            'target_term_ids': targets, 'missing_endpoint_classes': missing,
            'matched_definitions': definitions}


def validate_extension(state, kind, changes):
    from .planner_contracts import ActionError
    eligibility = extension_eligibility(state)
    if kind not in eligibility['allowed_change_kinds']:
        raise ActionError(eligibility['blocked_change_kinds'].get(kind, 'unsupported change kind'))
    permitted={
        'add_class':{'label','name','definition','parents'},
        'add_object_property':{'label','name','definition','domain','range'},
        'add_data_property':{'label','name','definition','domain','datatype','datatypes'},
        'add_alias':{'term_id','target_id','alias'},
        'revise_definition':{'term_id','target_id','definition'},
        'revise_constraint':{'term_id','target_id','domain','range','datatype','datatypes','constraints'},
        'merge_terms':{'term_ids','label','definition'}}
    if set(changes)-permitted[kind]: raise ActionError('unsupported proposed_changes fields for '+kind)
    for field,value in changes.items():
        if field in {'parents','domain','range','datatypes','term_ids'}:
            if not isinstance(value,list) or len(value)>16 or any(not isinstance(v,str) or not v.strip() for v in value):
                raise ActionError('ontology target list must contain bounded term IDs')
        elif field=='constraints':
            if not isinstance(value,dict) or not value: raise ActionError('constraints must be an explicit object')
        elif not isinstance(value,str) or not value.strip() or len(value)>2000:
            raise ActionError('ontology change values must be bounded nonempty text')
    if kind == 'add_class':
        label = changes.get('label') or changes.get('name')
        if normalize_name(label) not in {normalize_name(v) for v in eligibility['missing_endpoint_classes'].values()}:
            raise ActionError('class label must name a confirmed missing endpoint type')
    elif kind in {'add_object_property', 'add_data_property'}:
        if not isinstance(changes.get('label') or changes.get('name'), str):
            raise ActionError('new property requires a source-supported label')
    elif kind == 'merge_terms':
        targets = changes.get('term_ids')
        if not isinstance(targets, list) or len(set(targets)) != 2 or not set(targets) <= set(eligibility['target_term_ids']):
            raise ActionError('merge_terms requires two existing term_ids')
    else:
        target = changes.get('term_id') or changes.get('target_id')
        if target not in eligibility['target_term_ids']:
            raise ActionError('ontology change requires an existing target term_id')
        if kind == 'add_alias' and not str(changes.get('alias') or '').strip():
            raise ActionError('add_alias requires an explicit synonym')
        if kind == 'revise_definition' and not str(changes.get('definition') or '').strip():
            raise ActionError('revise_definition requires a definition')
        if kind == 'revise_constraint' and not (set(changes) & {'domain', 'range', 'datatype', 'datatypes', 'constraints'}):
            raise ActionError('revise_constraint requires explicit constraint fields')
    return eligibility


def proposed_change_schemas():
    text={'type':'string','minLength':1,'maxLength':2000}
    ids={'type':'array','maxItems':16,'items':{'type':'string','minLength':1}}
    fields={
        'add_class':{'label':text,'name':text,'definition':text,'parents':ids},
        'add_object_property':{'label':text,'name':text,'definition':text,'domain':ids,'range':ids},
        'add_data_property':{'label':text,'name':text,'definition':text,'domain':ids,'datatype':text,'datatypes':ids},
        'add_alias':{'term_id':text,'target_id':text,'alias':text},
        'revise_definition':{'term_id':text,'target_id':text,'definition':text},
        'revise_constraint':{'term_id':text,'target_id':text,'domain':ids,'range':ids,'datatype':text,'datatypes':ids,'constraints':{'type':'object','minProperties':1}},
        'merge_terms':{'term_ids':{'type':'array','minItems':2,'maxItems':2,'uniqueItems':True,'items':text},'label':text,'definition':text}}
    return {kind:{'type':'object','properties':properties,'additionalProperties':False,'minProperties':1}
            for kind,properties in fields.items()}
