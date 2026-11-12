"""Read-only receipt reconciliation and exact whole-chapter input verification.

No network calls, request retries, vote promotion, or release publication.
"""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evaluation.review_samples import ReadOnlyRepository
from ontology_review.contracts import proposal_digest
from ontology_review.publisher import current_release_id
from ontology_review.semantic_competency import request_for
from ontology_review.validators import response_errors
from ontology_review.contracts import RESPONSE_SCHEMA, vote_binding
from ontologyBuild.rdf_graph import parse_turtle
from ontologyBuild.rdf_artifact import graph_hash
from runs.formal_definitions_20261111 import code_hashes

OUT = ROOT / 'outputs/formal_definitions_20261111_ready'
HERE = Path(__file__).resolve().parent
ATTEMPT = 'attempt_9db64a66e56f4347bca5e8fcae5450bc'
ZONE = timezone(timedelta(hours=8))


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_time(value):
    return datetime.fromtimestamp(value, ZONE).isoformat() if value is not None else None


class ReceiptRepository(ReadOnlyRepository):
    def ensure_case(self, candidate_id, limits=None):
        # request_for/load_sources normally ensures an existing budget case.
        # This verifier must never create or update one.
        with self.connect() as connection:
            row = connection.execute(
                'SELECT case_id FROM review_cases WHERE candidate_id=?',
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise ValueError('Frozen source candidate has no existing review case')
        return row[0]


def main():
    manifest = read(OUT / 'prepared_manifest.json')
    proposal = read(OUT / 'prepared_proposal.json')
    database = OUT / 'review.sqlite3'
    db_before = digest(database)
    repository = ReceiptRepository(database)
    assert digest(OUT / 'prepared_proposal.json') == manifest['proposal_sha256']
    assert proposal_digest(proposal) == proposal['proposal_digest'] == manifest['proposal_digest']
    assert digest(OUT / 'prepared_competency_request.json') == manifest['competency_request_sha256']
    current_code = code_hashes()
    code_changes = sorted(k for k in set(current_code) | set(manifest['code_fingerprints'])
                          if current_code.get(k) != manifest['code_fingerprints'].get(k))
    assert not code_changes, code_changes
    request, cases = request_for(repository, OUT / 'releases', proposal)
    assert request == read(OUT / 'prepared_competency_request.json')
    patch = request['source_patch']
    base_graph = parse_turtle(patch['base_rdf'])
    added = parse_turtle('\n'.join(patch['added']), preserve_bnode_labels=True)
    removed = parse_turtle('\n'.join(patch['removed']), preserve_bnode_labels=True)
    merged = base_graph - removed + added
    assert graph_hash(base_graph) == patch['base_graph_hash']
    assert graph_hash(merged) == patch['graph_hash']
    assert len(merged) == patch['merged_rdf']['verified_triple_count']
    assert request['committee_policy'] == {
        'committee_size': 3, 'required_votes': 3, 'minimum_distinct_families': 3,
    }
    protected = read(OUT / 'protected_inputs.json')
    assert all(digest(Path(path)) == expected for path, expected in protected.items())
    assert current_release_id(ROOT / 'runs/ontology_releases') == manifest['production_release_head']
    assert current_release_id(OUT / 'releases') == manifest['production_release_head']
    with repository.connect() as connection:
        row = connection.execute('SELECT * FROM request_attempts WHERE attempt_id=?', (ATTEMPT,)).fetchone()
        assert row is not None
        record = dict(row)
        original = json.loads(record['result_json'] or '{}')
        metadata = json.loads(record['metadata_json'])
        events = [dict(event) for event in connection.execute(
            'SELECT event,at FROM request_attempt_events WHERE attempt_id=? ORDER BY at', (ATTEMPT,),
        )]
        history = [dict(attempt) for attempt in connection.execute(
            'SELECT attempt_id,state,started_at,response_received_at FROM request_attempts WHERE request_key=? ORDER BY started_at',
            (record['request_key'],),
        )]
        attempts = []
        for attempt in connection.execute('SELECT attempt_id,state,metadata_json FROM request_attempts'):
            meta = json.loads(attempt['metadata_json'])
            frozen = meta.get('proposal') or meta.get('request') or {}
            if frozen.get('task_id') == manifest['extension_id']:
                attempts.append({'attempt_id': attempt['attempt_id'], 'state': attempt['state']})
        has_semantic = bool(connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ontology_semantic_competency'",
        ).fetchone())
        semantic_rows = connection.execute(
            'SELECT COUNT(*) FROM ontology_semantic_competency WHERE parent_digest=?',
            (proposal['proposal_digest'],),
        ).fetchone()[0] if has_semantic else 0
    gemma = next(v for v in proposal['committee_members'] if v['id'] == 'judge_b')
    assert all(original.get(k) == value for k, value in vote_binding(gemma, proposal).items())
    assert metadata['binding']['voter_id'] == 'judge_b'
    audit = repository.ontology_audit(manifest['audit_id'])
    seats = []
    for member in proposal['committee_members']:
        vote = next(v for v in audit['votes'] if v['round_id'] == proposal['round_id'] and v['voter_id'] == member['id'])
        valid = vote.get('response_status') == 'ok'
        if valid:
            assert all(vote.get(k) == value for k, value in vote_binding(member, proposal).items())
            assert not response_errors({k: vote[k] for k in RESPONSE_SCHEMA['required']}, proposal)
        seats.append({'voter_id': member['id'], 'model_id': member['model_id'],
                      'response_status': vote.get('response_status'), 'decision': vote.get('decision'),
                      'valid_contract_and_binding': valid, 'outcome_unknown': bool(vote.get('outcome_unknown'))})
    assert digest(database) == db_before
    authorization_path = HERE / 'continuation_authorization.json'
    authorization = read(authorization_path) if authorization_path.exists() else None
    result = {
        'checked_at': datetime.now(ZONE).isoformat(),
        'scope': 'local durable receipts and exact frozen inputs; provider reconciliation is still required',
        'attempt_id': ATTEMPT, 'request_key': record['request_key'],
        'request_key_is_provider_request_id': False,
        'started_at': local_time(record['started_at']),
        'local_deadline_at': local_time(record['deadline_at']),
        'response_received_at': local_time(record['response_received_at']),
        'state': record['state'], 'response_status': original.get('response_status'),
        'http_status': original.get('http_status'), 'error_code': original.get('error_code'),
        'provider_request_id': record['provider_request_id'],
        'response_id': original.get('response_id'),
        'has_original_model_body': bool(original.get('raw_response')),
        'events': events, 'attempt_history': history,
        'original_modeling_request_count': len(attempts),
        'current_modeling_seats': seats,
        'provider_reconciliation_complete': False,
        'provider_log_access': read(HERE / 'own_log_query_result.json')['response'],
        'browser_log_access': 'Chrome app access approval timed out; no browser session read',
        'unknown_request_resent': False, 'new_model_calls': 0,
        'whole_chapter': {
            'frozen_request_identical': True, 'request_hash': request['request_hash'],
            'file_sha256': manifest['competency_request_sha256'],
            'questions': request['questions'], 'source_document_chars': sum(len(d['text']) for d in request['source_documents']),
            'source_documents': [{k: d[k] for k in ('document_id', 'document_version', 'source_index_hash')} for d in request['source_documents']],
            'base_rdf_chars': len(request['source_patch']['base_rdf']),
            'merged_rdf_delivery': patch['merged_rdf']['encoding'],
            'base_rdf_triples': len(base_graph), 'merged_rdf_triples': len(merged),
            'merged_graph_hash_verified': True,
            'rdf_added': len(request['source_patch']['added']), 'rdf_removed': len(request['source_patch']['removed']),
            'verified_context_spans': len(request.get('verified_context_spans', [])),
            'committee': manifest['roster'], 'required_calls': 3,
            'executed_for_this_parent': bool(semantic_rows),
            'blocked_by_parent_unknown_request': any(v['outcome_unknown'] for v in seats),
        },
        'prior_total_call_limit': 6, 'prior_calls_used': len(attempts),
        'current_authorized_total_call_limit': authorization['authorized_total_call_limit'] if authorization else 6,
        'remaining_authorized_calls': (authorization['authorized_total_call_limit'] if authorization else 6) - len(attempts),
        'minimum_total_if_original_gemma_approved_and_three_cq_calls': len(attempts) + 3,
        'code_and_config_unchanged': True, 'protected_inputs_unchanged': True,
        'review_database_unchanged': True, 'production_head_unchanged': True,
        'isolated_head_unchanged': True, 'published': False,
    }
    (HERE / 'local_reconciliation_and_cq_preflight.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8',
    )
    print(json.dumps({k: result[k] for k in ('state', 'provider_reconciliation_complete', 'new_model_calls', 'published')}, ensure_ascii=False))
    print(json.dumps(result['whole_chapter'], ensure_ascii=False))


if __name__ == '__main__':
    main()
