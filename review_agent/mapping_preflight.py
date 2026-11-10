"""Candidate-bound metadata checks and supplementary lookup, never semantic assignment."""
from copy import deepcopy
import json

from ontologyBuild.ontology_adapter import normalize_name
from schema.entity_registry import local_entity_id
from schema.relation_registry import RelationRegistry
from .contracts import sha256_json

VERSION = 'mapping_preflight_v1'
MODEL_RULE = ('Mapping metadata was checked before dispatch. If judgment_ready is false, preserve the original '
              'source opinion, keep formal mapping unassigned, and explain missing metadata. Do not treat missing '
              'metadata or lexical non-matches as a global ontology gap. Recalled IDs are candidates, not approved '
              'mappings. Never replace surface_relation with a merely similar predicate to satisfy schema.')


def policy_for(config):
    incoming = config.get('mapping_preflight') or {}
    if set(incoming) - {'enabled'} or type(incoming.get('enabled', False)) is not bool:
        raise ValueError('mapping_preflight accepts only a boolean enabled setting')
    return {'version': VERSION, 'search_order': ['review_subset', 'full_bound_release_and_aliases'],
            'automatic_assignment': False, 'absence_from_recall_is_gap_proof': False,
            'entity_identity': 'document_local_with_explicit_types_and_source', 'model_rule': MODEL_RULE}


def definitions(item):
    texts = []
    for key in ('definition', 'description', 'definitions'):
        values = item.get(key)
        for value in values if isinstance(values, list) else [values]:
            text = value.get('value') or value.get('text') if isinstance(value, dict) else value
            if isinstance(text, str) and text.strip(): texts.append(text.strip())
    return sorted(set(texts))


def names(item):
    return [str(x) for x in (item.get('id'), item.get('label'), item.get('name'),
            *(item.get('aliases') or []), *(v.get('value') for v in item.get('labels', []) if isinstance(v, dict))) if x]


def _class_matches(classes, value):
    key = normalize_name(str(value or ''))
    return sorted(identity for identity, item in classes.items() if key and any(normalize_name(n) == key for n in names(item)))


def _property_matches(registry, value):
    key = normalize_name(str(value or ''))
    return {identity for identity, item in registry.properties.items()
            if key and any(normalize_name(n) == key for n in names(item))}


def _entity(candidate, side, context, bundle, classes):
    triple = candidate['triple']; surface = triple.get(side) or ''
    assigned = triple.get(side + '_type')
    exact_types = _class_matches(classes, assigned) if assigned else []
    possible = sorted(set(exact_types) | set(_class_matches(classes, surface)))
    mention = ((triple.get('semantic_context') or {}).get('entity_mentions') or {}).get(side) or {}
    provided_alias = mention.get('canonical_entity')
    refs = [e['evidence_id'] for e in bundle.get('items', []) if surface and e.get('anchor_valid') and
            (surface in str(e.get('quote') or '') or str(mention.get('entity_surface') or surface) in str(e.get('quote') or ''))]
    missing = []
    if not candidate.get('document_id') or not candidate.get('document_version'):
        missing.append('source_identity_unavailable')
    expected = local_entity_id(candidate.get('document_id'), candidate.get('document_version'), surface)
    if triple.get(side + '_entity_id') != expected: missing.append('candidate_identity_unverified')
    if len(exact_types) != 1: missing.append('endpoint_type_unassigned_or_ambiguous')
    if len(exact_types) == 1 and exact_types[0] not in {c['id'] for c in context.get('classes', [])}:
        missing.append('endpoint_type_not_retrieved')
    if not refs: missing.append('referent_source_context_unavailable')
    return {'candidate_id': triple.get(side + '_entity_id'), 'surface': surface,
            'assigned_type': assigned, 'type_candidate_ids': possible,
            'aliases': [provided_alias] if provided_alias and provided_alias != surface else [],
            'aliases_are_unconfirmed': True, 'source_evidence_ids': refs[:4],
            'document_id': candidate.get('document_id'), 'document_version': candidate.get('document_version'),
            'referent_scope': mention.get('referent_scope'), 'missing_information': missing}


def _inspect(guard, candidate, bundle, context):
    registry = RelationRegistry(guard.ontology); triple = candidate['triple']
    context_ids = {p['id'] for p in context.get('properties', [])}
    assigned = triple.get('relation_id'); required = {assigned} if assigned in registry.properties else set()
    exact = set()
    for query in (triple.get('surface_relation'), triple.get('relation_label')):
        exact.update(_property_matches(registry, query))
    required |= exact
    considered = required or context_ids
    rows = []
    for identity in sorted(considered):
        prop = registry.properties.get(identity, {})
        missing = []
        if identity not in context_ids: missing.append('definition_not_retrieved')
        if not definitions(prop): missing.append('definition_missing_in_bound_release')
        if 'domain' not in prop or prop.get('property_kind') == 'object' and 'range' not in prop:
            missing.append('type_constraints_not_declared')
        if prop.get('property_kind') == 'data' and not (prop.get('datatype') or prop.get('range_datatype') or prop.get('datatypes')):
            missing.append('literal_datatype_not_declared')
        for field in ('domain', 'range') if prop.get('property_kind') == 'object' else ('domain',):
            refs = prop.get(field) or []
            refs = [refs] if isinstance(refs, str) else refs
            if any(not _class_matches(guard.classes, ref) for ref in refs):
                missing.append('constraint_class_reference_unavailable')
        rows.append({'id': identity, 'label': prop.get('label') or prop.get('name'),
            'aliases': deepcopy(prop.get('aliases') or []), 'property_kind': prop.get('property_kind'),
            'definition_available': bool(definitions(prop)), 'retrieved': identity in context_ids,
            'direction': 'head_to_literal' if prop.get('property_kind') == 'data' else 'head_to_tail',
            'direction_basis': 'formal_property_signature_not_semantic_approval',
            'domain': deepcopy(prop.get('domain')), 'range': deepcopy(prop.get('range')),
            'datatype': prop.get('datatype') or prop.get('range_datatype'), 'missing_information': missing})
    entities = {side: _entity(candidate, side, context, bundle, guard.classes) for side in ('head', 'tail')
                if side == 'head' or triple.get('tail_kind', 'entity') == 'entity'}
    blocking = [dict(kind='relation', id=r['id'], fields=r['missing_information']) for r in rows if r['missing_information']]
    # Optional weak lexical hits cannot block an otherwise complete candidate.
    if not required and any(not r['missing_information'] for r in rows): blocking = []
    blocking.extend(dict(kind='entity', id=side, fields=e['missing_information']) for side,e in entities.items() if e['missing_information'])
    if not considered: blocking.append({'kind': 'relation', 'id': None, 'fields': ['no_relation_candidate_recalled']})
    if assigned and assigned not in registry.properties:
        blocking.append({'kind': 'relation', 'id': assigned, 'fields': ['assigned_id_not_in_bound_release']})
    if triple.get('tail_kind') == 'literal' and (not triple.get('datatype') or triple.get('value') is None):
        blocking.append({'kind': 'entity', 'id': 'tail', 'fields': ['typed_literal_incomplete']})
    return rows, entities, blocking, required, exact


def model_view(report):
    return {key: deepcopy(report[key]) for key in ('version', 'judgment_ready', 'blocking_information',
            'relation_candidates', 'entities', 'confusable_relations', 'search_trace', 'global_gap_confirmed', 'model_rule')}


def prepare(guard, candidate, bundle, context, context_policy, policy, lookup=None):
    """Subset first; missing metadata triggers full-release exact alias and lexical recall."""
    from .ontology_context import select_context, _similarity
    if policy != policy_for({'mapping_preflight': {'enabled': True}}):
        raise ValueError('mapping preparation policy changed')
    original = deepcopy(context); context = deepcopy(context); lookup = deepcopy(lookup or {})
    rows, entities, blockers, required, exact = _inspect(guard, candidate, bundle, context)
    trace = [{'stage': 'review_subset', 'property_count': len(context['properties']), 'metadata_complete': not blockers}]
    excluded = []
    if blockers:
        registry = RelationRegistry(guard.ontology)
        relation_query = ' '.join(str(candidate['triple'].get(k) or '') for k in ('surface_relation', 'relation_label'))
        ranked = sorted((-_similarity(relation_query, prop), identity) for identity, prop in registry.properties.items()
                        if _similarity(relation_query, prop) > 0)
        # Exact IDs and every declared-alias competitor are mandatory; lexical suggestions remain unconfirmed.
        desired = required | set(lookup.get('retrieval_property_ids', []))
        room = max(0, context_policy['limits']['max_properties'] - len(desired))
        desired |= {identity for _, identity in ranked[:min(room, context_policy['limits']['top_k_properties'])]}
        class_ids = {identity for e in entities.values() for identity in e['type_candidate_ids']}
        expanded = {**lookup, 'retrieval_property_ids': sorted(desired),
                    'retrieval_class_ids': sorted(set(lookup.get('retrieval_class_ids', [])) | class_ids)}
        try:
            context = select_context(guard, candidate, bundle, context_policy, expanded)
        except ValueError as error:
            # Keep the source packet and disclose the incomplete retrieval, without trimming mandatory definitions.
            context = original
            excluded = sorted(desired - {p['id'] for p in original['properties']})
            trace.append({'stage': 'supplementary_budget', 'complete': False, 'reason': str(error)})
        trace.append({'stage': 'full_bound_release_and_aliases', 'properties_scanned': len(registry.properties),
                      'classes_scanned': len(guard.classes), 'exact_match_ids': sorted(exact),
                      'property_count': len(context['properties']), 'lexical_recall_is_semantic_confirmation': False})
        rows, entities, blockers, required, exact = _inspect(guard, candidate, bundle, context)
        if excluded: blockers.append({'kind': 'context', 'id': None, 'fields': ['supplementary_context_budget_exceeded']})
    triple = candidate['triple']
    report = {'version': VERSION, 'policy_hash': sha256_json(policy),
        'candidate_hash': sha256_json(candidate), 'evidence_hash': bundle.get('evidence_hash'),
        'ontology_context_hash': sha256_json(context), 'source_catalog_hash': context['source_catalog_hash'],
        'ontology_release_id': candidate.get('ontology_release_id'), 'ontology_version': candidate.get('ontology_version'),
        'judgment_ready': not blockers, 'blocking_information': blockers, 'relation_candidates': rows,
        'entities': entities, 'search_trace': trace, 'budget_excluded_property_ids': excluded,
        'confusable_relations': [{'query': triple.get('surface_relation'), 'candidate_ids': sorted(exact),
             'comparison': 'compare full definitions, declared direction and endpoint constraints'}] if len(exact) > 1 else [],
        'global_gap_confirmed': False, 'semantic_applicability': 'not_programmatically_confirmed', 'model_rule': MODEL_RULE,
        'original_expression': {'candidate_id': candidate['candidate_id'], 'revision': candidate['revision'],
            **{k: deepcopy(triple.get(k)) for k in ('head','surface_relation','tail','qualifiers','qualifier_status')}},
        'formal_mapping': {'assigned_relation_id': triple.get('relation_id'),
            'assigned_head_type': triple.get('head_type'), 'assigned_tail_type': triple.get('tail_type'),
            'candidate_relation_ids': [r['id'] for r in rows], 'semantic_approval': False}}
    if len(json.dumps(context, ensure_ascii=False)) + len(json.dumps(model_view(report), ensure_ascii=False)) > context_policy['limits']['max_chars']:
        raise ValueError('mapping context and preflight exceed frozen context budget; no partial definitions sent')
    return context, report


def frozen(proposal):
    """The optional feature is frozen separately, leaving older V7 requests intact."""
    if 'mapping_preflight_policy' not in proposal: return None
    report = proposal.get('mapping_preflight'); policy = proposal['mapping_preflight_policy']
    from .contracts import proposal_digest
    if (not isinstance(report, dict) or policy != policy_for({'mapping_preflight': {'enabled': True}}) or
            sha256_json(policy) != proposal.get('mapping_preflight_policy_hash') or
            sha256_json(report) != proposal.get('mapping_preflight_hash') or
            report.get('policy_hash') != sha256_json(policy) or
            report.get('candidate_hash') != sha256_json(proposal['candidate']) or
            report.get('evidence_hash') != proposal['evidence_bundle']['evidence_hash'] or
            report.get('ontology_context_hash') != proposal.get('ontology_context_hash') or
            report.get('ontology_context_hash') != sha256_json(proposal.get('ontology_context')) or
            report.get('source_catalog_hash') != proposal.get('ontology_catalog_hash') or
            proposal_digest(proposal) != proposal.get('proposal_digest')):
        raise ValueError('frozen mapping preflight binding mismatch')
    return deepcopy(report)


def diagnostic_report(entry, proposal):
    """Display an explicitly separate post-run analysis only for its exact old packet."""
    if not isinstance(entry, dict) or not isinstance(entry.get('report'), dict): return None
    binding = entry.get('binding') or {}
    expected = {'audit_id': proposal.get('audit_id'), 'proposal_digest': proposal.get('proposal_digest'),
        'candidate_hash': sha256_json(proposal.get('candidate')), 'evidence_hash': proposal.get('evidence_bundle', {}).get('evidence_hash'),
        'ontology_context_hash': proposal.get('ontology_context_hash')}
    report = entry['report']
    if not all(expected.values()) or any(binding.get(k) != v for k,v in expected.items()) or sha256_json(report) != entry.get('report_hash'):
        return None
    return {**deepcopy(report), 'post_run_diagnostic': True}
