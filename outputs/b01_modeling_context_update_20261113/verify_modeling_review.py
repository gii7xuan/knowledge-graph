"""Verify this authorized review's stored outcomes without any external calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
from ontology_review.contracts import proposal_digest, RESPONSE_SCHEMA, vote_binding
from ontology_review.modeling_context import verify
from ontology_review.publisher import current_release_id
from ontology_review.validators import consensus, response_errors
from review_agent.contracts import sha256_json


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = read(OUT / 'prepared_manifest.json')
    sealed = read(OUT / 'dispatch_manifest_20261113.json')
    before = read(OUT / 'review_preflight_20261113.json')
    grant = read(OUT / 'user_authorization_20261113.json')
    summary = read(OUT / 'modeling_summary_20261113.json')
    p = read(OUT / 'prepared_proposal.json')
    votes = read(OUT / 'modeling_votes_20261113.json')
    protected = read(OUT / 'protected_inputs.json')
    assert all(digest(Path(path)) == expected for path, expected in protected.items())
    assert all(digest(ROOT / path) == expected for path, expected in manifest['code_fingerprints'].items())
    assert all(digest(OUT / path) == expected for path, expected in read(OUT / 'delivery_verification.json')['artifact_sha256'].items())
    assert proposal_digest(p) == p['proposal_digest'] == manifest['proposal_digest']
    assert sealed['prepared_manifest_sha256'] == digest(OUT / 'prepared_manifest.json')
    assert sealed['runner_sha256'] == digest(OUT / 'review_context_b01.py')
    assert sealed['preflight_material_hash'] == sha256_json({k: v for k, v in before.items() if k not in {'observed_at_utc', 'status'}})
    assert grant['approved'] and grant['proposal_digest'] == p['proposal_digest']
    assert grant['max_new_calls'] == 3 and grant['cumulative_limit'] == 22
    assert current_release_id(ROOT / 'runs/ontology_releases') == current_release_id(Path(manifest['release_root'])) == p['base_release_id']
    verify(p)
    with sqlite3.connect(Path(manifest['database']).as_uri() + '?mode=ro', uri=True) as db:
        frozen = db.execute('SELECT proposal_json FROM ontology_proposals WHERE audit_id=? AND round_id=?',
                            (p['audit_id'], p['round_id'])).fetchone()[0]
        assert json.loads(frozen) == p
        stored = [json.loads(row[0]) for row in db.execute('SELECT vote_json FROM ontology_votes WHERE audit_id=? AND round_id=?',
                                                         (p['audit_id'], p['round_id']))]
        attempts = []
        for row in db.execute('SELECT attempt_id,state,metadata_json,result_json,started_at FROM request_attempts'):
            metadata = json.loads(row[2])
            if metadata.get('binding', {}).get('audit_id') != p['audit_id']:
                continue
            assert metadata['proposal'] == p
            assert metadata['generation']['max_output_tokens'] <= 8192
            result = json.loads(row[3] or '{}')
            attempts.append({'attempt_id': row[0], 'state': row[1],
                             'counted_calls': 0 if result.get('actual_calls') == 0 else 1,
                             'voter_id': metadata['binding']['voter_id'], 'started_at': row[4]})
        history = db.execute('SELECT previous_vote_json,recovered_vote_json FROM ontology_vote_recoveries WHERE audit_id=?',
                             (p['audit_id'],)).fetchall()
        assert len(history) == 1
        assert json.loads(history[0][0])['response_status'] == 'timeout'
        assert json.loads(history[0][1]) == next(v for v in votes if v['voter_id'] == 'judge_a')
        token_events = db.execute('SELECT COUNT(*) FROM case_cost_events WHERE event_id=?',
                                 ('request_tokens:attempt_98b5301fdec3472d88166e3cec96fcbf',)).fetchone()[0]
        assert token_events == 1
        original_received = db.execute('SELECT response_received_at FROM request_attempts WHERE attempt_id=?',
                                       ('attempt_98b5301fdec3472d88166e3cec96fcbf',)).fetchone()[0]
        assert all(a['started_at'] > original_received for a in attempts if a['voter_id'] != 'judge_a')
    assert {v['voter_id']: v for v in stored} == {v['voter_id']: v for v in votes}
    assert len(attempts) <= 3 and len({a['voter_id'] for a in attempts}) == len(attempts)
    count = sum(a['counted_calls'] for a in attempts)
    assert count == summary['new_model_calls'] and 19 + count == summary['cumulative_model_calls'] <= 22
    assert not summary['published'] and not summary['production_written']
    assert not summary['full_chapter_review_executed'] and not summary['sample_model_review_executed']
    receipts = read(OUT / 'case_budget_authorization_receipts_20261113.json')
    assert sum(json.loads(r['additions_json']).get('tokens', 0) for r in receipts) == grant['case_token_addition_authorized'] == 223465
    assert all(set(json.loads(r['additions_json'])) == {'tokens'} for r in receipts)
    tokens = sum((v.get('input_tokens') or 0) + (v.get('output_tokens') or 0) for v in votes)
    accounts = summary['budget']['case_accounts']
    assert len(accounts) == 1 and accounts[0]['limits']['tokens'] == 900808
    assert accounts[0]['usage']['tokens'] == before['case_accounts_before'][0]['usage']['tokens'] + tokens
    assert accounts[0]['usage']['llm_calls'] == before['case_accounts_before'][0]['usage']['llm_calls'] + count
    assert tokens <= grant['case_token_envelope'] == 300000
    continuation = read(OUT / 'continuation_dispatch_started_20261113.json')
    assert continuation['runner_sha256'] == digest(OUT / 'continue_unsubmitted_seats.py')
    assert continuation['authorization_sha256'] == digest(OUT / 'user_authorization_20261113.json')
    members = {v['id']: v for v in p['committee_members']}
    valid = []
    for vote in votes:
        assert all(vote.get(k) == value for k, value in vote_binding(members[vote['voter_id']], p).items())
        if vote['response_status'] == 'ok':
            assert not response_errors({k: vote[k] for k in RESPONSE_SCHEMA['required']}, p)
            valid.append(vote)
    gate = consensus(votes, p)
    assert gate == summary['consensus_including_all_publication_gates']
    result = {
        'status': 'passed', 'client_date': '2026-11-13',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'proposal_digest': p['proposal_digest'], 'audit_id': p['audit_id'],
        'actual_calls': count, 'cumulative_model_calls': 19 + count,
        'authorized_cumulative_limit': 22, 'valid_bound_votes': len(valid),
        'decisions': {v['voter_id']: v.get('decision') for v in votes},
        'modeling_three_seats_approve': summary['modeling_three_seats_approve'],
        'consensus_including_publication_gates': gate['result'],
        'new_unknown_requests': sum(a['state'] == 'unknown' for a in attempts),
        'case_token_addition_authorized_and_exact': 223465,
        'qwen_original_timeout_history_preserved': True,
        'qwen_same_original_reply_reconciled_without_new_call': True,
        'qwen_reported_tokens_charged_once': True,
        'other_seats_sent_only_after_original_reply_received': True,
        'reported_input_tokens': sum(v.get('input_tokens') or 0 for v in votes),
        'reported_output_tokens': sum(v.get('output_tokens') or 0 for v in votes),
        'previous_reviews_and_protected_inputs_unchanged': True,
        'full_chapter_review_executed': False, 'sample_model_review_executed': False,
        'published': False, 'production_written': False,
        'modeling_votes_sha256': digest(OUT / 'modeling_votes_20261113.json'),
        'completed_database_sha256': digest(Path(manifest['database'])),
        'result_artifact_sha256': {name: digest(OUT / name) for name in (
            'review_result_20261113.md', 'review_findings_20261113.json', 'modeling_summary_20261113.json',
            'zero_call_reconciliation_20261113.json', 'original_late_reply_20261113.json',
            'modeling_votes_at_stop_20261113.json', 'case_budget_authorization_receipts_20261113.json',
            'continuation_dispatch_started_20261113.json')},
    }
    (OUT / 'modeling_delivery_verification_20261113.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
