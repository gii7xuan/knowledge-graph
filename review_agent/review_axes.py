"""Four independent review axes; no inference from free-form model prose."""
from copy import deepcopy

VERSION = 'review_axes_v1'
EXPRESSION_CHECKS = ('entity_reference', 'relation_semantics', 'relation_direction', 'qualifiers')
QUALIFIER_FIELDS = ('polarity', 'condition', 'time_scope', 'applicability', 'modality', 'quantifier')


def normalization_errors(raw, proposal):
    """Only bounded, non-destructive representation operations are recognized."""
    triple = proposal['candidate']['triple']
    evidence = {e['evidence_id']: e for e in proposal['evidence_bundle']['items']}
    errors = []
    for finding in raw.get('expression_findings', []):
        if set(finding['evidence_ids']) - evidence.keys():
            errors.append('expression findings reference evidence outside the frozen bundle')
        plan = finding.get('normalization')
        if finding['kind'] not in {'field_placement', 'duplicate_qualifier'}:
            continue
        field, value = plan['field'], plan['value']
        if field in {'polarity', 'modality'}:
            errors.append('normalization cannot reclassify polarity or modality')
        if triple.get('qualifiers', {}).get(field) != value or not value.strip():
            errors.append('normalization must preserve the exact existing qualifier value')
        if triple.get('qualifier_status', {}).get(field) != 'extracted':
            errors.append('normalization requires an explicitly extracted qualifier')
        cited = [evidence.get(eid, {}) for eid in finding['evidence_ids']]
        if not cited or any(not e.get('anchor_valid') for e in cited) or not any(value in e.get('quote', '') for e in cited):
            errors.append('normalization requires anchored source evidence retaining the qualifier')
        if finding['kind'] == 'field_placement':
            if plan['operation'] != 'retain_qualifier' or finding['check'] != 'qualifiers':
                errors.append('field placement can only retain the qualifier in its current field')
        else:
            relation = triple.get('surface_relation') or ''
            remainder = relation[len(value):] if relation.startswith(value) else relation[:-len(value)]
            if (plan['operation'] != 'remove_duplicate_qualifier' or
                    finding['check'] not in {'qualifiers', 'relation_semantics'} or
                    not (relation.startswith(value) or relation.endswith(value)) or not remainder.strip()):
                errors.append('duplicate removal requires an exact boundary qualifier also preserved in qualifiers')
    return errors


def representation_checks(raw):
    return {f['check'] for f in raw.get('expression_findings', [])
            if f['kind'] in {'field_placement', 'duplicate_qualifier'}}


def expression_and_source(raw, *, expression_only=False):
    dims = raw['dimensions']
    findings = raw['expression_findings']
    normalized = representation_checks(raw)
    compound = {f['check'] for f in findings if f['kind'] == 'compound_entity'}
    semantic = dims['triple_support']['status'] == 'contradicted' or any(f['kind'] in {'semantic_error', 'compound_entity'} for f in findings)
    semantic = semantic or any(dims[n]['status'] == 'fail' and n not in normalized for n in EXPRESSION_CHECKS)
    pending = any(f['kind'] == 'unresolved' for f in findings) or any(
        dims[n]['status'] == 'unknown' and n not in normalized for n in EXPRESSION_CHECKS)
    expression = ('semantic_revision' if semantic else 'unresolved' if pending else
                  'lossless_normalization' if normalized else 'standard')
    if expression_only:
        return {'expression': {'status': expression, 'findings': deepcopy(findings)}}
    source = dims['triple_support']['status']
    if source == 'supported':
        # Representation quality and ontology mapping never decide source truth.
        source_failure = any(f['kind'] == 'semantic_error' for f in findings) or any(
            dims[n]['status'] == 'fail' and n not in normalized | compound for n in EXPRESSION_CHECKS)
        source = ('contradicted' if source_failure else 'insufficient' if pending or
                  dims['evidence_sufficiency']['status'] not in {'pass', 'not_applicable'} else 'supported')
        if dims['counterevidence']['status'] not in {'pass', 'not_applicable'}:
            source = 'unresolved'
    return {'source': {'status': source}, 'expression': {'status': expression, 'findings': deepcopy(findings)}}


def axis_opinion(raw, axis):
    """Read only real dependencies of this axis; never invent missing dimensions."""
    if axis in {'source','expression'}:return expression_and_source(raw,expression_only=axis=='expression')[axis]
    if axis=='scope':return {'status':'unclear','knowledge_scope':raw['dimensions']['knowledge_scope']['status']}
    mapping = raw['dimensions']['mapping']['status']
    mapping = 'valid' if mapping == 'matched' else 'conflict' if mapping in {
        'constraint_conflict', 'candidate_mapping_error'} else 'unassigned'
    return {'status':mapping}


def vote_axes(raw):
    return {axis:axis_opinion(raw,axis) for axis in ('source','expression','mapping','scope')}


def aggregate_axes(votes, proposal, consensus):
    from .scope_policy import DEFAULT_POLICY, classification_decision
    all_seats = len(votes) == len(proposal['committee_members']) and consensus.get('diversity_ready', False)
    sources = {v['review_axes']['source']['status'] for v in votes}
    conflicts = bool(consensus.get('unresolved_counterevidence') or consensus.get('unresolved_support_conflict') or consensus.get('counter_evidence_ids'))
    source = next(iter(sources)) if all_seats and len(sources) == 1 and not conflicts else 'unresolved'
    if source == 'supported' and not consensus.get('expression_complete', True):
        source = 'insufficient'
    expressions = {v['review_axes']['expression']['status'] for v in votes}
    expression = ('semantic_revision' if 'semantic_revision' in expressions else
                  'unresolved' if not all_seats or 'unresolved' in expressions or not consensus.get('expression_complete', True) else
                  'lossless_normalization' if 'lossless_normalization' in expressions else 'standard')
    guard = proposal.get('ontology_check', {})
    effective = consensus.get('effective_mapping_status')
    mapping = ('conflict' if effective == 'constraint_conflict' else
               'valid' if all_seats and effective == 'matched' else
               'context_incomplete' if guard.get('status') == 'unknown' or
               any(v.get('schema_issue') == 'suspected_schema_error' for v in votes) else 'unassigned')
    preparation = None
    if 'mapping_preflight_policy' in proposal:
        from .mapping_preflight import frozen
        preparation = frozen(proposal)
        if mapping != 'conflict' and not preparation['judgment_ready']:
            mapping = 'context_incomplete'
    policy = proposal.get('review_scope_policy') or DEFAULT_POLICY
    scope = consensus.get('knowledge_scope', 'unresolved')
    scope_decision = classification_decision(scope,all_seats and consensus.get('scope_confirmed'),policy)
    return {'version': VERSION, 'source': {'status': source, 'core_relation_status': consensus.get('core_source_support_result'),
                'evidence_ids': sorted({eid for v in votes for eid in v.get('supporting_evidence_ids', [])})},
            'expression': {'status': expression, 'findings': [dict(f, voter_id=v['voter_id'])
                for v in votes for f in v.get('expression_findings', [])]},
            'mapping': {'status': mapping, 'effective_mapping_status': effective,
                **({'preflight': preparation} if preparation is not None else {}),
                'reason_codes': guard.get('reason_codes', [])},
            'scope': scope_decision}


def route_axes(axes, *, complete=True):
    """Retain every outstanding action even when one queue has priority."""
    source, expression, mapping, scope = (axes[n]['status'] for n in ('source', 'expression', 'mapping', 'scope'))
    actions = []
    if not complete: actions.append('review_incomplete')
    if scope == 'exclude': actions.append('excluded_by_scope')
    if expression == 'semantic_revision' or source == 'contradicted': actions.append('semantic_revision_required')
    if complete and source == 'insufficient': actions.append('evidence_insufficient')
    if complete and source == 'unresolved': actions.append('source_review')
    if complete and expression == 'unresolved': actions.append('expression_review')
    if expression == 'lossless_normalization': actions.append('normalization_required')
    if mapping != 'valid': actions.append('mapping_review')
    if scope == 'unclear': actions.append('scope_review')
    return {'workflow_state': actions[0] if actions else 'review_completed', 'pending_actions': actions,
            'ready_for_admission': not actions, 'review_axes': axes}


def display_axes(decision):
    """Historical projections expose their limits without reclassifying old votes."""
    if not decision:
        return {}
    if decision.get('review_axes'):
        return deepcopy(decision['review_axes'])
    c = decision.get('consensus') or {}
    expression = c.get('expression_status')
    mapping = c.get('effective_mapping_status')
    return {'version': 'legacy_projection', 'legacy': True,
            'source': {'status': decision.get('source_support_result', 'unresolved')},
            'expression': {'status': 'legacy_faithful' if expression == 'faithful' else 'legacy_revision' if expression == 'needs_revision' else 'unresolved'},
            'mapping': {'status': 'valid' if mapping == 'matched' else 'conflict' if mapping == 'constraint_conflict' else 'unassigned' if mapping == 'unassigned' else 'context_incomplete'},
            'scope': {'status': 'exclude' if decision.get('workflow_state') == 'excluded_by_scope' else 'unclear', 'knowledge_scope': c.get('knowledge_scope')}}
