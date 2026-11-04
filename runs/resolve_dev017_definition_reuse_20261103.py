"""Record the user's definition-reuse disposition without creating a fact.

The existing OWL definition and exact source span are verified and bound in a
development resolution record. Original labels, candidates and votes are kept.
"""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from importlib.util import spec_from_file_location, module_from_spec
import json
from pathlib import Path

from rdflib import URIRef, SKOS
from ontologyBuild.rdf_graph import parse_turtle
from ontology_review.delta import catalog
from review_agent.admission import load_admission_artifacts
from review_agent.contracts import sha256_json
from review_agent.repository import ReviewRepository
from evidence.verification import make_evidence_bundle, verify_evidence_bundle

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/dev30_mapping_source_20261103'
ANALYSIS = ROOT / 'outputs/dev30_analysis_20261103'
DEST = OUT / 'DEV-017_definition_reuse_resolution.json'
spec = spec_from_file_location('dev4_pilot', ROOT / 'runs/prepare_dev30_revision_pilot_20261103.py')
pilot = module_from_spec(spec); spec.loader.exec_module(pilot)
integrity = pilot.verify_inputs(json.loads((pilot.SOURCE / 'sample_data.json').read_text(encoding='utf-8')))
frozen = json.loads((ROOT / 'outputs/dev30_revision_pilot_20261103/frozen_targets.json').read_text(encoding='utf-8'))
item = next(i for i in frozen['items'] if i['sample_id'] == 'DEV-017')
repo = ReviewRepository(OUT / 'development_review.sqlite3')
candidate = repo.get_revision(item['base_binding']['candidate_id'])
assert sha256_json(candidate) == item['base_binding']['candidate_content_hash'], 'DEV-017 was unexpectedly revised'
release, document = load_admission_artifacts(repo, candidate)
term = catalog(release.ontology)['C_8dcbead39b715b819261']
definition = '风引起的射程偏差和方向偏差的统称。'
quote = '风引起的射程偏差和方向偏差统称为风偏。'
assert term['label'] == '风偏' and definition in [x['value'] for x in term['definitions']]
source = (release.path / 'ontology.ttl').read_text(encoding='utf-8-sig')
method = json.loads((release.path / 'method_manifest.json').read_text(encoding='utf-8'))
graph = parse_turtle(source, base_iri=method.get('source_base_iri'))
values = list(graph.objects(URIRef(term['iri']), SKOS.definition))
annotation = next(v for v in values if str(v) == definition)
evidence = [e for e in candidate['evidence_items'] if e['quote'] == quote]
bundle = verify_evidence_bundle(make_evidence_bundle(evidence, ['user_definition_reuse']), document, candidate)
assert len(bundle['items']) == 1 and not bundle['rejected_items']
mapping = json.loads((OUT / 'DEV-017_mapping_review.json').read_text(encoding='utf-8'))
assert mapping['status'] == 'needs_human' and any(v.get('decision') == 'reject' for v in mapping['votes'])

if DEST.exists():
    resolution = json.loads(DEST.read_text(encoding='utf-8'))
    assert resolution['user_reply_original'] == '复用已有定义，不生成部件三元组'
    assert sha256_json({k: v for k, v in resolution.items() if k != 'resolution_hash'}) == resolution['resolution_hash']
else:
    definition_binding = {'ontology_release_id': release.manifest['release_id'],
                          'ontology_version': release.manifest['ontology_version'],
                          'profile_hash': release.manifest['profile_hash'],
                          'class_id': term['id'], 'class_iri': term['iri'], 'class_label': term['label'],
                          'predicate_iri': str(SKOS.definition), 'definition': definition,
                          'language': annotation.language, 'datatype': str(annotation.datatype) if annotation.datatype else None}
    resolution = {'sample_id': 'DEV-017', 'recorded_at_beijing': datetime.now(timezone(timedelta(hours=8))).isoformat(),
                  'user_reply_original': '复用已有定义，不生成部件三元组', 'decided_by': 'user',
                  'status': 'resolved_reuse_existing_definition', 'base_candidate_binding': item['base_binding'],
                  'definition_binding': definition_binding, 'definition_binding_hash': sha256_json(definition_binding),
                  'verified_source_bundle': bundle, 'generate_part_triple': False,
                  'generate_new_fact_candidate': False, 'ontology_mutation_required': False,
                  'new_ontology_extension_required': False, 'original_candidate_factual_gold': None,
                  'evaluation_kind': 'ontology_definition_evidence_reuse',
                  'superseded_development_proposal_hash': item['target_content_hash'],
                  'preserved_mapping_draft_id': mapping['draft_id'],
                  'preserved_mapping_proposal_digest': mapping['proposal_digest'],
                  'historical_votes_modified': False, 'production_applied': False,
                  'original_workbook_modified': False,
                  'meaning': '此处保留有效的风偏定义知识及其来源证据，不把部件三元组当成新事实，不将该处理标作原文反驳或无效知识。'}
    resolution['resolution_hash'] = sha256_json(resolution)
    DEST.write_text(json.dumps(resolution, ensure_ascii=False, indent=2), encoding='utf-8')

# The experiment-only table makes the disposition available to subsequent work.
with repo.transaction(immediate=True) as c:
    c.execute('CREATE TABLE IF NOT EXISTS development_case_resolutions(resolution_hash TEXT PRIMARY KEY,sample_id TEXT NOT NULL,candidate_id TEXT NOT NULL,resolution_json TEXT NOT NULL)')
    c.execute('INSERT OR IGNORE INTO development_case_resolutions VALUES(?,?,?,?)',
              (resolution['resolution_hash'], 'DEV-017', candidate['candidate_id'], json.dumps(resolution, ensure_ascii=False, sort_keys=True)))

old = json.loads((ANALYSIS / 'annotations_scoped_v2.json').read_text(encoding='utf-8'))
new = deepcopy(old); scope = new['scope']; scope['previous_scope_hash'] = scope.pop('scope_hash')
scope['item_specific_overrides']['DEV-017'] = {
    'disposition': 'reuse_existing_ontology_definition', 'generate_part_triple': False,
    'resolution_hash': resolution['resolution_hash'], 'evaluation_kind': 'ontology_definition_evidence_reuse'}
scope['recorded_at_beijing'] = resolution['recorded_at_beijing']; scope['scope_hash'] = sha256_json(scope)
for row, before in zip(new['annotations'], old['annotations']):
    row['confirmed_scope_hash'] = scope['scope_hash']
    if row['sample_id'] == 'DEV-017':
        row.update(label_scope_status='user_resolved_by_definition_reuse',
                   post_suggestion_factual_judgment=None, fact_generation_eligible=False,
                   evaluation_kind='ontology_definition_evidence_reuse',
                   excluded_from_new_fact_accuracy=True,
                   ontology_definition_reuse_target=resolution['definition_binding'],
                   definition_reuse_resolution_hash=resolution['resolution_hash'])
        row['post_revision_evaluation_target'] = {
            'status': 'superseded_by_definition_evidence_reuse', 'content_hash': None,
            'previous_proposal_content_hash': item['target_content_hash'], 'human_overall_gold': None}
    assert row['human_labels_original'] == before['human_labels_original']
(ANALYSIS / 'annotations_scoped_v3.json').write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding='utf-8')

assert sha256_json(repo.get_revision(candidate['candidate_id'])) == item['base_binding']['candidate_content_hash']
assert json.loads((OUT / 'DEV-017_mapping_review.json').read_text(encoding='utf-8')) == mapping
pilot.verify_inputs(json.loads((pilot.SOURCE / 'sample_data.json').read_text(encoding='utf-8')))
print(json.dumps({'sample_id': 'DEV-017', 'resolution': resolution['status'],
                  'resolution_hash': resolution['resolution_hash'], 'definition_class_id': term['id'],
                  'verified_evidence_id': bundle['items'][0]['evidence_id'], 'new_fact_generated': False,
                  'ontology_changed': False, 'production_applied': False}, ensure_ascii=False))
