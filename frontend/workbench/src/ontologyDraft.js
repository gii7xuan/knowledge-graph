export function draftForCandidate(item) {
  return {
    name: item.name || item.property_name || item.text_mention || '',
    definition: item.definition_candidate || '',
    parent: item.parent_candidate || '',
    domain: item.domain_candidate || '',
    range: item.candidate_type === 'datatype_property' || item.property_type === 'datatype_property'
      ? item.range_datatype || ''
      : item.range_candidate || item.range_datatype || '',
    propertyType: item.property_type || 'object_property',
    mappedTo: item.mapped_to || '',
    mappedType: item.mapped_type || '',
    comment: ''
  };
}

export function candidateModifications(item, draft) {
  const name = draft.name.trim();
  switch (item.candidate_type) {
    case 'class':
      return { name, parent_candidate: draft.parent.trim(), definition_candidate: draft.definition.trim() };
    case 'object_property':
      return { name, domain_candidate: draft.domain.trim(), range_candidate: draft.range.trim(), definition_candidate: draft.definition.trim() };
    case 'datatype_property':
      return { name, domain_candidate: draft.domain.trim(), range_datatype: draft.range.trim(), definition_candidate: draft.definition.trim() };
    case 'constraint':
      return {
        property_name: name,
        property_type: draft.propertyType,
        domain_candidate: draft.domain.trim(),
        range_candidate: draft.propertyType === 'object_property' ? draft.range.trim() : '',
        range_datatype: draft.propertyType === 'datatype_property' ? draft.range.trim() : ''
      };
    case 'mapping':
      return { text_mention: name, mapped_to: draft.mappedTo.trim(), mapped_type: draft.mappedType.trim() };
    default:
      return { name };
  }
}
