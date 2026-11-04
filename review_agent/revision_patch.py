"""Strict partial triple patches; never replace unrelated nested qualifiers."""
from copy import deepcopy
import math
from extract_triplets.candidate_contract import QUALIFIER_FIELDS, STATUS_VALUES, MODALITIES


def merge_triple_patch(original, patch, *, strict=False):
    from .repository import EDITABLE_TRIPLE_FIELDS, ValidationError
    if not isinstance(patch, dict) or not patch or set(patch) - EDITABLE_TRIPLE_FIELDS:
        raise ValidationError('revision patch contains protected or unknown fields')
    updated = deepcopy(original)
    for name, value in patch.items():
        if name in {'qualifiers', 'qualifier_status'}:
            if not isinstance(value, dict) or set(value) - set(QUALIFIER_FIELDS):
                raise ValidationError(name + ' must contain known qualifier fields')
            if name == 'qualifier_status' and any(v not in STATUS_VALUES for v in value.values()):
                raise ValidationError('invalid qualifier status enum')
            if name == 'qualifiers' and any(v is not None and not isinstance(v, str) for v in value.values()):
                raise ValidationError('qualifier values must be strings or null')
            updated[name] = {**deepcopy(updated.get(name) or {}), **deepcopy(value)}
        elif name == 'semantic_context':
            from extract_triplets.semantic_context import validate_context
            prior = original.get('semantic_context') or {}
            if not isinstance(value,dict): raise ValidationError('semantic_context must be an object')
            context = {**deepcopy(prior),**deepcopy(value)}
            for nested in ('entity_mentions','relation_meaning'):
                if nested in value and isinstance(value[nested],dict):
                    context[nested] = {**deepcopy(prior.get(nested) or {}),**deepcopy(value[nested])}
            if 'entity_mentions' in value and isinstance(value['entity_mentions'],dict):
                for side,mention in value['entity_mentions'].items():
                    if isinstance(mention,dict): context['entity_mentions'][side]={**deepcopy((prior.get('entity_mentions') or {}).get(side) or {}),**deepcopy(mention)}
            context = validate_context(context)
            # Original mentions are provenance, not an editable normalization.
            context['origin_mentions'] = deepcopy(prior.get('origin_mentions') or prior.get('entity_mentions') or
                {side:{'entity_surface':original[side],'canonical_entity':original[side],'referent_scope':None}
                 for side in ('head','tail') if isinstance(original.get(side),str)})
            updated[name] = context
        elif name == 'value':
            if not (value is None or isinstance(value, (str, bool, int, float))) or (
                    isinstance(value, float) and not math.isfinite(value)):
                raise ValidationError('literal value must be a finite scalar')
            updated[name] = value
        elif value is not None and not isinstance(value, str):
            raise ValidationError('triple field must be string or null: ' + name)
        else:
            updated[name] = deepcopy(value)
    if strict:
        values, statuses = updated.get('qualifiers', {}), updated.get('qualifier_status', {})
        if set(values) != set(QUALIFIER_FIELDS) or set(statuses) != set(QUALIFIER_FIELDS):
            raise ValidationError('all six qualifier values and statuses are required')
        if any(v not in STATUS_VALUES for v in statuses.values()):
            raise ValidationError('invalid qualifier status enum')
        if values['polarity'] not in {'positive', 'negative', 'unknown'}:
            raise ValidationError('invalid polarity enum')
        if values['modality'] is not None and values['modality'] not in MODALITIES:
            raise ValidationError('invalid modality enum')
        for field in QUALIFIER_FIELDS:
            value, status = values[field], statuses[field]
            if value is not None and not isinstance(value, str):
                raise ValidationError('invalid qualifier value: ' + field)
            if status == 'extracted' and (not value or value == 'unknown'):
                raise ValidationError('extracted qualifier requires an explicit value: ' + field)
            if status == 'not_stated' and value not in (None, '', 'unspecified'):
                raise ValidationError('not_stated qualifier must not assert a value: ' + field)
        if updated.get('tail_kind', 'entity') not in {'entity', 'literal', 'event', 'proposition'}:
            raise ValidationError('invalid tail_kind')
        if updated.get('tail_kind') == 'literal' and (not updated.get('datatype') or updated.get('value') is None):
            raise ValidationError('typed literal requires datatype and value')
        if not all(isinstance(updated.get(k), str) and updated[k].strip() for k in ('head', 'surface_relation', 'tail')):
            raise ValidationError('head, surface_relation and tail must remain nonempty')
    return updated


def expression_diagnostics(candidate):
    triple = candidate.get('triple', {})
    if candidate.get('contract_version') != 'extraction_candidate_v4':
        return {'expression_complete': True, 'blocking_fields': []}
    fields = [key for key in QUALIFIER_FIELDS if triple.get('qualifier_status', {}).get(key, 'unknown') == 'unknown']
    if triple.get('qualifiers', {}).get('polarity') not in {'positive', 'negative'} and 'polarity' not in fields:
        fields.append('polarity')
    return {'expression_complete': not fields, 'blocking_fields': fields}
