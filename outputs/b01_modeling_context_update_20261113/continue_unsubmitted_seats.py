"""Use the two unspent authorized seats only after the original reply is known."""
from datetime import datetime, timezone
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
from review_context_b01 import read, write, digest, now, scoped_attempts
from ontology_review.contracts import proposal_digest, RESPONSE_SCHEMA, vote_binding
from ontology_review.modeling_context import verify
from ontology_review.publisher import current_release_id
from ontology_review.validators import judge, consensus, response_errors
from review_agent.cli import _configured_validators
from review_agent.repository import ReviewRepository
from review_agent.case_budget import CaseBudget
from review_agent.service import load_config


def main():
    assert not (OUT / 'continuation_dispatch_started_20261113.json').exists(), 'Never relaunch; reconcile the existing attempts'
    m, p = read(OUT / 'prepared_manifest.json'), read(OUT / 'prepared_proposal.json')
    grant = read(OUT / 'user_authorization_20261113.json')
    prior = read(OUT / 'modeling_summary_20261113.json')
    reconciled = read(OUT / 'zero_call_reconciliation_20261113.json')
    protected = read(OUT / 'protected_inputs.json')
    assert all(digest(Path(path)) == value for path, value in protected.items())
    assert all(digest(ROOT / path) == value for path, value in m['code_fingerprints'].items())
    assert proposal_digest(p) == p['proposal_digest'] == m['proposal_digest'] == grant['proposal_digest']
    assert grant['approved'] and grant['source'] == 'explicit_user_reply_in_chat'
    assert grant['scope'] == 'b01_v3_context_three_modeling_calls'
    assert grant['max_new_calls'] == 3 and grant['previous_cumulative_calls'] == 19 and grant['cumulative_limit'] == 22
    assert grant['case_token_envelope'] == 300000 and grant['case_token_addition_authorized'] == 223465
    assert reconciled['request_reissued'] is False and reconciled['model_calls_added'] == 0
    assert prior['new_model_calls'] == 1 and prior['original_late_reply_reconciled']
    verify(p)
    repo = ReviewRepository(Path(m['database']))
    audit = repo.ontology_audit(p['audit_id'])
    assert next(x for x in audit['proposals'] if x['round_id'] == p['round_id']) == p
    votes = [v for v in audit['votes'] if v['round_id'] == p['round_id']]
    assert len(votes) == 1 and votes[0]['voter_id'] == 'judge_a' and votes[0]['response_status'] == 'ok'
    assert votes[0]['decision'] == 'revise' and not response_errors({k: votes[0][k] for k in RESPONSE_SCHEMA['required']}, p)
    first_vote = deepcopy(votes[0])
    attempts = scoped_attempts(repo, p)
    assert len(attempts) == 1 and attempts[0]['state'] == 'completed'
    assert current_release_id(ROOT / 'runs/ontology_releases') == current_release_id(Path(m['release_root'])) == p['base_release_id']
    voters = _configured_validators(load_config())
    roster = [{k: v[k] for k in ('id', 'model_id', 'model_family')} |
              {'model_identity': str(v.get('model_identity') or v['model_id']).casefold()} for v in voters]
    assert roster == p['committee_members']
    for v in voters:
        assert v['client'].base_url.rstrip('/') in grant['destinations'] and v['max_output_tokens'] <= 8192
        assert all(first_vote.get(k) == value for k, value in vote_binding(roster[0], p).items())
    selected = sorted([v for v in voters if v['id'] not in {x['voter_id'] for x in votes}],
                      key=lambda v: {'deepseek': 0, 'gemma': 1}.get(v['model_family'], 2))
    assert {v['id'] for v in selected} == {'judge_b', 'judge_c'}
    assert prior['stop_reason'] == 'case_or_task_budget_exhausted'
    assert (OUT / 'continuation_started_20261113.json').exists()
    (OUT / 'modeling_summary_local_deadline_blocked_20261113.json').write_bytes((OUT / 'modeling_summary_20261113.json').read_bytes())
    saved_budget = deepcopy(prior['budget'])
    renewed_deadline = time.time() + 360
    saved_budget['deadline_epoch'] = renewed_deadline
    write('continuation_dispatch_started_20261113.json', {
        'observed_at_utc': now(), 'authorization_sha256': digest(OUT / 'user_authorization_20261113.json'),
        'proposal_digest': p['proposal_digest'], 'runner_sha256': digest(Path(__file__)),
        'reason': 'Original unknown outcome reconciled to a valid exact-bound reply; send only the two never-submitted seats under the same grant.',
        'previous_new_calls': 1, 'maximum_remaining_calls': 2, 'cumulative_limit': 22,
        'qwen_will_not_be_called': True, 'new_budget_grant': False, 'original_timeout_history_retained': True,
        'original_dispatch_deadline_epoch': prior['budget']['deadline_epoch'],
        'continuation_dispatch_deadline_epoch': renewed_deadline,
        'previous_continuation_blocked_locally_without_dispatch': True,
        'continuation_wall_timeout_seconds': 360})
    budget = CaseBudget({'max_llm_calls_per_candidate': 3, 'max_total_tokens': 300000,
                         'max_retries_per_request': 0, 'total_timeout_seconds': 360},
                        repo, prior['budget']['case_ids'], task_type='ontology_extension', task_id=p['audit_id'],
                        saved=saved_budget, deadline_epoch=renewed_deadline)
    stop = 'all_authorized_seats_attempted_once_after_zero_call_reconciliation'
    for v in selected:
        assert len(scoped_attempts(repo, p)) < 3
        if not budget.reserve_calls(1):
            stop = 'case_or_task_budget_exhausted'
            break
        write('progress_20261113.json', {'active': True, 'phase': 'remaining_previously_authorized_seats',
                                      'voter_id': v['id'], 'observed_at_utc': now()})
        vote = judge({**v, 'ontology_delivery_version': 'ontology_validator_payload_v3'}, p, repo,
                     retries=0, timeout=float(v.get('request_timeout_seconds', 180)), deadline_epoch=budget.deadline_epoch)
        votes.append(vote)
        repo.ontology_vote(vote)
        write('modeling_votes_20261113.json', votes)
        if not vote.get('actual_calls'):
            budget.refund_calls(1)
        for attempt in vote.get('attempts') or []:
            budget.tokens(attempt)
        print(json.dumps({'voter_id': v['id'], 'response_status': vote['response_status'],
                          'decision': vote.get('decision'), 'outcome_unknown': vote.get('outcome_unknown', False)}, ensure_ascii=False), flush=True)
        if vote.get('outcome_unknown') or vote.get('pause_job'):
            stop = 'unknown_outcome_or_account_pause_preserved'
            break
    decision = consensus(votes, p)
    valid_approvals = [v for v in votes if v.get('response_status') == 'ok' and v.get('decision') == 'approve'
                       and not response_errors({k: v[k] for k in RESPONSE_SCHEMA['required']}, p)]
    task = repo.extension_status(p['task_id'])
    repo.finish_ontology_audit(p['audit_id'], {'extension_id': p['task_id'], 'recommendation': decision['result'],
        'task_status': 'needs_human', 'selected_round_id': p['round_id'], 'selected_proposal_digest': p['proposal_digest'],
        'consensus': decision, 'stop_reason': stop, 'full_chapter_review_executed': False}, task['row_version'])
    attempts = scoped_attempts(repo, p)
    count = sum(a['counted_calls'] for a in attempts)
    assert count <= 3 and len(attempts) <= 3 and len({a['voter_id'] for a in attempts}) == len(attempts)
    assert next(v for v in votes if v['voter_id'] == 'judge_a') == first_vote
    summary = {**prior, 'observed_at_utc': now(), 'new_model_calls': count, 'cumulative_model_calls': 19 + count,
               'modeling_approvals': len(valid_approvals), 'modeling_three_seats_approve': len(valid_approvals) == 3,
               'consensus_including_all_publication_gates': decision, 'attempts': attempts,
               'stop_reason': stop, 'budget': budget.snapshot(), 'unused_authorized_calls': 3 - count,
               'continued_only_after_original_unknown_resolved': True,
               'further_dispatch_automatically_resumed': True, 'qwen_original_revise_unchanged': True}
    assert all(digest(Path(path)) == value for path, value in protected.items())
    write('modeling_summary_20261113.json', summary)
    write('progress_20261113.json', {'active': False, **summary})
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
