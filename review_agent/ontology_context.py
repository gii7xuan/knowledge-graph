"""Deterministic, bounded ontology retrieval for a frozen review round."""
from copy import deepcopy
import json
import re

from ontologyBuild.ontology_adapter import normalize_name
from schema.relation_registry import RelationRegistry
from .contracts import sha256_json

VERSION = 'review_ontology_context_v1'
DEFAULT_LIMITS = {'top_k_properties': 8, 'max_properties': 24, 'max_classes': 64, 'max_chars': 32000}


def policy_for(config):
    limits = dict(DEFAULT_LIMITS)
    incoming = config.get('ontology_context') or {}
    if set(incoming) - set(limits):
        raise ValueError('unknown ontology context setting')
    for key, value in incoming.items():
        if type(value) is not int or not 1 <= value <= DEFAULT_LIMITS[key]:
            raise ValueError('ontology context limits may only tighten defaults: ' + key)
        limits[key] = value
    return {'version': VERSION, 'limits': limits, 'selection': 'exact_alias_then_lexical_and_types',
            'required_matches': 'complete_or_stop', 'absence_from_subset': 'not_proof_of_gap'}


def _tokens(value):
    text = normalize_name(str(value or ''))
    words = set(re.findall(r'[a-z0-9_]+', text))
    for run in re.findall(r'[\u3400-\u9fff]+', text):
        words.update(run[i:i+2] for i in range(len(run)-1))
        if len(run) == 1:
            words.add(run)
    return words


def _names(item):
    return [item.get('label'), item.get('name'), *(item.get('aliases') or []),
            *[x.get('value') for x in item.get('labels', []) if isinstance(x, dict)]]


def _similarity(query, item):
    names = ' '.join(str(x) for x in _names(item) if x)
    a, b = _tokens(query), _tokens(names)
    label = len(a & b) / max(1, len(a))
    definitions = ' '.join(str(item.get(k) or '') for k in ('definition', 'description', 'definitions'))
    return 4 * label + len(a & _tokens(definitions)) / max(1, len(a))


def select_context(guard, candidate, bundle, policy, lookup=None):
    """Search the full server-owned schema, return only complete selected records.

    Exact IDs and declared aliases (including competing aliases), endpoint types,
    domain/range classes and all their ancestors are mandatory. Optional recall is
    packed under one shared budget. No definition/constraint is text-truncated.
    """
    if policy.get('version') != VERSION:
        raise ValueError('unsupported ontology context policy')
    if policy != policy_for({'ontology_context':policy.get('limits')}):
        raise ValueError('ontology context policy differs from implemented selector')
    registry = RelationRegistry(guard.ontology)
    properties, classes = registry.properties, guard.classes
    triple = candidate.get('triple') or candidate
    hints = candidate.get('extraction_hints') or {}
    lookup = lookup or {}
    queries = [triple.get('relation_id'), triple.get('surface_relation'), triple.get('relation_label'),
               *lookup.get('retrieval_queries', [])]
    queries = sorted({str(x) for x in queries if x})
    required = set()
    exact_searches = []
    for query in queries:
        result = registry.resolve(query)
        ids = {result['relation_id']} if result.get('relation_id') else set(result.get('matched_ids', []))
        required.update(ids)
        exact_searches.append({'query': query, 'status': result['status'], 'matched_ids': sorted(ids)})
    required.update(x for x in lookup.get('retrieval_property_ids', []) if x in properties)
    endpoints = set()
    endpoint_queries = {}
    for side in ('head', 'tail'):
        actual = triple.get(side + '_type')
        actual_id = guard.class_index.get(normalize_name(actual)) if actual else None
        value = actual if actual_id else hints.get(side + '_type_candidate') or actual
        if value:
            endpoint_queries[side] = {'query': value, 'class_id': guard.class_index.get(normalize_name(value)),
                                     'confirmed_candidate_type': actual_id is not None}
            if endpoint_queries[side]['class_id']:
                endpoints.add(endpoint_queries[side]['class_id'])

    def closure(ids, extra=()):
        wanted = set(endpoints) | set(extra)
        for identity in ids:
            for key in ('domain', 'range'):
                refs = properties[identity].get(key) or []
                for ref in ([refs] if isinstance(refs, str) else refs):
                    found = guard.class_index.get(normalize_name(ref))
                    if found:
                        wanted.add(found)
        stack = list(wanted)
        while stack:
            for parent in sorted(guard.parents.get(stack.pop(), ())):
                if parent in classes and parent not in wanted:
                    wanted.add(parent)
                    stack.append(parent)
        return wanted

    full_catalog = {**registry.reference_catalog(), 'classes': list(classes.values())}
    source_hash = sha256_json(full_catalog)
    limits = policy['limits']
    # Evidence affects recall, never the truth of a relation or an endpoint type.
    source_text = ' '.join(str(e.get('quote') or '') for e in bundle.get('items', []))[:2000]
    relation_query = ' '.join(queries)[:2000]
    text_query = ' '.join(str(triple.get(k) or '') for k in ('head', 'tail')) + ' ' + source_text
    ranked = []
    for identity, item in properties.items():
        score = 10 * _similarity(relation_query, item) + _similarity(text_query, item)
        # A weak fallback also recalls alternative predicates with compatible types.
        if endpoints:
            refs = set()
            for field in ('domain', 'range'):
                values = item.get(field) or []
                refs.update(guard.class_index.get(normalize_name(x)) for x in ([values] if isinstance(values, str) else values))
            if refs & endpoints:
                score += .05
        if score > 0 and identity not in required:
            ranked.append((-score, identity))
    ranked.sort()
    selected = set(required)
    selected_classes = closure(selected)

    def materialize(ids, class_ids):
        return {'version': VERSION, 'scope': 'retrieved_subset', 'policy_hash': sha256_json(policy),
                'source_catalog_hash': source_hash,
                'ontology_version': candidate.get('ontology_version'),
                'ontology_release_id': candidate.get('ontology_release_id'), 'profile_hash': candidate.get('profile_hash'),
                'source_counts': {'properties': len(properties), 'classes': len(classes)},
                'properties': [deepcopy(properties[x]) for x in sorted(ids)],
                'classes': [{**deepcopy(classes[x]), 'parents': sorted(guard.parents.get(x, ())) }
                            for x in sorted(class_ids)],
                'retrieval': {'queries': queries, 'exact_searches': exact_searches,
                              'endpoint_queries': endpoint_queries, 'required_property_ids': sorted(required),
                              'searched_full_frozen_schema': True, 'recall_is_mapping_confirmation': False},
                'full_catalog_included': ids == set(properties) and class_ids == set(classes),
                'absence_confirmed': False,
                'coverage_rule': 'A missing subset item is not an ontology gap; request further lookup or keep mapping unresolved.'}

    def fits(ids, class_ids):
        return (len(ids) <= limits['max_properties'] and len(class_ids) <= limits['max_classes']
                and len(json.dumps(materialize(ids, class_ids), ensure_ascii=False)) <= limits['max_chars'])

    if not fits(selected, selected_classes):
        raise ValueError('required ontology definitions and ancestor closure exceed context budget; manual review required')
    for _, identity in ranked:
        if len(selected) >= max(len(required), limits['top_k_properties']):
            break
        wanted = selected | {identity}
        needed = closure(wanted, selected_classes)
        if fits(wanted, needed):
            selected, selected_classes = wanted, needed
    # Relevant standalone classes can matter even when no relation was recalled.
    class_ranking = sorted((-_similarity(text_query + ' ' + relation_query, item), identity)
                           for identity, item in classes.items() if identity not in selected_classes)
    added = 0
    for score, identity in class_ranking:
        if score >= 0 or added >= 4:
            break
        needed = closure(selected, selected_classes | {identity})
        if fits(selected, needed):
            selected_classes = needed
            added += 1
    return materialize(selected, selected_classes)


def frozen_context(proposal):
    """Validate the stored subset, never recompute it during recovery."""
    context = proposal.get('ontology_context')
    policy = proposal.get('ontology_context_policy')
    if not isinstance(context, dict) or not isinstance(policy, dict):
        raise ValueError('frozen ontology context or policy missing')
    from .contracts import proposal_digest
    if proposal_digest(proposal) != proposal.get('proposal_digest'):
        raise ValueError('frozen ontology proposal digest mismatch')
    if (policy.get('version') != VERSION or sha256_json(policy) != proposal.get('ontology_context_policy_hash')
            or context.get('policy_hash') != sha256_json(policy)
            or sha256_json(context) != proposal.get('ontology_context_hash')
            or context.get('source_catalog_hash') != proposal.get('ontology_catalog_hash')):
        raise ValueError('frozen ontology context hash/policy mismatch')
    for key in ('ontology_version', 'ontology_release_id', 'profile_hash'):
        if context.get(key) != proposal.get('candidate', {}).get(key):
            raise ValueError('frozen ontology context release mismatch')
    return context


def selectable_properties(proposal):
    if proposal.get('response_contract_version') == 'validator_response_v6_3':
        return frozen_context(proposal)['properties']
    return (proposal.get('ontology_catalog') or {}).get('properties', [])
