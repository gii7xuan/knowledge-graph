"""Finalize unsubmitted offline packets after code verification; preserve draft snapshot."""
from collections import Counter
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('offline_revision_preparation', OUT / 'prepare.py')
preparation = importlib.util.module_from_spec(spec); spec.loader.exec_module(preparation)
from ontology_review import coverage_inventory, semantic_competency
from ontology_review.contracts import proposal_digest
from ontology_review.delta import catalog
from ontology_review.publisher import current_release_id
from ontology_review.impact import analyze_impact
from ontology_review.tools import remap
from ontologyBuild.release_service import load_release
from ontologyBuild.rdf_graph import parse_turtle
from ontologyBuild.rdf_artifact import graph_hash
from review_agent.repository import ReviewRepository
from review_agent.contracts import sha256_json
from evaluation.review_samples import ReadOnlyRepository

read, write, digest = preparation.read, preparation.write, preparation.digest


def main():
    if (OUT / 'delivery_verification.json').exists(): raise ValueError('Final packet already sealed; do not overwrite')
    manifest = read(OUT / 'prepared_manifest.json')
    initial = OUT / 'draft_snapshot_01'; initial.mkdir()
    shutil.copy2(OUT / 'prepared_manifest.json', initial / 'prepared_manifest.json')
    expected = read(OUT / 'protected_inputs.json')
    assert all(digest(Path(p)) == value for p, value in expected.items())
    repo = ReviewRepository(manifest['database']); root = Path(manifest['release_root'])
    new_ids = {b['extension_id'] for b in manifest['batches']}
    with repo.connect() as c:
        attempts_before = c.execute('SELECT COUNT(*) FROM request_attempts').fetchone()[0]
        for batch in manifest['batches']:
            assert c.execute('SELECT COUNT(*) FROM ontology_votes WHERE audit_id=?', (batch['audit_id'],)).fetchone()[0] == 0
        for row in c.execute('SELECT metadata_json FROM request_attempts'):
            assert json.loads(row[0]).get('binding', {}).get('task_id') not in new_ids
    base = load_release(root / manifest['base_release_id'])
    refreshes = []
    all_targets = {}
    full_source = read(preparation.OLD / 'prepared_competency_request.json')['source_documents']
    for batch in manifest['batches']:
        folder = OUT / batch['batch_id']; saved = initial / batch['batch_id']; saved.mkdir()
        for name in ('prepared_competency_request.json', 'coverage_output_contract.json'):
            shutil.copy2(folder / name, saved / name)
        proposal = read(folder / 'prepared_proposal.json')
        assert proposal_digest(proposal) == proposal['proposal_digest']
        assert digest(folder / 'prepared_proposal.json') == batch['proposal_sha256']
        assert 1 <= len(proposal['operations']) <= 16
        assert all(e['anchor_valid'] and e['anchor_status'] == 'exact' for e in proposal['evidence'])
        checks = preparation.code_hashes()
        request, _ = semantic_competency.request_for(repo, root, proposal)
        assert request['response_contract_version'] == coverage_inventory.VERSION
        assert request['coverage_inventory'] == coverage_inventory.inventory(proposal['merged_schema'])
        assert request['coverage_inventory_hash'] == sha256_json(request['coverage_inventory'])
        assert len(request['coverage_inventory']) == 270
        assert request['source_documents'] == full_source
        assert request['coverage_contract_hash'] == coverage_inventory.contract_hash(semantic_competency.SCHEMA, request)
        assert request['request_hash'] == sha256_json({k: v for k, v in request.items() if k != 'request_hash'})
        old_request_hash = read(folder / 'prepared_competency_request.json')['request_hash']
        write(folder / 'prepared_competency_request.json', request)
        write(folder / 'coverage_output_contract.json', semantic_competency.response_schema(request))
        batch.update(competency_request_hash=request['request_hash'], competency_request_sha256=digest(folder / 'prepared_competency_request.json'),
                     coverage_contract_hash=request['coverage_contract_hash'], definition_gaps_in_this_batch_base=len(coverage_inventory.definition_gaps(request)))
        refreshes.append({'batch_id': batch['batch_id'], 'old_request_hash': old_request_hash,
            'final_request_hash': request['request_hash'], 'previous_request_was_dispatched': False,
            'reason': 'Finalize explicit metadata-gap reporting and bind the exact per-ID output contract before any real call.'})
        # Recheck actual candidate impact after all subscriptions are stable.
        impact = analyze_impact(repo, base.manifest['release_id'], base.ontology, proposal['merged_schema'], remap)
        previous = proposal['check_records']['impact']
        assert all(impact[k] == previous[k] for k in ('changed_schema_ids', 'dependent_schema_ids', 'affected_candidates', 'candidate_rows_scanned'))
        for op in proposal['operations']:
            assert all_targets.get(op['target'], batch['batch_id']) == batch['batch_id']
            all_targets[op['target']] = batch['batch_id']
        patch = proposal['source_patch']
        reconstructed = parse_turtle(patch['base_rdf']) - parse_turtle('\n'.join(patch['removed'])) + parse_turtle('\n'.join(patch['added']))
        assert graph_hash(reconstructed) == patch['graph_hash'] == graph_hash(parse_turtle(patch['merged_rdf']))
    write(OUT / 'pre_dispatch_refinement_history.json', refreshes)
    current_codes = preparation.code_hashes()
    changes = [{'path': p, 'initial_sha256': value, 'final_sha256': current_codes.get(p)} for p, value in manifest['code_fingerprints'].items() if current_codes.get(p) != value]
    assert {x['path'].replace('\\', '/') for x in changes} <= {
        'ontology_review/coverage_inventory.py', 'ontology_review/semantic_competency.py',
        'review_agent/transport_diagnostics.py', 'tests/test_competency_inventory.py',
        'outputs/ontology_revision_next_20261112/prepare.py'}
    manifest.update(code_fingerprints=current_codes, bundle_revision=2,
        finalized_at=datetime.now(timezone.utc).isoformat(), pre_dispatch_code_refinements=changes,
        finalization_script_sha256=digest(Path(__file__)))
    schema = read(OUT / 'integrated_schema_preview.json'); items = catalog(schema)
    assert set(items) == set(catalog(base.ontology)) and len(items) == 270
    after = items['R_fbc54f6800679e25d95d']; before = catalog(base.ontology)['R_fbc54f6800679e25d95d']
    assert after['domain'] == before['domain'] and after['range'] == before['range']
    assert items['R_2a66075367666d54e7e7']['range'] == ['C_a7877ae0fe8183f8b986']
    assert items['R_e1de72d5c68b765c28b9']['domain'] == ['C_c89817eb0fc6a2a0b963']
    assert items['R_e1de72d5c68b765c28b9']['range'] == ['C_426d7ce8c711071adac8']
    assert items['R_3c75dd89923bc714630d']['range'] == ['C_cbcb15d49fba77017748']
    assert not items['C_88ecbdb51d6e44007aec'].get('parents')
    inventory = read(OUT / 'full_coverage_inventory.json')
    assert inventory == coverage_inventory.inventory(schema)
    gaps = coverage_inventory.definition_gaps({'coverage_inventory': inventory})
    assert len(gaps) == 47
    structural = read(OUT / 'structural_disposition.json')
    held = read(OUT / 'held_items.json')
    known_issues = {d['schema_id']: d for d in structural if d['preparation_status'] != 'candidate_prepared'}
    original_holds = {r['schema_id']: r for r in held['original_holds']}
    queue_ids = set(gaps) | set(known_issues) | set(original_holds) | set(held['reuse_holds'])
    queue = []
    for identity in sorted(queue_ids):
        item = items[identity]
        categories = []
        if identity in gaps: categories.append('formal_definition_missing')
        if identity in known_issues: categories.append(known_issues[identity]['preparation_status'])
        if identity in original_holds: categories.append(original_holds[identity]['recommendation'])
        if identity in held['reuse_holds']: categories.append('reuse_and_identity_review')
        queue.append({'schema_id': identity, 'label': item['label'], 'schema_kind': item['schema_kind'],
            'categories': categories, 'current_definition': item.get('description') or None,
            'action': known_issues.get(identity, {}).get('repair') or original_holds.get(identity, {}).get('reason') or
                '核验现有ID指称及原文，补有证据的定义；来源不足则保留缺口，不凭通用名称编造定义。',
            'semantically_approved': False})
    write(OUT / 'remaining_work_queue.json', queue)
    write(OUT / 'remaining_definition_gaps.json', {'count': len(gaps), 'by_kind': dict(Counter(items[i]['schema_kind'] for i in gaps)),
        'schema_ids': gaps, 'formal_definition_coverage_complete': False,
        'note': '元数据缺口单独报告，不自动当作无原文支持；整章通过还必须逐ID核验声明语义和结构。'})
    test_result = read(OUT / 'regression_test_result.json')
    log = (OUT / 'regression_tests.log').read_text(encoding='utf-8-sig')
    match = re.search(r'Ran (\d+) tests', log)
    assert match and test_result['exit_code'] == 0 and log.rstrip().endswith('OK')
    tests = {'count': int(match.group(1)), **test_result}
    manifest.update(regression_tests=tests, remaining_work_queue_count=len(queue))
    write(OUT / 'prepared_manifest.json', manifest)
    assert current_release_id(root) == current_release_id(ROOT / 'runs/ontology_releases') == manifest['base_release_id']
    with repo.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM request_attempts').fetchone()[0] == attempts_before
        for identity in new_ids:
            task = repo.extension_status(identity)
            assert not task.get('ontology_review_executed') and task['status'] != 'published'
    assert all(digest(Path(p)) == value for p, value in expected.items())
    artifact_paths = [p for p in OUT.rglob('*') if p.is_file() and not p.is_relative_to(OUT / 'workspace')
        and not p.is_relative_to(initial) and p.name not in {'delivery_verification.json', 'regression_tests_before_fixes.log'}]
    result = {'status': 'passed', 'at': datetime.now(timezone.utc).isoformat(), 'bundle_revision': 2,
        'batch_count': len(manifest['batches']), 'all_batches_at_most_16_operations': True,
        'all_evidence_exactly_verified': True, 'lossless_rdf_patches_verified': True,
        'impact_bindings_current_after_all_subscriptions': True, 'all_270_ids_in_every_new_competency_request': True,
        'old_v1_requests_use_original_schema_and_prompt': True, 'new_v2_output_contract_hash_bound': True,
        'definition_gaps_separate_from_source_absence': True, 'formal_definition_gaps': len(gaps),
        'remaining_work_queue_ids': len(queue), 'new_model_api_calls': 0, 'cumulative_model_calls': 13,
        'old_unknown_requests_unchanged': True, 'old_deepseek_fail_unchanged': True,
        'production_written': False, 'published': False, 'protected_inputs_unchanged': True,
        'regression_tests': tests, 'code_fingerprints_sha256': sha256_json(current_codes),
        'database_sha256': digest(Path(repo.database_path)),
        'artifact_sha256': {str(p.relative_to(OUT)): digest(p) for p in artifact_paths}}
    write(OUT / 'delivery_verification.json', result)
    print(json.dumps({k: result[k] for k in ('status', 'batch_count', 'formal_definition_gaps', 'remaining_work_queue_ids', 'new_model_api_calls', 'regression_tests')}, ensure_ascii=False))


if __name__ == '__main__': main()
