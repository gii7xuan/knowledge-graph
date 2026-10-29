"""Independent human dimensions; unknown never becomes a negative label."""
from .repository import UnprocessableEntityError

BINARY_DIMENSIONS=('valid_for_kg','schema_valid','task_relevance','ontology_gap','relation_mapping_correct','triple_correct','schema_compatible')
SOURCE_VALUES={'supported','contradicted','insufficient','unknown',None}


def validate_labels(labels):
    if not isinstance(labels,dict): raise UnprocessableEntityError('labels must be an object')
    for field in BINARY_DIMENSIONS:
        value=labels.get(field)
        if value is not None and (type(value) is not int or value not in (0,1)):
            raise UnprocessableEntityError(field+' must be 0, 1 or null')
    for field in ('source_support','evidence_support'):
        value=labels.get(field)
        if value is not None and (not isinstance(value,str) or value not in SOURCE_VALUES):
            raise UnprocessableEntityError(field+' has an invalid value')
    return dict(labels)
