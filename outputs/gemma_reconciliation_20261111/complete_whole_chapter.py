"""Three original seats, one call each; publish only a fully approved isolated release."""
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from manual_gemma_reassessment import HERE, OUT, ATTEMPT, KEY, ANSWER, read, write, file_hash, now, scoped_attempts
from review_agent.repository import ReviewRepository
from review_agent.contracts import canonical_json, sha256_json
from review_agent.case_budget import CaseBudget
from review_agent.request_execution import judge_typed
from review_agent.service import load_config
from review_agent.cli import _configured_validators
from ontology_review import semantic_competency as competency
from ontology_review.validators import response_errors, consensus
from ontology_review.contracts import RESPONSE_SCHEMA, vote_binding, proposal_digest
from ontology_review.publisher import current_release_id, OntologyPublisher
from runs.formal_definitions_20261111 import code_hashes, roster


def protected_unchanged(manifest):
    assert all(file_hash(Path(path)) == value for path, value in read(OUT / 'protected_inputs.json').items())
    assert current_release_id(ROOT / 'runs/ontology_releases') == manifest['production_release_head']


def modeling_votes(repository, proposal):
    votes = repository.ontology_audit(proposal['audit_id'])['votes']
    selected = [v for v in votes if v['round_id'] == proposal['round_id']]
    assert len(selected) == len(proposal['committee_members']) == 3
    for member in proposal['committee_members']:
        vote = next(v for v in selected if v['voter_id'] == member['id'])
        assert all(vote.get(k) == value for k, value in vote_binding(member, proposal).items())
        cache = repository.validation_request(vote['request_key'])
        if vote['response_status'] == 'ok':
            assert not response_errors({k: vote[k] for k in RESPONSE_SCHEMA['required']}, proposal)
            assert cache['status'] == 'completed'
            assert cache['vote'].get('response_id') == vote.get('response_id')
        else:
            assert member['id'] == 'judge_b' and vote.get('outcome_unknown')
            assert cache['status'] == 'unknown'
    return selected


def independent_full_chapter_review(repository, request, voters, cases, parent_votes):
    """Use the exact CQ contract, keeping an incomplete modeling gate explicit.

    The user authorized all three CQ calls after one manual Gemma reassessment.
    No unknown modeling request is repeated, reclassified, or supplied to a peer.
    The ordinary publisher still requires three valid modeling approvals.
    """
    competency.initialize(repository)
    with repository.connect() as connection:
        assert not connection.execute('SELECT 1 FROM ontology_semantic_competency WHERE parent_digest=?',
                                      (request['parent_proposal_digest'],)).fetchone()
    reason = ('用户已授权一次Gemma受控重评及随后原三席各一次整章审核，总上限9次。'
              '重评仍504，建模门槛未满足；独立完成冻结整章问题，不重试未知请求，不授权发布。')
    for case in cases:
        account = repository.case_status(case)
        additions = {k: max(0, account['usage'][k] + value - account['limits'][k])
                     for k, value in {'llm_calls': 3, 'tokens': 450000}.items()}
        additions = {k: value for k, value in additions.items() if value}
        if additions:
            with repository.connect() as connection:
                identity = connection.execute('SELECT candidate_id FROM review_cases WHERE case_id=?', (case,)).fetchone()[0]
            repository.authorize_case_budget(identity, additions, reason=reason,
                actor='user_authorized_independent_whole_chapter',
                authorization_key='independent-whole-chapter:' + request['request_hash'] + ':' + case)
    budget = CaseBudget({'max_llm_calls_per_candidate': 3, 'max_total_tokens': 450000,
                         'total_timeout_seconds': 600}, repository, cases,
                        task_type='ontology_competency_review', task_id=request['task_id'])
    saved = {}
    def store():
        ordered = [saved[m['id']] for m in request['committee_members'] if m['id'] in saved]
        result = {'assessment': competency.aggregate(ordered, request), 'votes': ordered,
                  'budget': budget.snapshot(), 'reason': reason,
                  'parent_modeling_approvals': sum(v.get('response_status') == 'ok' and v.get('decision') == 'approve'
                                                  for v in parent_votes),
                  'parent_unknown_requests_preserved': True, 'publication_authorized_by_this_assessment': False}
        with repository.transaction(immediate=True) as connection:
            connection.execute('INSERT INTO ontology_semantic_competency VALUES(?,?,?,?) '
                               'ON CONFLICT(parent_digest) DO UPDATE SET result_json=excluded.result_json',
                               (request['parent_proposal_digest'], request['request_hash'],
                                canonical_json(request), canonical_json(result)))
        return result
    for voter in sorted(voters, key=lambda v: {'qwen': 0, 'deepseek': 1, 'gemma': 2}[v['model_family'].lower()]):
        assert budget.reserve_calls(1)
        print(json.dumps({'phase': 'dispatch', 'voter_id': voter['id'], 'model': voter['model_id']}), flush=True)
        vote = judge_typed(voter, request, repository, task_type='ontology_competency_review',
            binding=competency.binding(voter, request),
            invoke=lambda feedback: competency.judge_once(voter, request, feedback),
            validate_cached=lambda value: competency.errors({k: value.get(k) for k in competency.SCHEMA['required']}, request),
            prompt_hash=sha256_json([competency.PROMPT, competency.SCHEMA]),
            retries=0, timeout=budget.remaining_seconds(), deadline_epoch=budget.deadline_epoch)
        if not vote.get('actual_calls'):
            budget.refund_calls(1)
        for attempt in vote.get('attempts') or []:
            budget.tokens(attempt)
        saved[voter['id']] = vote
        store()
        print(json.dumps({'phase': 'received', 'voter_id': voter['id'],
                          'status': vote['response_status'], 'coverage': vote.get('coverage'),
                          'assessment_statuses': [a['status'] for a in vote.get('assessments', [])]}, ensure_ascii=False), flush=True)
    return store()


def check_original_receipt(repository, proposal):
    original = repository.request_attempt(ATTEMPT)
    # An actual late substantive objection cannot be hidden by the replacement.
    value = original['result']
    if value.get('response_status') == 'ok':
        assert all(value.get(k) == expected for k, expected in vote_binding(
            next(m for m in proposal['committee_members'] if m['id'] == 'judge_b'), proposal).items())
        assert not response_errors({k: value[k] for k in RESPONSE_SCHEMA['required']}, proposal)
        if value['decision'] != 'approve' or value['counter_evidence_ids']:
            raise ValueError('Original late Gemma response contains substantive objections; publication held')
    return {'attempt_id': ATTEMPT, 'state': original['state'],
            'provider_outcome_reconciled': original['state'] != 'unknown',
            'manual_reassessment_authorized': True,
            'authorization_file_sha256': file_hash(HERE / 'manual_reassessment_authorization.json'),
            'original_result_used_as_approval': False}


def reconcile_late_competency(repository, request, result):
    result = deepcopy(result)
    changed = []
    for index, old in enumerate(result['votes']):
        if old.get('response_status') == 'ok':
            continue
        cache = repository.validation_request(old.get('request_key')) if old.get('request_key') else None
        if not cache or cache['status'] != 'completed':
            continue
        actual = deepcopy(cache['vote'])
        member = next(m for m in request['committee_members'] if m['id'] == old['voter_id'])
        assert all(actual.get(k) == value for k, value in competency.binding(member, request).items())
        assert not competency.errors({k: actual.get(k) for k in competency.SCHEMA['required']}, request)
        history = repository.request_history(old['request_key'])
        assert len(history) == 1 and history[0]['state'] == 'completed'
        assert actual.get('response_id') == history[0]['result'].get('response_id')
        changed.append({'voter_id': old['voter_id'], 'old_caller_envelope': old,
                        'actual_durable_response': actual, 'new_calls': 0})
        result['votes'][index] = actual
    if changed:
        result['assessment'] = competency.aggregate(result['votes'], request)
        with repository.transaction(immediate=True) as connection:
            row = connection.execute('SELECT request_hash FROM ontology_semantic_competency WHERE parent_digest=?',
                                     (request['parent_proposal_digest'],)).fetchone()
            assert row and row[0] == request['request_hash']
            connection.execute('UPDATE ontology_semantic_competency SET result_json=? WHERE parent_digest=? AND request_hash=?',
                               (canonical_json(result), request['parent_proposal_digest'], request['request_hash']))
    write(HERE / 'whole_chapter_late_receipt_reconciliation.json', {'new_calls': 0, 'reconciled': changed})
    return result


def main():
    if sys.argv[1:] != ['--execute']:
        raise SystemExit('Only --execute after the new Gemma modeling approval')
    if (HERE / 'whole_chapter_started.json').exists():
        raise ValueError('Single launch only; inspect existing receipts rather than relaunching')
    manifest = read(OUT / 'prepared_manifest.json')
    proposal = read(OUT / 'prepared_proposal.json')
    authorization = read(HERE / 'manual_reassessment_authorization.json')
    assert authorization['user_answer'] == ANSWER
    assert read(HERE / 'continuation_authorization.json')['authorized_total_call_limit'] == 9
    assert code_hashes() == manifest['code_fingerprints']
    assert file_hash(OUT / 'prepared_proposal.json') == manifest['proposal_sha256']
    assert proposal_digest(proposal) == proposal['proposal_digest'] == manifest['proposal_digest']
    assert file_hash(OUT / 'prepared_competency_request.json') == manifest['competency_request_sha256']
    protected_unchanged(manifest)
    repository = ReviewRepository(OUT / 'review.sqlite3')
    config = load_config()
    voters = _configured_validators(config)
    assert roster(voters) == manifest['roster']
    votes = modeling_votes(repository, proposal)
    original = check_original_receipt(repository, proposal)
    assert len(scoped_attempts(repository, manifest['extension_id'])) == 6
    history = repository.request_history(KEY)
    assert len(history) == 2 and history[-1]['state'] in {'completed', 'unknown'}
    request, cases = competency.request_for(repository, OUT / 'releases', proposal)
    assert request == read(OUT / 'prepared_competency_request.json')
    write(HERE / 'whole_chapter_started.json', {'at': now(), 'max_new_calls': 3, 'total_limit': 9,
        'request_hash': request['request_hash'], 'original_request_exception': original,
        'models': manifest['roster'], 'no_automatic_retry': True})
    print(json.dumps({'phase': 'whole_chapter', 'request_hash': request['request_hash'], 'max_calls': 3}), flush=True)
    result = independent_full_chapter_review(repository, request, voters, cases, votes)
    result = reconcile_late_competency(repository, request, result)
    write(HERE / 'whole_chapter_result.json', result)
    verified = competency.verified_result(repository, OUT / 'releases', proposal)
    final = consensus(votes, proposal, semantic_competency=verified)
    total_calls = len(scoped_attempts(repository, manifest['extension_id']))
    assert total_calls <= 9
    task = repository.extension_status(manifest['extension_id'])
    audit_result = {'extension_id': manifest['extension_id'], 'recommendation': final['result'],
        'task_status': 'approved' if final['result'] == 'approve' else 'needs_human',
        'selected_round_id': proposal['round_id'], 'selected_proposal_digest': proposal['proposal_digest'],
        'consensus': final, 'semantic_competency': verified,
        'manual_unknown_reassessment': check_original_receipt(repository, proposal),
        'stop_reason': 'bounded_three_seat_whole_chapter_assessment_completed'}
    repository.finish_ontology_audit(manifest['audit_id'], audit_result, task['row_version'])
    publication = None
    if final['result'] == 'approve':
        check_original_receipt(repository, proposal)
        protected_unchanged(manifest)
        publisher = OntologyPublisher(repository, OUT / 'releases', config)
        reviewed_task, _, _ = publisher.reviewed(manifest['extension_id'])
        publication = publisher.publish(manifest['extension_id'], {
            'approved_by': 'user_authorized_codex_isolated_definition_release',
            'reason': '用户授权8项定义、一次Gemma受控重评与原三席整章核验；全部门槛通过，仅生成隔离版本，原Gemma未知执行历史保留。',
            'proposal_digest': proposal['proposal_digest'], 'expected_base_release_id': proposal['base_release_id'],
            'expected_row_version': reviewed_task['row_version'], 'confirm_high_impact': True,
        }, automatic=False)
        write(HERE / 'isolated_publication.json', publication)
    protected_unchanged(manifest)
    write(HERE / 'completion_summary.json', {'at': now(), 'total_calls': total_calls,
        'authorized_max_calls': 9,
        'modeling_approvals': sum(v.get('response_status') == 'ok' and v.get('decision') == 'approve' for v in votes),
        'semantic_competency': verified,
        'semantic_assessment': result['assessment'], 'final_consensus': final,
        'isolated_publication': publication, 'original_request': check_original_receipt(repository, proposal),
        'protected_inputs_unchanged': True, 'production_head_unchanged': True})
    print(json.dumps({'phase': 'finished', 'calls': total_calls, 'semantic': result['assessment'],
                      'consensus': final['result'], 'isolated_published': bool(publication)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
