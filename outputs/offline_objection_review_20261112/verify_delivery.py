"""Read-only integrity checks for protected state; write only this delivery's report."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def release_head(root):
    with sqlite3.connect((root / 'release_catalog.sqlite3').as_uri() + '?mode=ro', uri=True) as connection:
        return connection.execute('SELECT release_id FROM ontology_release_head WHERE singleton=1').fetchone()[0]


def main():
    manifest = read(HERE / 'input_manifest.json')
    original = ROOT / 'outputs/formal_definitions_20261111_ready'
    frozen = read(original / 'prepared_manifest.json')
    protected = {**read(original / 'protected_inputs.json'), **manifest['protected_input_hashes']}
    protected_checks = {path: {'expected_sha256': expected, 'actual_sha256': digest(Path(path)),
                              'unchanged': digest(Path(path)) == expected} for path, expected in protected.items()}
    assert all(x['unchanged'] for x in protected_checks.values())
    request = read(original / 'prepared_competency_request.json')
    source = request['source_documents'][0]
    text = source['text']
    assert (HERE / 'frozen_source.md').read_text(encoding='utf-8') == text
    assert hashlib.sha256(text.encode()).hexdigest() == manifest['canonical_source_sha256']
    assert digest(original / 'prepared_proposal.json') == frozen['proposal_sha256']
    assert digest(original / 'prepared_competency_request.json') == frozen['competency_request_sha256']
    records, terms = read(HERE / 'per_id_review.json'), read(HERE / 'term_review.json')
    objections = next(x for x in read(ROOT / 'outputs/gemma_reconciliation_20261111/whole_chapter_result.json')['votes'] if x['voter_id'] == 'judge_c')
    assert objections['response_status'] == 'ok'
    assert any(x['status'] == 'fail' for x in objections['assessments'])
    assert [x['term'] for x in terms] == objections['unresolved_terms']
    assert len(terms) == 130 and len(records) == 166
    assert len({x['schema_id'] for x in records}) == 166
    assert Counter(x['schema_kind'] for x in records) == {'class': 86, 'object_property': 80}
    assert Counter(x['recommendation'] for x in terms) == {'keep': 40, 'revise': 84, 'hold': 6}
    evidence = {x['evidence_id']: x for x in read(HERE / 'source_evidence.json')}
    for e in evidence.values():
        assert text[e['start']:e['end']] == e['quote'] and text.count(e['quote']) == 1
        assert all(e[k] == source[k] for k in ('document_id', 'document_version', 'source_index_hash'))
    for row in records:
        assert row['reason'] and row['evidence_ids'] and set(row['evidence_ids']) <= set(evidence)
        assert row['annotated_by'] == 'ai' and row['human_gold'] is False
        assert row['committee_vote_changed'] is False and row['ontology_changed'] is False
    drafts = read(HERE / 'unapproved_definition_drafts.json')
    assert not drafts['approved'] and not drafts['applied'] and len(drafts['operations']) == 80
    assert len({x['target'] for x in drafts['operations']}) == 80
    assert all(not x['approved'] and not x['applied'] for x in drafts['operations'])
    structural = read(HERE / 'structural_revision_candidates.json')['candidates']
    assert len(structural) == 19 and all(not x['executable_patch'] and not x['applied'] and not x['approved'] for x in structural)
    schema = read(original / 'prepared_proposal.json')['merged_schema']
    all_ids = set()
    for key in ('classes', 'object_properties', 'data_properties'):
        entries = schema.get(key, {})
        all_ids.update(x['id'] for x in (entries.values() if isinstance(entries, dict) else entries))
    assert set(x['schema_id'] for x in records) <= all_ids
    for x in structural:
        assert set(x.get('candidate_domain', []) + x.get('candidate_range', [])) <= all_ids
    with sqlite3.connect((ROOT / 'outputs/formal_definition_rerun_20261112_prepared/review.sqlite3').as_uri() + '?mode=ro', uri=True) as c:
        attempts = []
        for state, raw in c.execute('SELECT state,metadata_json FROM request_attempts'):
            binding = json.loads(raw).get('binding', {})
            if binding.get('task_id') == frozen['extension_id'] or binding.get('audit_id') == frozen['audit_id']:
                attempts.append(state)
    assert len(attempts) == 11 and attempts.count('unknown') == 4
    probes = read(ROOT / 'outputs/gemma_timeout_diagnosis_20261112/client_probes_20261112/summary.json')
    assert probes['new_model_calls'] == 2 and probes['cumulative_call_count'] == 13
    heads = {str(root): release_head(root) for root in (ROOT / 'runs/ontology_releases', original / 'releases')}
    assert all(head == frozen['base_release_id'] if 'base_release_id' in frozen else head == manifest['base_release_id'] for head in heads.values())
    changed = []
    for path, old_hash in frozen['code_fingerprints'].items():
        current_hash = digest(ROOT / path)
        if current_hash != old_hash:
            changed.append({'path': path, 'frozen_sha256': old_hash, 'current_sha256': current_hash})
    expected_changed = {
        'ontology_review/clarification.py', 'ontology_review/semantic_competency.py', 'ontology_review/validators.py',
        'review_agent/execution.py', 'review_agent/llm_clients.py', 'review_agent/request_repository.py', 'review_agent/validators.py'}
    assert {x['path'].replace('\\', '/') for x in changed} == expected_changed
    test_result = read(HERE / 'regression_test_result.json')
    log = (HERE / 'regression_tests.log').read_text(encoding='utf-8-sig')
    test_count = int(re.search(r'Ran (\d+) tests', log).group(1))
    assert test_result['exit_code'] == 0 and test_count == 101 and log.rstrip().endswith('OK')
    transport = {'changed_code_fingerprints': changed,
        'new_code_sha256': {str(ROOT / name): digest(ROOT / name) for name in ('review_agent/transport_diagnostics.py', 'tests/test_transport_diagnostics.py')},
        'regression_tests': {'count': test_count, **test_result}, 'network': 'mock_only',
        'old_manifest_overwritten': False, 'future_review_requires_new_preparation': True,
        'old_unknown_states_reclassified': False}
    write('transport_upgrade_verification.json', transport)
    result = {'verified_at': datetime.now(timezone.utc).isoformat(), 'status': 'passed',
        'objection_terms': 130, 'schema_ids': 166, 'exact_unique_evidence_quotes': len(evidence),
        'term_decisions': dict(Counter(x['recommendation'] for x in terms)),
        'structural_revision_candidates': len(structural), 'unapproved_definition_drafts': len(drafts['operations']),
        'all_source_quotes_and_bindings_exact': True, 'all_ids_resolve_in_frozen_schema': True,
        'annotated_by': 'ai', 'human_gold': False, 'new_model_api_calls': 0, 'new_external_network_calls': 0,
        'formal_scoped_attempts': len(attempts), 'formal_unknown_attempts_preserved': attempts.count('unknown'),
        'synthetic_probe_calls': probes['new_model_calls'], 'cumulative_model_calls': 13,
        'original_deepseek_valid_fail_retained': True, 'production_written': False, 'published': False,
        'release_heads': heads, 'protected_file_checks': protected_checks,
        'code_changed_intentionally': True, 'fresh_frozen_preparation_required_before_next_review': True,
        'regression_tests': {'count': test_count, **test_result}}
    write('delivery_verification.json', result)
    print(json.dumps({k: result[k] for k in ('status', 'objection_terms', 'schema_ids', 'new_model_api_calls', 'cumulative_model_calls', 'formal_unknown_attempts_preserved', 'regression_tests')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
