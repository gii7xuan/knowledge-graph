"""Two authorized Gemma calls in a new copy; keep all prior valid judgments."""
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
HERE = Path(__file__).resolve().parent
SOURCE = ROOT / 'outputs/formal_definitions_20261111_ready'
sys.path.insert(0, str(ROOT))
from review_agent.repository import ReviewRepository, ConflictError, utc_now
from review_agent.contracts import canonical_json, sha256_json
from review_agent.cli import _configured_validators
from review_agent.service import load_config
from review_agent.case_budget import CaseBudget
from review_agent.request_execution import judge_typed
from ontology_review import semantic_competency as cq
from ontology_review.validators import judge as modeling_judge, response_errors, consensus
from ontology_review.contracts import RESPONSE_SCHEMA, vote_binding
from runs.formal_definitions_20261111 import code_hashes, roster

MODEL_KEY = '460c42a4f1c0bb4977b70a9b85189ab47b0b7645085df511bdcb3db14c143f1b'
ANSWER = '定向复跑2次：Gemma建模重评＋整章引用修复，累计上限11次（推荐）'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def task_attempts(repository, task_id):
    with repository.connect() as connection:
        rows = connection.execute('SELECT * FROM request_attempts ORDER BY started_at').fetchall()
    return [dict(row) for row in rows
            if (json.loads(row['metadata_json']).get('proposal') or {}).get('task_id') == task_id]


def cq_key():
    return read(HERE / 'baseline_state.json')['gemma_competency_request_key']


class TwoCallRepository(ReviewRepository):
    def claim_validation_request(self, request_key, owner, *, semantic_key=None):
        authorization = read(HERE / 'authorization.json')
        if authorization.get('user_answer') != ANSWER or authorization.get('max_new_calls') != 2:
            raise ConflictError('Exact targeted rerun authorization required')
        if request_key not in {MODEL_KEY, cq_key()}:
            raise ConflictError('Only the two original Gemma request identities are authorized')
        model = request_key == MODEL_KEY
        with self.transaction(immediate=True) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS targeted_rerun_dispatches('
                               'request_key TEXT PRIMARY KEY,authorization_json TEXT NOT NULL,'
                               'original_cache_json TEXT NOT NULL,at TEXT NOT NULL)')
            if connection.execute('SELECT 1 FROM targeted_rerun_dispatches WHERE request_key=?', (request_key,)).fetchone():
                raise ConflictError('This request has already consumed its single rerun authorization')
            cache = connection.execute('SELECT * FROM validation_requests WHERE request_key=?', (request_key,)).fetchone()
            history = connection.execute('SELECT * FROM request_attempts WHERE request_key=? ORDER BY started_at',
                                         (request_key,)).fetchall()
            expected = 'unknown' if model else 'failed'
            if not cache or cache['status'] != expected or len(history) != (2 if model else 1):
                raise ConflictError('Original receipt state changed; inspect it instead of dispatching')
            if model:
                if any(row['state'] != 'unknown' or not json.loads(row['result_json']).get('outcome_unknown') for row in history):
                    raise ConflictError('Unknown modeling exception cannot reroll a terminal judgment')
            elif history[0]['state'] != 'failed' or json.loads(history[0]['result_json'])['response_status'] != 'invalid_contract':
                raise ConflictError('Only the known Gemma quotation error may be repaired')
            family = connection.execute('SELECT semantic_key FROM validation_request_families WHERE request_key=?',
                                        (request_key,)).fetchone()
            if not family or family[0] != semantic_key:
                raise ConflictError('Frozen model, source, contract, or semantic identity changed')
            connection.execute('INSERT INTO targeted_rerun_dispatches VALUES(?,?,?,?)',
                               (request_key, canonical_json(authorization), cache['vote_json'], utc_now()))
            changed = connection.execute("UPDATE validation_requests SET status='running',owner=?,updated_at=? "
                                         "WHERE request_key=? AND owner=? AND status=?",
                                         (owner, utc_now(), request_key, cache['owner'], expected)).rowcount
            if changed != 1:
                raise ConflictError('Request ownership changed during authorization')
            event = 'operator_authorized_targeted_reassessment' if model else 'operator_authorized_quotation_repair'
            for row in history:
                connection.execute('INSERT INTO request_attempt_events VALUES(?,?,?,?,?)', (
                    uuid4().hex, row['attempt_id'], event, time.time(), canonical_json({
                        'authorization_sha256': digest(HERE / 'authorization.json'),
                        'previous_attempt_state_preserved': row['state'],
                        'not_executed_claimed': False, 'new_call_maximum': 1}),
                ))
        return {'status': 'claimed'}

    def begin_request_attempt(self, key, owner, timeout, metadata, recovery_reason=None):
        metadata = deepcopy(metadata)
        if key == cq_key():
            if metadata['task_type'] != 'ontology_competency_review':
                raise ConflictError('Wrong task for quotation repair')
            # Supply only this seat's own invalid output and verified error.
            # The full source and unchanged competency question remain present.
            metadata['feedback'] = {**(metadata.get('feedback') or {}),
                                    **read(HERE / 'gemma_competency_repair_feedback.json')}
            request = read(HERE / 'frozen_competency_request.json')
            document = request['source_documents'][0]
            previous_quote = metadata['feedback']['invalid_quotation_references'][0]['previous_quote']
            exact_quote = previous_quote.replace('\\mathrm {s}', '\\mathrm{s}')
            assert document['text'].count(exact_quote) == 1
            start = document['text'].index(exact_quote)
            metadata['feedback']['verified_source_quote_candidates'] = [{
                'document_id': document['document_id'], 'document_version': document['document_version'],
                'start': start, 'end': start + len(exact_quote), 'quote': exact_quote}]
            metadata['feedback']['candidate_scope'] = ('Exact excerpts from the already supplied frozen source for '
                'quotation repair only. They do not prove complete coverage or authorize changing a substantive judgment.')
        return super().begin_request_attempt(key, owner, timeout, metadata,
            recovery_reason=recovery_reason or 'user_authorized_targeted_rerun_20261112')


def prepare_database():
    destination = HERE / 'review.sqlite3'
    if destination.exists():
        raise ValueError('Rerun DB already exists; do not overwrite it')
    manifest = read(SOURCE / 'prepared_manifest.json')
    assert code_hashes() == manifest['code_fingerprints']
    for name, value in read(HERE / 'prepared_rerun_plan.json')['frozen_files'].items():
        assert digest(HERE / name) == value
    source_hash = digest(SOURCE / 'review.sqlite3')
    with sqlite3.connect((SOURCE / 'review.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as original:
        with sqlite3.connect(destination) as target:
            original.backup(target)
    repository = ReviewRepository(destination)
    assert len(task_attempts(repository, manifest['extension_id'])) == 9
    with repository.connect() as connection:
        row = connection.execute('SELECT request_hash,request_json,result_json FROM ontology_semantic_competency '
                                 'WHERE parent_digest=?', (manifest['proposal_digest'],)).fetchone()
    result = json.loads(row['result_json'])
    assert json.loads(row['request_json']) == read(HERE / 'frozen_competency_request.json')
    gemma = next(v for v in result['votes'] if v['voter_id'] == 'judge_b')
    baseline = {'at': now(), 'source_database': str(SOURCE / 'review.sqlite3'),
        'source_database_sha256': source_hash, 'manifest': manifest,
        'modeling_votes': repository.ontology_audit(manifest['audit_id'])['votes'],
        'competency_result': result, 'gemma_competency_request_key': gemma['request_key'],
        'original_modeling_attempts': [{k: a[k] for k in ('attempt_id', 'state', 'result_json')}
                                      for a in repository.request_history(MODEL_KEY)]}
    write(HERE / 'baseline_state.json', baseline)
    assert digest(SOURCE / 'review.sqlite3') == source_hash


def verify_claims_on_copy():
    verification = HERE / 'claim_verification.sqlite3'
    assert not verification.exists()
    with sqlite3.connect((HERE / 'review.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as original:
        with sqlite3.connect(verification) as target:
            original.backup(target)
    repository = TwoCallRepository(verification)
    for key in (MODEL_KEY, cq_key()):
        before = repository.request_history(key)
        with repository.connect() as connection:
            family = connection.execute('SELECT semantic_key FROM validation_request_families WHERE request_key=?', (key,)).fetchone()[0]
        assert repository.claim_validation_request(key, 'fixture-owner', semantic_key=family) == {'status': 'claimed'}
        assert repository.request_history(key) == before
        try:
            repository.claim_validation_request(key, 'second-owner', semantic_key=family)
        except ConflictError:
            pass
        else:
            raise AssertionError('Authorization consumed twice')
    try:
        repository.claim_validation_request('unapproved-key', 'fixture-owner', semantic_key='none')
    except ConflictError:
        pass
    else:
        raise AssertionError('Changed identity accepted')
    write(HERE / 'claim_verification.json', {'at': now(), 'real_model_calls': 0,
        'same_request_identities_only': True, 'one_dispatch_per_key': True,
        'unknown_and_failed_prior_attempts_preserved': True})


def execute():
    if (HERE / 'execution_started.json').exists():
        raise ValueError('This two-call rerun has already started; inspect durable receipts, do not relaunch')
    baseline = read(HERE / 'baseline_state.json')
    manifest = baseline['manifest']
    assert digest(SOURCE / 'review.sqlite3') == baseline['source_database_sha256']
    assert code_hashes() == manifest['code_fingerprints']
    assert read(HERE / 'claim_verification.json')['real_model_calls'] == 0
    for name, value in read(HERE / 'prepared_rerun_plan.json')['frozen_files'].items():
        assert digest(HERE / name) == value
    proposal = read(HERE / 'frozen_modeling_proposal.json')
    request = read(HERE / 'frozen_competency_request.json')
    repository = TwoCallRepository(HERE / 'review.sqlite3')
    assert len(task_attempts(repository, proposal['task_id'])) == 9
    rebuilt, cases = cq.request_for(repository, SOURCE / 'releases', proposal)
    assert rebuilt == request
    voters = _configured_validators(load_config())
    assert roster(voters) == manifest['roster']
    voter = next(v for v in voters if v['id'] == 'judge_b')
    original_generation = repository.request_history(MODEL_KEY)[-1]['metadata']['generation']
    modeling_voter = {**voter, 'max_output_tokens': original_generation['max_output_tokens'],
                      'ontology_delivery_version': original_generation['ontology_delivery_version']}
    assert modeling_voter['client'].stream_responses == original_generation.get('stream_responses', False)
    reason = '用户选择定向复跑2次，累计上限11次；保留两条Gemma未知历史与Qwen/DeepSeek有效票，不自动重试，不发布。'
    for case in cases:
        account = repository.case_status(case)
        additions = {key: max(0, account['usage'][key] + value - account['limits'][key])
                     for key, value in {'llm_calls': 2, 'tokens': 300000}.items()}
        additions = {key: value for key, value in additions.items() if value}
        if additions:
            with repository.connect() as connection:
                candidate_id = connection.execute('SELECT candidate_id FROM review_cases WHERE case_id=?', (case,)).fetchone()[0]
            repository.authorize_case_budget(candidate_id, additions, reason=reason,
                actor='user_authorized_targeted_rerun', authorization_key='targeted-rerun-20261112:' + case)
    budget = CaseBudget({'max_llm_calls_per_candidate': 2, 'max_total_tokens': 300000,
                         'total_timeout_seconds': 420}, repository, cases,
                        task_type='targeted_rerun', task_id=proposal['task_id'])
    initial_ids = {row['attempt_id'] for row in task_attempts(repository, proposal['task_id'])}
    write(HERE / 'execution_started.json', {'at': now(), 'max_new_calls': 2, 'max_total_calls': 11,
        'profile': 'targeted', 'source_db_unchanged': True, 'publication_enabled': False})
    assert budget.reserve_calls(1)
    print(json.dumps({'phase': 'gemma_modeling_reassessment', 'max_calls': 1}), flush=True)
    model_vote = modeling_judge(modeling_voter, proposal, repository, retries=0,
                               timeout=180, deadline_epoch=budget.deadline_epoch)
    if not model_vote.get('actual_calls'):
        budget.refund_calls(1)
    for attempt in model_vote.get('attempts') or []:
        budget.tokens(attempt)
    if model_vote.get('actual_calls'):
        repository.ontology_recovered_vote(model_vote)
    write(HERE / 'modeling_rerun_result.json', model_vote)
    print(json.dumps({'phase': 'modeling_returned', 'status': model_vote['response_status'],
                      'decision': model_vote.get('decision'), 'error_code': model_vote.get('error_code')}), flush=True)
    assert budget.reserve_calls(1)
    print(json.dumps({'phase': 'gemma_competency_quotation_repair', 'max_calls': 1}), flush=True)
    repaired = judge_typed(voter, request, repository, task_type='ontology_competency_review',
        binding=cq.binding(voter, request), invoke=lambda feedback: cq.judge_once(voter, request, feedback),
        validate_cached=lambda value: cq.errors({k: value.get(k) for k in cq.SCHEMA['required']}, request),
        prompt_hash=sha256_json([cq.PROMPT, cq.SCHEMA]), retries=0,
        timeout=budget.remaining_seconds(), deadline_epoch=budget.deadline_epoch)
    if not repaired.get('actual_calls'):
        budget.refund_calls(1)
    for attempt in repaired.get('attempts') or []:
        budget.tokens(attempt)
    write(HERE / 'competency_repair_result.json', repaired)
    previous = deepcopy(baseline['competency_result'])
    updated = deepcopy(previous)
    if repaired.get('actual_calls'):
        updated['votes'] = [repaired if v['voter_id'] == 'judge_b' else v for v in updated['votes']]
    updated['assessment'] = cq.aggregate(updated['votes'], request)
    updated['targeted_repair'] = {'reason': reason, 'original_invalid_response_preserved_in': 'baseline_state.json'}
    with repository.transaction(immediate=True) as connection:
        old = connection.execute('SELECT request_hash FROM ontology_semantic_competency WHERE parent_digest=?',
                                 (proposal['proposal_digest'],)).fetchone()
        assert old[0] == request['request_hash']
        connection.execute('UPDATE ontology_semantic_competency SET result_json=? WHERE parent_digest=? AND request_hash=?',
                           (canonical_json(updated), proposal['proposal_digest'], request['request_hash']))
    effective_modeling = repository.ontology_audit(manifest['audit_id'])['votes']
    for seat in ('judge_a', 'judge_c'):
        assert next(v for v in effective_modeling if v['voter_id'] == seat) == next(v for v in baseline['modeling_votes'] if v['voter_id'] == seat)
        assert next(v for v in updated['votes'] if v['voter_id'] == seat) == next(v for v in previous['votes'] if v['voter_id'] == seat)
    for original in baseline['original_modeling_attempts']:
        current = repository.request_attempt(original['attempt_id'])
        assert current['state'] == original['state'] == 'unknown'
        assert current['result_json'] == original['result_json']
    if model_vote['response_status'] == 'ok':
        assert all(model_vote.get(k) == value for k, value in vote_binding(modeling_voter, proposal).items())
        assert not response_errors({k: model_vote[k] for k in RESPONSE_SCHEMA['required']}, proposal)
    if repaired['response_status'] == 'ok':
        assert not cq.errors({k: repaired[k] for k in cq.SCHEMA['required']}, request)
    verified = cq.verified_result(repository, SOURCE / 'releases', proposal)
    final = consensus(effective_modeling, proposal, semantic_competency=verified)
    task = repository.extension_status(proposal['task_id'])
    repository.finish_ontology_audit(proposal['audit_id'], {
        'extension_id': proposal['task_id'], 'recommendation': final['result'], 'task_status': 'needs_human',
        'selected_round_id': proposal['round_id'], 'selected_proposal_digest': proposal['proposal_digest'],
        'consensus': final, 'semantic_competency': verified,
        'stop_reason': 'two_authorized_targeted_calls_completed_no_publication',
    }, task['row_version'])
    attempts = task_attempts(repository, proposal['task_id'])
    new = [row for row in attempts if row['attempt_id'] not in initial_ids]
    assert len(new) <= 2 and len(attempts) <= 11
    receipts = []
    for row in new:
        metadata = json.loads(row['metadata_json'])
        assert metadata['binding']['voter_id'] == 'judge_b'
        receipts.append({**{k: row[k] for k in ('attempt_id', 'request_key', 'state', 'started_at',
            'response_received_at', 'provider_request_id', 'recovery_of')},
            'task_type': metadata['task_type'], 'feedback': metadata.get('feedback'),
            'actual_receipt': json.loads(row['result_json'] or '{}')})
    assert digest(SOURCE / 'review.sqlite3') == baseline['source_database_sha256']
    assert all(digest(Path(path)) == value for path, value in read(SOURCE / 'protected_inputs.json').items())
    assert code_hashes() == manifest['code_fingerprints']
    summary = {'at': now(), 'profile': 'targeted', 'new_model_calls': len(new), 'total_model_calls': len(attempts),
        'authorized_total_limit': 11, 'modeling_result': {k: model_vote.get(k) for k in ('response_status', 'decision', 'error_code', 'outcome_unknown')},
        'competency_repair_result': {k: repaired.get(k) for k in ('response_status', 'coverage', 'error_code', 'outcome_unknown')},
        'competency_repair_assessments': repaired.get('assessments'),
        'competency_aggregate': updated['assessment'],
        'valid_modeling_approvals': sum(v.get('response_status') == 'ok' and v.get('decision') == 'approve' for v in effective_modeling),
        'final_consensus': final, 'prior_valid_votes_unchanged': True, 'deepseek_fail_preserved': True,
        'two_prior_unknown_attempts_preserved': True, 'source_database_unchanged': True,
        'protected_inputs_unchanged': True, 'source_proposal_and_question_and_code_unchanged': True,
        'published': False, 'production_written': False, 'budget': budget.snapshot(),
        'new_reported_input_tokens': sum(r['actual_receipt'].get('input_tokens') or 0 for r in receipts),
        'new_reported_output_tokens': sum(r['actual_receipt'].get('output_tokens') or 0 for r in receipts),
        'new_usage_missing_requests': sum(r['actual_receipt'].get('input_tokens') is None or r['actual_receipt'].get('output_tokens') is None for r in receipts)}
    write(HERE / 'new_request_receipts.json', receipts)
    write(HERE / 'effective_competency_result.json', updated)
    write(HERE / 'final_summary.json', summary)
    print(json.dumps({key: summary[key] for key in ('new_model_calls', 'total_model_calls', 'modeling_result',
        'competency_repair_result', 'competency_aggregate', 'published')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['--prepare-db']:
        prepare_database()
    elif sys.argv[1:] == ['--verify-claims']:
        verify_claims_on_copy()
    elif sys.argv[1:] == ['--execute']:
        execute()
    else:
        raise SystemExit('Use --prepare-db, --verify-claims, then --execute once')
