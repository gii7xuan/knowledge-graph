"""Freeze source-bound structural fixtures and one reviewed-next B01 round; no network."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
from ontology_review.contracts import proposal_digest
from ontology_review.delta import catalog
from ontology_review import semantic_competency
from ontology_review.review_payload import validator_payload
from review_agent.contracts import sha256_json
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.shadow_revision import clone

BUNDLE = ROOT / 'outputs/ontology_revision_next_20261112'
SOURCE = ROOT / 'outputs/dev30_v8_full_20261111'


def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def write(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def expectation(status, domain='pass', range_status='pass'):
    return {'status': status, 'domain_status': domain, 'range_status': range_status}


def main():
    if (OUT / 'started.json').exists(): raise ValueError('Already prepared; never overwrite or redispatch this frozen round')
    parent_manifest = read(BUNDLE / 'prepared_manifest.json')
    verification = read(BUNDLE / 'delivery_verification.json')
    assert digest(Path(parent_manifest['database'])) == verification['database_sha256']
    assert all(digest(BUNDLE / p) == h for p, h in verification['artifact_sha256'].items())
    assert all(digest(ROOT / p) == h for p, h in parent_manifest['code_fingerprints'].items())
    protected = {**read(BUNDLE / 'protected_inputs.json'),
        str(BUNDLE / 'prepared_manifest.json'): digest(BUNDLE / 'prepared_manifest.json'),
        str(Path(parent_manifest['database'])): digest(Path(parent_manifest['database']))}
    write(OUT / 'started.json', {'at': datetime.now(timezone.utc).isoformat(), 'authorized_additional_calls': 0,
        'purpose': '8 structural fixtures + 9 impacted frozen samples + 3 controls; then prepare B01 modeling only'})
    write(OUT / 'protected_inputs.json', protected)
    proposal = read(BUNDLE / 'B01/prepared_proposal.json')
    schemas = {'baseline': proposal['base_schema'], 'B01': proposal['merged_schema'],
        'integrated_preview': read(BUNDLE / 'integrated_schema_preview.json')}
    guards = {k: OntologyGuard(load_snapshot(v)) for k, v in schemas.items()}
    strict = {k: OntologyGuard(load_snapshot(v)) for k, v in schemas.items()}
    for guard in strict.values(): guard.require_relation_definition = True
    by_id = catalog(schemas['B01'])
    evidence = {e['evidence_id']: e for e in proposal['evidence']}
    source = read(BUNDLE / 'B01/prepared_competency_request.json')['source_documents'][0]
    for e in evidence.values(): assert source['text'][e['start']:e['end']] == e['quote']
    positive = expectation('pass')
    range_fail = expectation('fail', range_status='fail')
    both_fail = expectation('fail', domain='fail', range_status='fail')
    fixtures = []

    def fixture(case_id, target, polarity, relation, head, head_type, tail, tail_type, before, after, note):
        op = next(o for o in proposal['operations'] if o['kind'] == 'revise_constraint' and o['target'] == target)
        fixtures.append({'case_id': case_id, 'origin': 'ai_authored_structural_fixture', 'human_gold': False,
            'target_revision': target, 'polarity': polarity, 'source_document': {k: source[k] for k in ('document_id', 'document_version', 'source_index_hash')},
            'evidence': [deepcopy(evidence[e]) for e in op['evidence_ids']],
            'candidate': {'triple': {'head': head, 'head_type': head_type, 'surface_relation': by_id[relation]['label'],
                'relation_id': relation, 'tail': tail, 'tail_type': tail_type, 'tail_kind': 'entity'}},
            'expected': {'baseline': before, 'B01': after, 'integrated_preview': after},
            'expectation_scope': 'named domain/range/hierarchy acceptance only; not semantic approval or a human label',
            'semantic_note': note})

    fixture('STR-01-P', 'R_2a66075367666d54e7e7', 'positive', 'R_2a66075367666d54e7e7',
        '边界层附近被带动的空气', 'C_2800f3a353c9063a4ccb', '弹丸动能', 'C_a7877ae0fe8183f8b986', range_fail, positive,
        '来源消耗对象为动能；原文黏性及飞行语境保留在证据中。')
    fixture('STR-01-N', 'R_2a66075367666d54e7e7', 'negative', 'R_2a66075367666d54e7e7',
        '边界层附近的空气', 'C_2800f3a353c9063a4ccb', '弹丸实体', 'C_85d7c2e6732f8a304f88', positive, range_fail,
        '检测把能量对象错置为弹丸实体的旧错误，不把负例写入事实库。')
    fixture('STR-02-P', 'R_e1de72d5c68b765c28b9', 'positive', 'R_e1de72d5c68b765c28b9',
        '分离波', 'C_c89817eb0fc6a2a0b963', '正激波', 'C_426d7ce8c711071adac8', both_fail, positive,
        '分离波由正激波与斜激波组成；本例检验其中正激波这一部件，不声称部件只有一个。')
    fixture('STR-02-N', 'R_e1de72d5c68b765c28b9', 'negative', 'R_e1de72d5c68b765c28b9',
        '正激波', 'C_426d7ce8c711071adac8', '分离波', 'C_c89817eb0fc6a2a0b963', positive, both_fail,
        '原部件关系的反向误读应被拒绝。')
    old_shape = catalog(schemas['baseline'])['R_3c75dd89923bc714630d']['range']
    assert len(old_shape) == 1
    fixture('STR-03-P', 'R_3c75dd89923bc714630d', 'positive', 'R_3c75dd89923bc714630d',
        '波动阻力大小', 'C_52c1a1e08cb0ee536af8', '弹头部形状', 'C_cbcb15d49fba77017748', range_fail, positive,
        '来源主要取决于形状，不将主要取决于改写为唯一决定因素。')
    fixture('STR-03-N', 'R_3c75dd89923bc714630d', 'negative', 'R_3c75dd89923bc714630d',
        '波动阻力大小', 'C_52c1a1e08cb0ee536af8', '弹头部实体', old_shape[0], positive, range_fail,
        '检测对象特征被弹头部实体替代的旧端点问题。')
    fixture('STR-04-P', 'C_88ecbdb51d6e44007aec', 'positive', 'R_fb229b192204df6b55e3',
        '本章相对弹道', 'C_2596ed9929a2eb4634d6', '恒定风条件下明确选取的动参考系', 'C_29309f2a3a84cce01e0e', positive, positive,
        '具体动参考系仍可用于观察相对弹道；删除错误全称父类不否定来源中该系可兼具惯性性质。')
    fixture('STR-04-N', 'C_88ecbdb51d6e44007aec', 'negative', 'R_fb229b192204df6b55e3',
        '本章相对弹道', 'C_2596ed9929a2eb4634d6', '仅标注为惯性参考系的对象', 'C_88ecbdb51d6e44007aec', positive, range_fail,
        '只阻止凭惯性参考系类型自动继承动参考系类型；不推断两个类互斥，也不判此对象在现实中不可能是动系。')
    packets = {s['sample_id']: s for s in read(SOURCE / 'review_packets.json')['samples']}
    impact = read(BUNDLE / 'integrated_impact.json')
    sample_ids = {p['candidate']['candidate_id']: sid for sid, p in packets.items()}
    affected = sorted(sample_ids[x['candidate_id']] for x in impact['affected_candidates'])
    assert affected == ['DEV-002','DEV-004','DEV-007','DEV-013','DEV-014','DEV-015','DEV-024','DEV-025','DEV-028']
    controls = ['DEV-001', 'DEV-019', 'DEV-022']
    selected = []
    for sid in affected + controls:
        p = packets[sid]
        expected = expectation('fail', range_status='fail') if sid == 'DEV-028' else (
            expectation('unknown', domain='unknown', range_status='unknown') if sid == 'DEV-001' else positive)
        selected.append({'case_id': sid, 'origin': 'unchanged_original_frozen_sample', 'human_gold': False,
            'role': 'impacted_sample' if sid in affected else 'unchanged_control',
            'original_binding': p['binding'], 'candidate': p['candidate'], 'evidence': p['evidence_bundle']['items'],
            'original_packet_sha256': sha256_json(p), 'expected': {k: expected for k in schemas},
            'expectation_scope': 'guard behavior only; original semantic objections remain unresolved where applicable',
            'semantic_note': 'DEV-028 continues to fail property range mapping; no ontology widening.' if sid == 'DEV-028' else
                'DEV-019/022 are semantic-objection controls; guard pass cannot approve them.' if sid in {'DEV-019','DEV-022'} else
                'DEV-001 remains unassigned/unknown; a definition patch must not invent a formal mapping.' if sid == 'DEV-001' else
                'Independent semantic effects require a new reviewed isolated run.'})
    cases = fixtures + selected
    assert len(cases) == 20 and len({c['case_id'] for c in cases}) == 20
    write(OUT / 'frozen_regression_cases.json', {'annotated_by': 'ai', 'human_gold': False,
        'case_count': len(cases), 'source_labels_changed': False, 'cases': cases})
    observations = []
    for case in cases:
        for name, guard in guards.items():
            result = guard.check(case['candidate'])
            assert all(result[k] == expected for k, expected in case['expected'][name].items()), (case['case_id'], name, result)
            observations.append({'case_id': case['case_id'], 'schema': name,
                'expected': case['expected'][name], 'observed_guard': result,
                'strict_definition_guard': strict[name].check(case['candidate']), 'expectation_matched': True})
    assert guards['baseline'].is_subclass_or_same('C_88ecbdb51d6e44007aec', 'C_29309f2a3a84cce01e0e') is True
    for name in ('B01','integrated_preview'):
        assert guards[name].is_subclass_or_same('C_88ecbdb51d6e44007aec', 'C_29309f2a3a84cce01e0e') is False
        assert guards[name].is_subclass_or_same('C_88ecbdb51d6e44007aec', 'C_88ecbdb51d6e44007aec') is True
    write(OUT / 'offline_results.json', {'expectations_matched': len(observations), 'checks': len(observations),
        'schemas': {k: sha256_json(v) for k, v in schemas.items()}, 'observations': observations,
        'named_inheritance_absence_is_not_disjointness': True, 'semantic_approval_claimed': False, 'model_calls': 0})
    record = {'status': 'pass', 'scope': 'eight_source_bound_structural_fixtures_named_constraints_only',
        'source_parent_digest': proposal['proposal_digest'], 'case_ids': [c['case_id'] for c in fixtures],
        'cases_sha256': sha256_json(fixtures), 'cases': fixtures,
        'results': [o for o in observations if o['case_id'].startswith('STR-') and o['schema'] == 'B01'],
        'expectations_authored_by': 'ai', 'independent_semantic_approval': False,
        'does_not_replace_source_checks_or_full_chapter_review': True}
    record['record_id'] = 'check_' + sha256_json(['targeted_structural_regression_v1', record])
    write(OUT / 'structural_regression_record.json', record)
    repo = clone(Path(parent_manifest['database']), OUT / 'workspace', allowed_root=ROOT / 'outputs')
    releases = OUT / 'workspace/releases'; releases.mkdir()
    base_id = proposal['base_release_id']
    shutil.copytree(Path(parent_manifest['release_root']) / base_id, releases / base_id)
    with sqlite3.connect((Path(parent_manifest['release_root']) / 'release_catalog.sqlite3').as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(releases / 'release_catalog.sqlite3') as dst: src.backup(dst)
    fresh = deepcopy(proposal)
    fresh['round_id'] = 2
    fresh['check_records']['regression'] = record
    fresh['proposal_digest'] = proposal_digest(fresh)
    repo.ontology_proposal(fresh)
    request, _ = semantic_competency.request_for(repo, releases, fresh)
    write(OUT / 'prepared_b01_proposal.json', fresh)
    write(OUT / 'prepared_b01_modeling_payload.json', validator_payload(fresh, delivery_version='ontology_validator_payload_v3'))
    write(OUT / 'prepared_b01_competency_request.json', request)
    with repo.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM ontology_votes WHERE audit_id=? AND round_id=2', (fresh['audit_id'],)).fetchone()[0] == 0
    sample_to_batch = {sid: next(b['batch_id'] for b in parent_manifest['batches'] if packets[sid]['candidate']['triple'].get('relation_id') in
        {op['target'] for op in read(BUNDLE / b['batch_id'] / 'prepared_proposal.json')['operations']}) for sid in affected}
    plan = {'stages': [
        {'stage': 1, 'status': 'complete_offline', 'fixtures': 8, 'impacted_samples': 9, 'controls': 3, 'checks_matched': len(observations)},
        {'stage': 2, 'status': 'prepared_pending_call_authorization', 'next_step': 'B01 three-seat modeling review only',
         'max_new_calls_requested': 3, 'cumulative_limit_requested': 16, 'no_automatic_retry': True, 'stop_on_unknown': True,
         'not_a_release_approval': True, 'full_chapter_still_required': True},
        {'stage': 3, 'status': 'blocked_until_exact_candidate_ontology_is_reviewed_and_calls_authorized',
         'cases': [c['case_id'] for c in cases], 'warning': 'Nine affected cases belong to ' + ', '.join(sorted(set(sample_to_batch.values()))) + '; B01 review alone cannot authorize the 93-operation integrated preview.',
         'sample_to_batch': sample_to_batch,
         'full_30_after_targeted_validation': True}],
        'model_calls_executed': 0, 'cumulative_calls': 13, 'additional_calls_authorized': 0}
    write(OUT / 'sequence_plan.json', plan)
    grant = {'status': 'not_authorized', 'requested_additional_calls': 3, 'requested_total_limit': 16,
        'scope': 'B01 modeling only; one request per frozen Qwen/Gemma/DeepSeek seat, no retries; stop on unknown, do not publish or run samples',
        'proposal_digest': fresh['proposal_digest'], 'evidence_spans': len(fresh['evidence']), 'operations': 10,
        'destinations': ['https://43.143.151.41:3000/v1', 'https://api.deepseek.com'],
        'materials': ['10 proposed operations', '14 exact frozen source spans', 'complete RDF ontology and exact patch', '8 source-bound structural regression fixtures and observed guard behavior'],
        'old_unknown_requests_preserved': True, 'prior_votes_reused': False}
    write(OUT / 'authorization_request.json', grant)
    manifest = {'at': datetime.now(timezone.utc).isoformat(), 'source_bundle_manifest_sha256': digest(BUNDLE / 'prepared_manifest.json'),
        'database': str(Path(repo.database_path).resolve()), 'release_root': str(releases),
        'proposal_digest': fresh['proposal_digest'], 'audit_id': fresh['audit_id'], 'extension_id': fresh['task_id'], 'round_id': fresh['round_id'],
        'base_release_id': base_id, 'delta_hash': fresh['delta_hash'], 'evidence_hash': fresh['evidence_hash'],
        'case_count': len(cases), 'structural_fixtures': 8, 'guard_checks_matched': len(observations),
        'source_bundle_code_fingerprints': parent_manifest['code_fingerprints'],
        'preparation_script_sha256': digest(Path(__file__)),
        'artifact_hashes': {p.name: digest(p) for p in OUT.iterdir() if p.is_file() and p.name not in {'prepared_manifest.json','started.json','prepare.py'}},
        'new_model_calls': 0, 'cumulative_model_calls': 13, 'published': False, 'production_written': False}
    assert all(digest(Path(path)) == value for path, value in protected.items())
    write(OUT / 'prepared_manifest.json', manifest)
    print(json.dumps({k: manifest[k] for k in ('case_count','structural_fixtures','guard_checks_matched','new_model_calls','proposal_digest')}, ensure_ascii=False), flush=True)


if __name__ == '__main__': main()
