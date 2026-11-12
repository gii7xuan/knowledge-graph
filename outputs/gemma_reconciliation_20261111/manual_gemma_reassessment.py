"""One expressly authorized reassessment, preserving the original unknown attempt.

This output-local exception does not alter global retry policy, request identity,
frozen inputs, or the original request's execution classification.
"""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from review_agent.repository import ReviewRepository, ConflictError, utc_now
from review_agent.contracts import canonical_json
from review_agent.service import load_config
from review_agent.cli import _configured_validators
from review_agent.case_budget import CaseBudget
from ontology_review.validators import judge_independently, response_errors
from ontology_review.contracts import RESPONSE_SCHEMA, vote_binding
from ontology_review.evidence import load_sources
from runs.formal_definitions_20261111 import code_hashes, roster

HERE = Path(__file__).resolve().parent
OUT = ROOT / 'outputs/formal_definitions_20261111_ready'
ATTEMPT = 'attempt_9db64a66e56f4347bca5e8fcae5450bc'
KEY = '460c42a4f1c0bb4977b70a9b85189ab47b0b7645085df511bdcb3db14c143f1b'
ANSWER = '允许一次受控重评'
AUTHORITY = (
    '用户明确授权：保留原Gemma结果未知历史，接受可能重复计费，'
    '对同一冻结提案作一次Gemma受控重评；替代先取得原请求确定结果的前置条件；'
    '整批仍不超过9次调用，不自动重试，不把原请求记为未执行或批准。'
)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def scoped_attempts(repository, task_id):
    with repository.connect() as connection:
        rows = connection.execute('SELECT attempt_id,state,metadata_json FROM request_attempts').fetchall()
    return [dict(row) for row in rows
            if (json.loads(row['metadata_json']).get('proposal') or {}).get('task_id') == task_id]


class AuthorizedOneShotRepository(ReviewRepository):
    def claim_validation_request(self, request_key, owner, *, semantic_key=None):
        # A changed payload, model, credential, generation, or namespace cannot
        # use this exception. No generic retry path is enabled.
        if request_key != KEY:
            raise ConflictError('Manual exception is restricted to the exact original request key')
        authorization = read(HERE / 'manual_reassessment_authorization.json')
        if authorization.get('user_answer') != ANSWER or authorization.get('attempt_id') != ATTEMPT:
            raise ConflictError('Exact user authorization is missing')
        with self.transaction(immediate=True) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS manual_unknown_reassessments('
                               'original_attempt_id TEXT PRIMARY KEY,request_key TEXT NOT NULL,'
                               'authorized_at TEXT NOT NULL,authorization_json TEXT NOT NULL,'
                               'original_cache_json TEXT NOT NULL)')
            if connection.execute('SELECT 1 FROM manual_unknown_reassessments WHERE original_attempt_id=?',
                                  (ATTEMPT,)).fetchone():
                raise ConflictError('The one-shot authorization has already been consumed')
            original = connection.execute('SELECT * FROM request_attempts WHERE attempt_id=? AND request_key=?',
                                          (ATTEMPT, KEY)).fetchone()
            cache = connection.execute('SELECT * FROM validation_requests WHERE request_key=?', (KEY,)).fetchone()
            history = connection.execute('SELECT attempt_id FROM request_attempts WHERE request_key=?', (KEY,)).fetchall()
            if (not original or original['state'] != 'unknown' or not cache or cache['status'] != 'unknown'
                    or len(history) != 1 or history[0][0] != ATTEMPT):
                raise ConflictError('Original state changed; do not reassess without inspecting its actual receipt')
            result = json.loads(original['result_json'])
            if not result.get('outcome_unknown') or result.get('response_status') == 'ok':
                raise ConflictError('A valid or terminal original result cannot be rerolled')
            original_family = connection.execute('SELECT semantic_key FROM validation_request_families WHERE request_key=?',
                                                 (KEY,)).fetchone()
            if not original_family or original_family[0] != semantic_key:
                raise ConflictError('Original semantic request identity changed')
            task_id = json.loads(original['metadata_json'])['proposal']['task_id']
            count = sum((json.loads(row[0]).get('proposal') or {}).get('task_id') == task_id
                        for row in connection.execute('SELECT metadata_json FROM request_attempts'))
            if count != 5:
                raise ConflictError('Expected five previous calls before the single manual reassessment')
            connection.execute('INSERT INTO manual_unknown_reassessments VALUES(?,?,?,?,?)',
                               (ATTEMPT, KEY, utc_now(), canonical_json(authorization), cache['vote_json']))
            changed = connection.execute(
                "UPDATE validation_requests SET owner=?,status='running',updated_at=? "
                "WHERE request_key=? AND owner=? AND status='unknown'",
                (owner, utc_now(), KEY, cache['owner']),
            ).rowcount
            if changed != 1:
                raise ConflictError('Current request changed during manual authorization')
            connection.execute('INSERT INTO request_attempt_events VALUES(?,?,?,?,?)', (
                uuid4().hex, ATTEMPT, 'operator_authorized_one_reassessment', time.time(),
                canonical_json({'reason': AUTHORITY, 'original_execution_outcome': 'still_unknown',
                                'not_executed_claimed': False, 'same_request_key': True,
                                'authorization_file_sha256': file_hash(HERE / 'manual_reassessment_authorization.json')}),
            ))
        return {'status': 'claimed'}


def check_claim_on_copy():
    # Verify the consequential state transition against a disposable DB copy,
    # including consumption-once and exact original-state preservation.
    copy_path = HERE / 'manual_claim_verification.sqlite3'
    if copy_path.exists():
        raise ValueError('Use the saved verification result; do not overwrite its DB')
    with sqlite3.connect((OUT / 'review.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as source:
        with sqlite3.connect(copy_path) as target:
            source.backup(target)
    repository = AuthorizedOneShotRepository(copy_path)
    original = repository.request_attempt(ATTEMPT)
    with repository.connect() as connection:
        family = connection.execute('SELECT semantic_key FROM validation_request_families WHERE request_key=?', (KEY,)).fetchone()[0]
    try:
        repository.claim_validation_request('different-request-key', 'test-owner', semantic_key=family)
    except ConflictError:
        pass
    else:
        raise AssertionError('Changed request identity was accepted')
    assert repository.claim_validation_request(KEY, 'test-owner', semantic_key=family) == {'status': 'claimed'}
    assert repository.request_attempt(ATTEMPT) == original
    assert repository.validation_request(KEY)['status'] == 'running'
    try:
        repository.claim_validation_request(KEY, 'second-owner', semantic_key=family)
    except ConflictError:
        pass
    else:
        raise AssertionError('One-shot authorization could be consumed twice')
    write(HERE / 'manual_claim_verification.json', {
        'at': now(), 'changed_key_rejected': True, 'second_claim_rejected': True,
        'original_attempt_and_unknown_result_unchanged': True,
        'model_calls': 0, 'database': str(copy_path),
    })


def execute():
    if (HERE / 'manual_reassessment_started.json').exists():
        raise ValueError('One launch only; inspect its durable receipt instead of relaunching')
    manifest = read(OUT / 'prepared_manifest.json')
    proposal = read(OUT / 'prepared_proposal.json')
    assert code_hashes() == manifest['code_fingerprints']
    assert file_hash(OUT / 'prepared_proposal.json') == manifest['proposal_sha256']
    assert file_hash(OUT / 'prepared_competency_request.json') == manifest['competency_request_sha256']
    config = load_config()
    voters = _configured_validators(config)
    assert roster(voters) == manifest['roster']
    repository = AuthorizedOneShotRepository(OUT / 'review.sqlite3')
    original = repository.request_attempt(ATTEMPT)
    assert original['state'] == 'unknown'
    before = repository.ontology_audit(manifest['audit_id'])['votes']
    unchanged = {v['voter_id']: deepcopy(v) for v in before if v['voter_id'] != 'judge_b'}
    assert len(unchanged) == 2 and all(v['response_status'] == 'ok' for v in unchanged.values())
    task = repository.extension_status(manifest['extension_id'])
    _, _, candidates, cases = load_sources(repository, task)
    for candidate_id in candidates:
        account = repository.case_status(repository.ensure_case(candidate_id))
        additions = {k: max(0, account['usage'][k] + value - account['limits'][k])
                     for k, value in {'llm_calls': 1, 'tokens': 150000}.items()}
        additions = {k: value for k, value in additions.items() if value}
        if additions:
            repository.authorize_case_budget(candidate_id, additions, reason=AUTHORITY,
                actor='user_authorized_codex_one_shot', authorization_key='manual-reassessment:' + ATTEMPT + ':' + candidate_id)
    budget = CaseBudget({'max_llm_calls_per_candidate': 1, 'max_total_tokens': 150000,
                         'total_timeout_seconds': 210}, repository, cases,
                        task_type='ontology_manual_reassessment', task_id=ATTEMPT)
    assert budget.reserve_calls(1)
    generation = original['metadata']['generation']
    voter = next(v for v in voters if v['id'] == 'judge_b')
    voter = {**voter, 'max_output_tokens': generation['max_output_tokens'],
             'ontology_delivery_version': generation['ontology_delivery_version']}
    assert voter['client'].stream_responses == generation.get('stream_responses', False)
    write(HERE / 'manual_reassessment_started.json', {'at': now(), 'same_request_key': KEY,
        'original_attempt_id': ATTEMPT, 'max_new_calls': 1, 'total_call_limit': 9,
        'original_state_retained': 'unknown', 'authorization': AUTHORITY})
    def store(vote):
        repository.ontology_recovered_vote(vote)
    votes = judge_independently([voter], proposal, repository, retries=0, timeout=180,
                               deadline_epoch=budget.deadline_epoch, on_vote=store)
    for vote in votes:
        if not vote.get('actual_calls'):
            budget.refund_calls(1)
        for attempt in vote.get('attempts') or []:
            budget.tokens(attempt)
    after = repository.ontology_audit(manifest['audit_id'])['votes']
    assert all(next(v for v in after if v['voter_id'] == seat) == value for seat, value in unchanged.items())
    original_after = repository.request_attempt(ATTEMPT)
    assert original_after['state'] == original['state'] == 'unknown'
    assert original_after['result'] == original['result']
    assert len(repository.request_history(KEY)) == 2
    assert len(scoped_attempts(repository, manifest['extension_id'])) == 6
    write(HERE / 'manual_reassessment_result.json', {
        'at': now(), 'votes': votes, 'budget': budget.snapshot(),
        'original_attempt_still_unknown': True, 'original_result_unchanged': True,
        'previous_valid_votes_unchanged': True, 'same_request_key': True,
        'new_model_calls': 1, 'total_calls': 6, 'automatic_retries': 0,
    })
    vote = votes[0]
    if vote.get('response_status') == 'ok':
        assert all(vote.get(k) == value for k, value in vote_binding(voter, proposal).items())
        assert not response_errors({k: vote[k] for k in RESPONSE_SCHEMA['required']}, proposal)
    print(json.dumps({k: vote.get(k) for k in ('response_status', 'decision', 'error_code', 'response_id')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['--verify-claim']:
        check_claim_on_copy()
    elif sys.argv[1:] == ['--execute']:
        execute()
    else:
        raise SystemExit('Use --verify-claim before the single --execute launch')
