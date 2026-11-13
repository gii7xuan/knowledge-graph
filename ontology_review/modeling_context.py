"""Schema facts and non-vetoing diagnostics for new ontology review prompts."""
from copy import deepcopy
from difflib import SequenceMatcher
from review_agent.contracts import sha256_json
from .delta import catalog

VERSION = 'ontology_modeling_context_v1'
PROMPT_VERSION = 'ontology_extension_validator_v3_modeling_context_v1'


def enabled(proposal):
    return proposal.get('versions', {}).get('prompt_version') == PROMPT_VERSION


def definition(item):
    return item.get('definition') or item.get('description') or None


def state(item):
    if not item:
        return None
    keys = ('id', 'label', 'iri', 'source_iri', 'schema_kind', 'parent', 'parents',
            'domain', 'range', 'domain_semantics', 'range_semantics', 'datatype', 'datatypes', 'aliases')
    return {k: deepcopy(item[k]) for k in keys if k in item} | {'definition': definition(item)}


def display_state(value):
    if value is None:
        return None
    # Identity IRIs and all omitted annotations remain in the complete RDF/index.
    return {k: deepcopy(v) for k, v in value.items()
            if k not in {'iri', 'source_iri'} and (v not in (None, []) or k == 'definition')}


def _verify_graph_binding(proposal):
    patch = proposal.get('source_patch')
    if not patch:
        return {'source': 'complete_project_schema', 'rdf_verified': False}
    from ontologyBuild.rdf_graph import parse_turtle
    from ontologyBuild.rdf_artifact import graph_hash
    before, after = parse_turtle(patch['base_rdf']), parse_turtle(patch['merged_rdf'])
    if graph_hash(before) != patch['base_graph_hash'] or graph_hash(after) != patch['graph_hash']:
        raise ValueError('modeling context RDF graph binding differs from frozen source')
    from ontologyBuild.owl_to_project_schema import convert_turtle
    for name, text in (('base_schema', patch['base_rdf']), ('merged_schema', patch['merged_rdf'])):
        supplied = catalog(proposal[name])
        pinned = {item.get('iri') or item.get('source_iri'): identity for identity, item in supplied.items()
                  if item.get('iri') or item.get('source_iri')}
        actual = catalog(convert_turtle(text, iri_id_map=pinned)['project_schema'])
        if set(supplied) != set(actual) or any(state(supplied[i]) != state(actual[i]) for i in supplied):
            raise ValueError('modeling context schema facts differ from complete frozen RDF: ' + name)
    return {'source': 'complete_frozen_rdf_and_project_schema', 'rdf_verified': True,
            'schema_facts_reimported_and_verified': True,
            'base_graph_hash': patch['base_graph_hash'], 'merged_graph_hash': patch['graph_hash']}


def build(proposal):
    before, after = catalog(proposal['base_schema']), catalog(proposal['merged_schema'])
    touched = {o['target'] for o in proposal['operations']}
    references = set(touched)
    for op in proposal['operations']:
        for field in ('domain', 'range', 'parents', 'parent', 'into'):
            values = op['changes'].get(field) or []
            references.update(values if isinstance(values, list) else [values])
    for identity in touched:
        for item in (before.get(identity, {}), after.get(identity, {})):
            for field in ('domain', 'range', 'parents'):
                references.update(item.get(field) or [])
            if item.get('parent'):
                references.add(item['parent'])
    cards = []
    for identity in sorted(references):
        if identity not in before and identity not in after:
            continue
        old, new = state(before.get(identity)), state(after.get(identity))
        card = {'schema_id': identity, 'base': display_state(old)}
        if new != old:
            card['merged_changes'] = ({k: v for k, v in display_state(new).items() if not old or old.get(k) != v}
                                      if new is not None else {'retired': True})
            if new is not None:
                card['removed_display_fields'] = sorted(set(display_state(old) or {}) - set(display_state(new)))
        cards.append(card)
    labels = {x.get('label') for i in touched for x in (before.get(i, {}), after.get(i, {}))
              if x.get('schema_kind') != 'class'} - {None}
    properties = [state(item) for identity, item in sorted(before.items())
                  if item['schema_kind'] in {'object_property', 'data_property'} and item.get('label') in labels]
    examples = [item for item in properties if item['id'] not in touched]
    related = []
    targets = [after.get(i) or before.get(i) for i in sorted(touched)
               if (after.get(i) or before.get(i))['schema_kind'] == 'class']
    for identity, item in sorted(before.items()):
        if identity in references or item['schema_kind'] != 'class':
            continue
        score = max((SequenceMatcher(None, t.get('label', ''), item.get('label', '')).ratio() for t in targets), default=0)
        if score >= .45:
            related.append((score, identity, state(item)))
    related.sort(key=lambda row: (-row[0], row[1]))
    result = {'version': VERSION, 'bindings': {k: proposal.get(k) for k in ('base_release_id', 'delta_hash', 'evidence_hash')},
        'schema_graph_binding': _verify_graph_binding(proposal),
        'operation_reviews': [{'operation_index': number, 'kind': op['kind'], 'target': op['target'],
            'actual_target_label': (after.get(op['target']) or before.get(op['target']))['label'],
            'before_changed_fields': {field: (definition(before.get(op['target'], {})) if field == 'definition'
                                               else before.get(op['target'], {}).get(field)) for field in op['changes']},
            'requested_changes_from': 'operations[' + str(number) + '].changes', 'source_evidence_ids': list(op['evidence_ids'])}
            for number, op in enumerate(proposal['operations'])],
        'schema_reference_cards': cards,
        'reference_card_encoding': 'base overwritten by merged_changes; omitted changes mean the state is unchanged; full graphs remain authoritative',
        'same_label_base_property_examples': [display_state(x) for x in examples[:12]], 'changed_targets_already_in_reference_cards': True,
        'same_label_property_total': len(properties), 'same_label_examples_truncated': len(examples) > 12,
        'name_related_base_classes': [display_state(row[2]) for row in related[:10]], 'name_related_class_total': len(related),
        'name_similarity_is_not_semantic_equivalence': True,
        'facts_are_not_source_or_semantic_approval': True, 'peer_votes_included': False}
    result['context_hash'] = sha256_json(result)
    return result


def verify(proposal):
    if not enabled(proposal):
        return
    expected = build(proposal)
    if proposal.get('modeling_context') != expected:
        raise ValueError('missing or changed frozen ontology modeling context')
    record = proposal.get('check_records', {}).get('modeling_context')
    if record is not None and record != check_record(expected):
        raise ValueError('modeling context check record differs from verified context')


def check_record(context):
    value = {'status': 'pass', 'scope': 'derived_schema_facts_only',
             'context_hash': context['context_hash'],
             'rdf_schema_projection_verified': bool(context['schema_graph_binding'].get('schema_facts_reimported_and_verified')),
             'independent_semantic_approval': False, 'substitutes_for_source_evidence': False}
    return {**value, 'record_id': 'check_' + sha256_json(['modeling_context', value])}


def suggestion_diagnostics(value, proposal):
    """Report operational issues without changing the model's valid decision."""
    if not isinstance(value, dict) or value.get('suggested_changes') is None:
        return []
    suggestions = value.get('suggested_changes')
    if not isinstance(suggestions, dict):
        return [{'code': 'SUGGESTION_LAYOUT_UNRECOGNIZED', 'severity': 'warning'}]
    before, after = catalog(proposal['base_schema']), catalog(proposal['merged_schema'])
    requested = {}
    for op in proposal['operations']:
        requested.setdefault(op['target'], {}).update(op['changes'])
    diagnostics = []
    for identity, changes in suggestions.items():
        item = after.get(identity) or before.get(identity)
        if not item:
            diagnostics.append({'code': 'UNKNOWN_SUGGESTION_TARGET', 'severity': 'warning', 'target': identity})
            continue
        if not isinstance(changes, dict):
            diagnostics.append({'code': 'SUGGESTION_FIELDS_UNRECOGNIZED', 'severity': 'warning', 'target': identity})
            continue
        equal, restored, refs, unknown = [], [], set(), set()
        for field, proposed in changes.items():
            current = requested.get(identity, {}).get(field, definition(item) if field == 'definition' else item.get(field))
            base = definition(before.get(identity, {})) if field == 'definition' else before.get(identity, {}).get(field)
            if proposed == current:
                equal.append(field)
            elif identity in before and proposed == base:
                restored.append(field)
            if field in {'domain', 'range', 'parents', 'parent', 'into'}:
                for ref in proposed if isinstance(proposed, list) else [proposed]:
                    if not isinstance(ref, str):
                        continue
                    if ref in after or ref in before:
                        refs.add(ref)
                    else:
                        unknown.add(ref)
        if equal:
            diagnostics.append({'code': 'SUGGESTION_EQUALS_PROPOSED_STATE', 'severity': 'warning', 'target': identity, 'fields': sorted(equal)})
        if restored:
            diagnostics.append({'code': 'SUGGESTION_RESTORES_BASE_FIELD', 'severity': 'info', 'target': identity, 'fields': sorted(restored)})
        if refs:
            diagnostics.append({'code': 'REFERENCED_SCHEMA_ID_LABELS', 'severity': 'info', 'target': identity,
                'references': [{'id': ref, 'label': (after.get(ref) or before[ref])['label'],
                                'schema_kind': (after.get(ref) or before[ref])['schema_kind']} for ref in sorted(refs)]})
        if unknown:
            diagnostics.append({'code': 'UNRESOLVED_SUGGESTION_REFERENCES', 'severity': 'warning', 'target': identity, 'references': sorted(unknown)})
    return diagnostics
