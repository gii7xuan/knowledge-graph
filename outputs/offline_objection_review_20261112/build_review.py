"""Bind the explicit AI decisions to exact frozen source offsets and formal IDs."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from class_decisions import DECISIONS as CLASS_DECISIONS
from relation_decisions import DECISIONS as RELATION_DECISIONS


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    manifest = read(HERE / 'input_manifest.json')
    source = read(ROOT / 'outputs/formal_definitions_20261111_ready/prepared_competency_request.json')['source_documents'][0]
    text = source['text']
    assert hashlib.sha256(text.encode()).hexdigest() == manifest['canonical_source_sha256']
    dossiers = read(HERE / 'term_dossiers.json')
    paragraphs = read(HERE / 'source_paragraphs.json')
    assert len(CLASS_DECISIONS) == 86
    assert len(RELATION_DECISIONS) == 80
    decisions = {}
    for number, action, issue, reason, locators, draft in CLASS_DECISIONS:
        dossier = dossiers[number - 1]
        assert dossier['number'] == number and len(dossier['items']) == 1
        item = dossier['items'][0]
        assert item['schema_kind'] == 'class'
        decisions[item['id']] = (action, issue, reason, locators, draft)
    for identity, *decision in RELATION_DECISIONS:
        assert identity not in decisions
        decisions[identity] = decision
    expected = {item['id'] for dossier in dossiers for item in dossier['items']}
    assert set(decisions) == expected
    evidence = {}
    def anchor(locator):
        position = text.find(locator)
        if position < 0:
            raise ValueError('Source locator absent: ' + locator)
        paragraph = next(p for p in paragraphs if p['start'] <= position < p['end'])
        start, end = max(paragraph['start'], position - 150), min(paragraph['end'], position + len(locator) + 400)
        # Preserve exact source spelling, including LaTeX and line boundaries.
        while text.count(text[start:end]) != 1 and end - start < 1200:
            start, end = max(0, start - 100), min(len(text), end + 100)
        quote = text[start:end]
        assert locator in quote and text.count(quote) == 1
        identity = 'offline_ev_' + hashlib.sha256(f"{source['document_version']}:{start}:{end}".encode()).hexdigest()[:20]
        evidence[identity] = {'evidence_id': identity,
            **{k: source[k] for k in ('document_id', 'document_version', 'source_index_hash')},
            'offset_basis': 'canonical_document', 'start': start, 'end': end, 'quote': quote,
            'paragraph_id': paragraph['paragraph_id']}
        return identity
    semantic_issues = {'coefficient_force_conflation', 'subtype_as_definition', 'wrong_range_role',
        'context_object_as_agent', 'formula_as_agent_misread', 'described_quantity_as_agent',
        'representation_relation_ambiguous', 'wrong_dependency_arguments', 'field_or_trajectory_as_agent',
        'attribute_role_suppressed', 'physical_region_vs_distribution', 'part_whole_direction_reversed',
        'design_intent_as_unconditional_action', 'basis_as_agent_wrong_target', 'object_as_force_wrong_role',
        'projection_result_as_input', 'actor_and_parameter_scope', 'partial_contribution_as_whole',
        'conditional_example_promoted_to_taxonomy'}
    records, term_records, drafts = [], [], []
    for dossier in dossiers:
        children = []
        for item in dossier['items']:
            action, issue, reason, locators, draft = decisions[item['id']]
            anchors = list(dict.fromkeys(anchor(locator) for locator in locators))
            if action == 'keep':
                objection = 'not_substantiated_for_this_entry'
            elif action.startswith('hold_'):
                objection = 'requires_source_scope_or_existing_review_clarification'
            elif issue.startswith('missing_definition'):
                objection = 'metadata_gap_not_proof_of_name_absence'
            else:
                objection = 'definition_scope_or_modeling_issue_requires_revision'
            record = {'term_number': dossier['number'], 'term': dossier['term'], 'schema_id': item['id'],
                'schema_kind': item['schema_kind'], 'annotated_by': 'ai', 'human_gold': False,
                'recommendation': action, 'issue_kind': issue, 'reason': reason,
                'objection_assessment': objection,
                'declared_semantics_needs_review': issue in semantic_issues or action.startswith('hold_'),
                'name_or_relation_supported_only_by_literal_match': False,
                'literal_name_occurrences': text.count(dossier['term']),
                'current_definition': item.get('description') or None, 'base_definition': item.get('base_definition'),
                'definition_missing': not bool(item.get('description') or item.get('definitions')),
                'current_parent': item.get('parent'), 'current_parent_label': item.get('parent_label'),
                'current_domain': item.get('domain') or [], 'current_domain_labels': item.get('domain_labels') or [],
                'current_range': item.get('range') or [], 'current_range_labels': item.get('range_labels') or [],
                'evidence_ids': anchors, 'unapproved_definition_draft': draft,
                'committee_vote_changed': False, 'ontology_changed': False}
            records.append(record)
            children.append(record)
            if draft is not None:
                drafts.append({'kind': 'revise_definition', 'target': item['id'],
                    'changes': {'definition': draft, 'definition_language': 'zh'}, 'evidence_ids': anchors,
                    'annotated_by': 'ai', 'approved': False, 'applied': False,
                    'precondition': 'Fresh modeling review, full competency gate and exact compiler preparation required before any publication'})
        actions = [row['recommendation'] for row in children]
        action = 'hold' if any(a.startswith('hold_') for a in actions) else 'revise' if 'revise' in actions else 'keep'
        term_records.append({'number': dossier['number'], 'term': dossier['term'], 'recommendation': action,
            'schema_ids': [row['schema_id'] for row in children], 'item_count': len(children),
            'per_id_recommendations': {row['schema_id']: row['recommendation'] for row in children},
            'annotated_by': 'ai', 'human_gold': False})
    assert len(records) == 166 and len(term_records) == 130
    for e in evidence.values():
        assert text[e['start']:e['end']] == e['quote']
    write(HERE / 'per_id_review.json', records)
    write(HERE / 'term_review.json', term_records)
    write(HERE / 'source_evidence.json', list(evidence.values()))
    write(HERE / 'unapproved_definition_drafts.json', {'approved': False, 'applied': False,
        'basis_proposal_digest': manifest['proposal_digest'], 'operations': drafts})
    all_items = []
    for key, value in read(ROOT / 'outputs/formal_definitions_20261111_ready/prepared_proposal.json')['merged_schema'].items():
        if key in ('classes', 'object_properties', 'data_properties'):
            all_items.extend(value.values() if isinstance(value, dict) else value)
    summary = {'reviewed_objection_terms': len(term_records), 'reviewed_schema_ids': len(records),
        'class_terms': 86, 'relationship_labels': 44, 'relationship_ids': 80,
        'term_recommendations': dict(Counter(row['recommendation'] for row in term_records)),
        'id_recommendations': dict(Counter(row['recommendation'] for row in records)),
        'draft_definition_count': len(drafts), 'source_evidence_count': len(evidence),
        'scope': 'DeepSeek 130 objections only; not a complete certification of the entire baseline ontology',
        'outside_scope_ids': [item['id'] for item in all_items if item['id'] not in expected],
        'annotated_by': 'ai', 'human_gold': False, 'model_api_calls': 0, 'ontology_changed': False,
        'original_deepseek_fail_retained': True}
    write(HERE / 'review_summary.json', summary)
    print(json.dumps(summary | {'outside_scope_ids': len(summary['outside_scope_ids'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
