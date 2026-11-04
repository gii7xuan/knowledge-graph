"""Revision/package-bound human annotations, independent of admission actions."""
from copy import deepcopy
from .contracts import sha256_json

VERSION = 'human_annotation_v1'
DIMENSIONS = {
    'source_support': {'supported','contradicted','insufficient','unresolved'},
    'expression_status': {'faithful','needs_revision','unresolved'},
    'mapping_status': {'correct','incorrect','unassigned','ambiguous','unresolved'},
    'schema_coverage': {'covered','not_covered','unresolved'},
    'knowledge_scope': {'general','conditional','local_definition','example','derivation','unresolved'},
    'admission_preference': {'include','revise','exclude','unresolved'},
}
RAW_SOURCE = {'支持':'supported','反驳':'contradicted','证据不足':'insufficient','未知':'unresolved'}


def import_annotation(raw, candidate, proposal, *, adjudication=None, ontology=None):
    """Import claims, not truths inferred from unrelated spreadsheet columns."""
    if not isinstance(raw,dict): raise ValueError('raw_annotation must be an object')
    adjudication = deepcopy(adjudication or {})
    if not isinstance(adjudication,dict): raise ValueError('adjudication must be an object')
    supplied = adjudication.get('adjudicated_label') or {}
    if not isinstance(supplied,dict) or set(supplied)-DIMENSIONS.keys(): raise ValueError('unknown adjudicated dimension')
    labels = {k:supplied.get(k,'unresolved') for k in DIMENSIONS}
    for field in ('reason_codes','resolved_issue_codes','observed_supporting_evidence_ids'):
        values=adjudication.get(field) or []
        if not isinstance(values,list) or any(not isinstance(v,str) or not v.strip() for v in values): raise ValueError('invalid '+field)
    for field,values in DIMENSIONS.items():
        if not isinstance(labels[field],str) or labels[field] not in values: raise ValueError('invalid '+field)
    bundle = proposal.get('evidence_bundle') or {}
    available = {x['evidence_id'] for x in bundle.get('items',[]) if x.get('anchor_valid') is True}
    supporting, counter = (adjudication.get(k) or [] for k in ('supporting_evidence_ids','counter_evidence_ids'))
    for ids in (supporting,counter):
        if not isinstance(ids,list) or any(not isinstance(x,str) for x in ids) or not set(ids)<=available:
            raise ValueError('annotation references evidence outside frozen review package')
    if set(supporting)&set(counter): raise ValueError('conflicting human evidence roles require adjudication')
    if not proposal.get('proposal_digest') or not bundle.get('evidence_hash'): raise ValueError('frozen proposal/evidence hash required')
    if sha256_json(proposal.get('candidate')) != sha256_json(candidate): raise ValueError('annotation candidate/proposal mismatch')
    issues = []
    raw_labels = raw.get('human_labels_original') or raw.get('labels') or raw
    if not isinstance(raw_labels,dict): raise ValueError('raw annotation labels must be an object')
    claim = RAW_SOURCE.get(raw_labels.get('来源支持'),raw_labels.get('source_support'))
    note = str(adjudication.get('note') or '')
    semantic_error = adjudication.get('semantic_error')
    if claim=='contradicted' and not counter and not semantic_error: issues.append('CONTRADICTION_REASON_REQUIRED')
    if claim=='supported' and raw_labels.get('事实表述正确')=='否': issues.append('SOURCE_EXPRESSION_SCOPE_CONFLICT')
    if raw_labels.get('关系方向正确')=='否' and raw_labels.get('事实表述正确')=='是': issues.append('DIRECTION_FACT_SCOPE_CONFLICT')
    triple = candidate.get('triple') or {}
    if raw_labels.get('关系映射正确')=='是' and not triple.get('relation_id'): issues.append('FORMAL_MAPPING_UNASSIGNED')
    if raw_labels.get('限定完整')=='不适用' and any(v not in (None,'','unknown','unspecified') for k,v in triple.get('qualifiers',{}).items()
            if k not in {'polarity','modality'}) and not adjudication.get('qualifier_not_applicable_reason'):
        issues.append('QUALIFIER_NOT_APPLICABLE_REASON_REQUIRED')
    support_conflict = set(adjudication.get('observed_supporting_evidence_ids') or [])
    if not support_conflict<=available: raise ValueError('conflict evidence outside package')
    if (claim=='contradicted' or labels['source_support']=='contradicted') and support_conflict: issues.append('HUMAN_SOURCE_SUPPORT_CONFLICT')
    if (claim=='insufficient' or labels['source_support']=='insufficient') and support_conflict: issues.append('HUMAN_MODEL_EVIDENCE_SCOPE_DIFFERENCE')
    if labels['source_support']=='contradicted' and not counter and not semantic_error: issues.append('CONTRADICTION_REASON_REQUIRED')
    if labels['source_support']=='supported' and not supporting: issues.append('SUPPORTING_EVIDENCE_REQUIRED')
    from evidence.units import can_support
    substantive={x['evidence_id'] for x in bundle.get('items',[]) if can_support(x)}
    if set(supporting)-substantive: issues.append('NON_SUBSTANTIVE_SUPPORT_REFERENCE')
    if labels['mapping_status']=='correct' and (not triple.get('relation_id') or not adjudication.get('mapping_evidence')):
        issues.append('VERIFIED_MAPPING_REQUIRED')
    if labels['mapping_status']=='correct' and ontology is not None:
        from schema.relation_registry import RelationRegistry
        prop=RelationRegistry(ontology).properties.get(triple.get('relation_id'))
        proof=adjudication.get('mapping_evidence') or {}
        from .ontology_guard import OntologyGuard,load_snapshot
        if (not prop or not isinstance(proof,dict) or proof.get('relation_id')!=triple.get('relation_id') or
                not proof.get('definition') or proof['definition'] not in [prop.get('definition'),prop.get('description')] or
                OntologyGuard(load_snapshot(ontology)).check(candidate).get('status')!='pass'):
            issues.append('VERIFIED_MAPPING_REQUIRED')
    elif labels['mapping_status']=='correct': issues.append('VERIFIED_MAPPING_REQUIRED')
    coverage = adjudication.get('coverage_evidence') or {}
    if not isinstance(coverage,dict): raise ValueError('coverage_evidence must be an object')
    expected_revision=deepcopy(adjudication.get('expected_revision'))
    if expected_revision is not None:
        if (not isinstance(expected_revision,dict) or not all(isinstance(expected_revision.get(k),str) and expected_revision[k].strip()
                for k in ('head','surface_relation','tail')) or not isinstance(expected_revision.get('qualifiers'),dict) or
                set(expected_revision['qualifiers'])!={'polarity','condition','time_scope','applicability','modality','quantifier'}):
            issues.append('EXPECTED_REVISION_FULL_STATEMENT_REQUIRED')
    evaluation_target=adjudication.get('evaluation_target','original_candidate')
    if evaluation_target not in {'original_candidate','proposed_revision'}: raise ValueError('invalid annotation evaluation_target')
    if evaluation_target=='proposed_revision': issues.append('NEW_REVISION_BINDING_REQUIRED')
    if labels['schema_coverage']=='covered' and (not coverage.get('term_ids') or not coverage.get('definitions') or 'type_paths' not in coverage):
        issues.append('COVERAGE_PROOF_REQUIRED')
    if labels['schema_coverage']=='covered':
        if ontology is None: issues.append('COVERAGE_PROOF_REQUIRED')
        else:
            from .ontology_guard import OntologyGuard,load_snapshot
            from schema.relation_registry import RelationRegistry
            guard=OntologyGuard(load_snapshot(ontology)); terms={**guard.classes,**RelationRegistry(ontology).properties}
            definitions=coverage.get('definitions') or {}; paths=coverage.get('type_paths') or []
            term_ids=coverage.get('term_ids') or []
            relation_id=coverage.get('relation_id') or triple.get('relation_id')
            endpoints={triple.get('head_type')}
            if triple.get('tail_kind','entity')=='entity': endpoints.add(triple.get('tail_type'))
            probe=deepcopy(candidate);probe['triple']['relation_id']=relation_id
            if (not isinstance(term_ids,list) or any(not isinstance(t,str) for t in term_ids) or
                    not isinstance(relation_id,str) or not relation_id or None in endpoints or not ({relation_id}|endpoints)<=set(term_ids) or
                    relation_id not in guard.relations or guard.check(probe).get('status')!='pass'):
                issues.append('COVERAGE_PROOF_REQUIRED')
                term_ids=[]
            if (not isinstance(definitions,dict) or not isinstance(paths,list) or not paths or
                    any(t not in terms or not definitions.get(t) or definitions[t] not in
                        [terms[t].get('definition'),terms[t].get('description')] for t in term_ids) or
                    any(not isinstance(p,list) or not p or any(n not in guard.classes for n in p) or
                        any(b not in guard.parents.get(a,[]) for a,b in zip(p,p[1:])) for p in paths)):
                issues.append('COVERAGE_PROOF_REQUIRED')
    resolved = adjudication.get('adjudication_status')=='resolved'
    if resolved:
        if adjudication.get('adjudicated_by')!='human' or not adjudication.get('reviewer') or not adjudication.get('annotated_at'):
            raise ValueError('resolved annotation requires named human provenance and time')
        if not isinstance(adjudication.get('saw_model_conclusions'),bool): raise ValueError('record whether model conclusions were seen')
        if adjudication.get('full_bundle_reviewed') is not True: issues.append('FULL_REVIEW_PACKAGE_NOT_CONFIRMED')
        explained = set(adjudication.get('resolved_issue_codes') or [])
        if issues and (not note.strip() or not set(issues)<=explained): resolved=False
        # Missing formal identity/proof cannot be waived by a text explanation.
        if set(issues)&{'FORMAL_MAPPING_UNASSIGNED','VERIFIED_MAPPING_REQUIRED','COVERAGE_PROOF_REQUIRED'}:
            if labels['mapping_status']=='correct' or labels['schema_coverage']=='covered': resolved=False
        if set(issues)&{'EXPECTED_REVISION_FULL_STATEMENT_REQUIRED','NEW_REVISION_BINDING_REQUIRED','FULL_REVIEW_PACKAGE_NOT_CONFIRMED'}:
            resolved=False
        if 'SUPPORTING_EVIDENCE_REQUIRED' in issues or 'NON_SUBSTANTIVE_SUPPORT_REFERENCE' in issues:
            resolved=False
        if labels['source_support']=='contradicted' and not counter and not semantic_error:
            resolved=False
    result = {'contract_version':VERSION,'raw_annotation':deepcopy(raw),'adjudicated_label':labels,
        'reason_codes':list(dict.fromkeys([*(adjudication.get('reason_codes') or []),*issues])),
        'supporting_evidence_ids':supporting,'counter_evidence_ids':counter,
        'expected_revision':expected_revision,'evaluation_target':evaluation_target,
        'adjudication_status':'resolved' if resolved else 'pending','adjudication':adjudication,
        'binding':{**{k:candidate.get(k) for k in ('candidate_id','revision','ontology_release_id','profile_hash','source_index_hash')},
            'candidate_content_hash':sha256_json(candidate),'proposal_digest':proposal['proposal_digest'], 'evidence_hash':bundle['evidence_hash']}}
    result['annotation_hash'] = sha256_json(result)
    return result


def training_record(annotation, dimension):
    if dimension not in DIMENSIONS: raise ValueError('unknown training dimension')
    if annotation.get('adjudication_status')!='resolved': return None
    label = annotation['adjudicated_label'].get(dimension)
    if label in (None,'unresolved'): return None
    return {'binding':deepcopy(annotation['binding']),'dimension':dimension,'label':label,
            'label_source':'human_adjudicated','annotation_hash':annotation['annotation_hash']}
