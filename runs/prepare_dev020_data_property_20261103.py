"""Prepare an unapproved data-property delta; never publish or bind it as formal."""
from copy import deepcopy
from datetime import datetime
from importlib.util import spec_from_file_location, module_from_spec
import json
from pathlib import Path

from ontologyBuild.release_service import load_release
from ontologyBuild.ontology_adapter import iter_datatype_properties
from ontology_review.delta import apply_delta, check_structure, catalog
from ontology_review.impact import analyze_impact
from ontology_review.tools import remap
from review_agent.contracts import sha256_json
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.repository import ReviewRepository
from evidence.verification import verify_evidence_bundle
from document.hierarchy_index import load_document_index

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/dev30_mapping_source_20261103'
DEST = OUT / 'DEV-020_unapproved_data_property'
DEST.mkdir(parents=True, exist_ok=True)
spec = spec_from_file_location('dev4_pilot', ROOT / 'runs/prepare_dev30_revision_pilot_20261103.py')
pilot = module_from_spec(spec); spec.loader.exec_module(pilot)
pilot.verify_inputs(json.loads((pilot.SOURCE / 'sample_data.json').read_text(encoding='utf-8')))
repo = ReviewRepository(OUT / 'development_review.sqlite3')
frozen = json.loads((ROOT / 'outputs/dev30_revision_pilot_20261103/frozen_targets.json').read_text(encoding='utf-8'))
item = next(x for x in frozen['items'] if x['sample_id'] == 'DEV-020')
candidate = repo.get_revision(item['base_binding']['candidate_id'])
assert candidate['triple']['head_type'] == 'C_85d7c2e6732f8a304f88'
base = load_release(candidate['ontology_release_path']); manifest = repo.get_run(candidate['run_id'])
document = load_document_index(manifest['document_path'], candidate['source_index_hash'], require_verified=True)
bundle = verify_evidence_bundle(item['evidence_bundle'], document, candidate)
assert not bundle['rejected_items'] and not list(iter_datatype_properties(base.ontology))
primary = [e['evidence_id'] for e in bundle['items'] if '其章动角（或攻角）总是很小' in e['quote']]
assert primary
ops = [{'kind': 'add_data_property', 'target': None,
        'changes': {'label': '具有特征', 'label_language': 'zh',
                    'definition': '关联弹丸与其在明确适用条件下的定性特征描述。',
                    'definition_language': 'zh', 'domain': ['C_85d7c2e6732f8a304f88'],
                    'datatype': 'http://www.w3.org/2001/XMLSchema#string'},
        'evidence_ids': primary}]
delta = apply_delta(base, ops, {e['evidence_id'] for e in bundle['items']})
patch = delta['owl_patch']; identity = delta['operations'][0]['target']
schema = delta['merged_schema']; structure = check_structure(schema, schema['application_profile'], base.ontology)
assert structure['status'] == 'pass', structure
assert not patch['removed'] and patch['preserved_triple_count'] == patch['before_triple_count']
probe = deepcopy(candidate); probe['triple']['relation_id'] = identity
old_guard = OntologyGuard(load_snapshot(base.ontology)); new_guard = OntologyGuard(load_snapshot(schema))
after_guard = new_guard.check(probe); assert after_guard['status'] == 'pass', after_guard
readonly = pilot.ReadOnly(ROOT / 'runs/v3/review.sqlite3')
impact = analyze_impact(readonly, base.manifest['release_id'], base.ontology, schema, remap)
with readonly.connect() as c:
    rows = c.execute('SELECT r.candidate_json FROM candidates c JOIN candidate_revisions r ON r.candidate_id=c.candidate_id AND r.revision=c.current_revision').fetchall()
changes = []
semantic_fields = ('status', 'relation_known', 'relation_id', 'domain_status', 'range_status',
                   'type_unknown', 'mapping_ambiguous', 'hard_failure', 'reason_codes', 'constraint_semantics')
for row in rows:
    current = json.loads(row[0])
    if current.get('ontology_release_id') != base.manifest['release_id']: continue
    before_check = old_guard.check(current); after_check = new_guard.check(current)
    if any(before_check.get(k) != after_check.get(k) for k in semantic_fields):
        changes.append({'candidate_id': current['candidate_id'],
                        'declared_relation_id': current['triple'].get('relation_id'),
                        'before': before_check, 'after': after_check})
formal_changes = [x for x in changes if x['declared_relation_id'] in catalog(base.ontology)]
assert not formal_changes, 'existing formal relations must preserve their constraint behavior'
checks = {'created_at': datetime.now().astimezone().isoformat(), 'sample_id': 'DEV-020',
          'base_release_id': base.manifest['release_id'], 'base_candidate_hash': sha256_json(candidate),
          'full_schema_terms_scanned': len(catalog(base.ontology)), 'base_data_property_count': 0,
          'structure': structure, 'impact': impact,
          'all_current_candidate_guard_regression': {'scanned': len(rows), 'existing_formal_mapping_behavior_changes': len(formal_changes),
                                                      'surface_only_lookup_changes': changes,
                                                      'comparison': 'constraint decisions; ontology version metadata intentionally differs'},
          'trigger_probe': {'status': after_guard['status'], 'guard': after_guard,
                            'meaning': 'Only a local probe against an unpublished draft schema; not a formal binding or admission.'},
          'rdf': {k: patch[k] for k in ('before_triple_count', 'after_triple_count', 'preserved_triple_count', 'patch_hash', 'source_sha256')},
          'semantic_competency_review_completed': False, 'independent_ontology_review_completed': False,
          'release_published': False, 'production_applied': False,
          'remaining': ['正式三席本体独立审核', '语义能力问题审核及发布门槛', '发布后按新本体版本重新绑定并复审候选'],
          'literal_language_note': '沿用候选string值和独立language元数据；本草案不改变候选语言或文字值。'}
for name, value in (('delta.json', delta), ('evidence_bundle.json', bundle), ('local_checks.json', checks),
                    ('ontology.json', schema), ('application_profile.json', schema['application_profile'])):
    (DEST / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
(DEST / 'ontology.ttl').write_text(patch['merged_rdf'], encoding='utf-8')
(DEST / 'README.md').write_text(
    '# DEV-020 未批准的数据属性草案\n\n'
    '当前正式本体270个术语中没有数据属性。草案只新增“具有特征”：主体为已有“弹丸”类，值为字符串。'
    '不添加任何全称约束或默认值，稳定飞行条件和“总是”量词继续保留在候选表达中。\n\n'
    f'OWL结构检查及触发候选局部约束探测通过，完整保留原图；已扫描{len(rows)}条当前候选，已有正式映射的约束判定均未改变。'
    f'未指定正式关系ID的表面词检索变化共{len(changes)}条，详见local_checks.json；这不代表重新绑定或自动准入。'
    '这些检查不等于语义正确性、OWL推理一致性或三席批准。本目录不是正式版本，不能用其ID直接准入。\n\n'
    '正式本体审核、语义能力问题审核和发布尚未执行。后续须在通过门槛后发布，再按新版本重新绑定和复审候选。\n',
    encoding='utf-8')
print(json.dumps({'prepared': True, 'new_property_id': identity, 'structure': structure['status'],
                  'trigger_probe': after_guard['status'], 'existing_formal_mapping_behavior_changes': len(formal_changes),
                  'surface_only_lookup_changes': len(changes), 'old_candidates_scanned': len(rows),
                  'rdf': checks['rdf'], 'published': False}, ensure_ascii=False))
