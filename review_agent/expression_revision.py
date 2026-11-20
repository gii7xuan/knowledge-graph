"""Source-grounded edits to the whole triple, before independent revision review."""
from copy import deepcopy

from .planner_contracts import validate_action
from .revision_patch import merge_triple_patch

VERSION = 'grounded_expression_revision_v1'


def validate_revision_action(action, candidate, bundle, *, checked_scopes=()):
    action = validate_action(deepcopy(action))
    if action['action'] != 'propose_revision':
        raise ValueError('expression revision requires propose_revision')
    args = action['arguments']
    changes = args['changes']
    original = candidate['triple']
    from .repository import ValidationError
    try:
        updated = merge_triple_patch(original, changes,
            strict=candidate.get('contract_version') == 'extraction_candidate_v4')
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    for key in ('head', 'surface_relation'):
        if key in changes and (not isinstance(updated.get(key), str) or not updated[key].strip()):
            raise ValueError('head and surface_relation must remain nonempty expressions')
    if 'tail' in changes and updated.get('tail_kind', 'entity') == 'entity' and (
            not isinstance(updated.get('tail'), str) or not updated['tail'].strip()):
        raise ValueError('an entity tail must remain nonempty')
    allowed = {e['evidence_id'] for e in bundle['items'] if e.get('anchor_valid')}
    cited = set(args['evidence_ids'])
    if not cited or not cited <= allowed:
        raise ValueError('revision needs verified source evidence')
    values, statuses = changes.get('qualifiers', {}), changes.get('qualifier_status', {})
    if set(values) != set(statuses):
        raise ValueError('changed qualifier values and statuses must be paired')
    fields = {k for k in changes if k not in {'qualifiers', 'qualifier_status'}
              and original.get(k) != updated.get(k)}
    qualifiers = {k for k in values if original.get('qualifiers', {}).get(k) != values[k]
                  or original.get('qualifier_status', {}).get(k) != statuses[k]}
    required = fields | qualifiers
    if not required:
        raise ValueError('revision needs a substantive changed field')
    evidence, notes = args.get('field_evidence') or {}, args.get('field_notes') or {}
    if set(evidence) != required or set(notes) != required:
        raise ValueError('each changed expression field needs its own evidence and interpretation')
    field_map = {}
    for key in required:
        if not evidence[key] or not set(evidence[key]) <= cited or not str(notes[key]).strip():
            raise ValueError('field evidence must cite verified draft evidence')
        if key in fields:
            field_map[key] = list(evidence[key])
            continue
        if statuses[key] not in {'extracted', 'not_stated'}:
            raise ValueError('a revised qualifier must be source-grounded or explicitly not stated')
        if key == 'polarity' and (values[key] not in {'positive', 'negative'} or statuses[key] != 'extracted'):
            raise ValueError('polarity requires an explicit source-grounded positive/negative assessment')
        if statuses[key] == 'not_stated':
            scope = (args.get('reviewed_scopes') or {}).get(key) or {}
            if (scope.get('scope') not in checked_scopes or not scope.get('evidence_ids')
                    or not set(scope['evidence_ids']) <= set(evidence[key])):
                raise ValueError('not_stated requires an inspected scope and exact field evidence')
        for prefix in ('qualifiers', 'qualifier_status'):
            if original.get(prefix, {}).get(key) != updated.get(prefix, {}).get(key):
                field_map[prefix + '.' + key] = list(evidence[key])
    return field_map
