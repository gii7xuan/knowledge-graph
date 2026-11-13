"""Reconcile only the completed original request; never send or retry a request."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
from ontology_review.contracts import RESPONSE_SCHEMA, vote_binding, proposal_digest
from ontology_review.validators import response_errors, consensus
from review_agent.repository import ReviewRepository
from review_agent.case_budget import CaseBudget


def read(name):
    return json.loads((OUT / name).read_text(encoding='utf-8'))


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    assert not (OUT / 'zero_call_reconciliation_20261113.json').exists()
    m, p = read('prepared_manifest.json'), read('prepared_proposal.json')
    old_votes, old_summary = read('modeling_votes_20261113.json'), read('modeling_summary_20261113.json')
    assert proposal_digest(p) == p['proposal_digest'] == m['proposal_digest']
    assert old_summary['new_model_calls'] == 1 and old_summary['cumulative_model_calls'] == 20
    assert len(old_votes) == 1 and old_votes[0]['response_status'] == 'timeout' and old_votes[0]['outcome_unknown']
    repo = ReviewRepository(Path(m['database']))
    initial = old_votes[0]
    attempt = repo.request_attempt(initial['attempt_id'])
    assert attempt['state'] == 'completed' and attempt['response_status'] == 'ok'
    assert attempt['metadata']['proposal'] == p
    raw = deepcopy(attempt['result'])
    member = next(v for v in p['committee_members'] if v['id'] == initial['voter_id'])
    assert raw.get('late_response') is True and raw['request_key'] == initial['request_key']
    assert all(raw.get(k) == v for k, v in vote_binding(member, p).items())
    assert not response_errors({k: raw[k] for k in RESPONSE_SCHEMA['required']}, p)
    assert raw['decision'] == 'revise'
    with repo.connect() as db:
        current = [json.loads(r[0]) for r in db.execute('SELECT vote_json FROM ontology_votes WHERE audit_id=?', (p['audit_id'],))]
        assert current == old_votes
    for src, dst in [('modeling_votes_20261113.json', 'modeling_votes_at_stop_20261113.json'),
                     ('modeling_summary_20261113.json', 'modeling_summary_at_stop_20261113.json'),
                     ('progress_20261113.json', 'progress_at_stop_20261113.json')]:
        assert not (OUT / dst).exists()
        (OUT / dst).write_bytes((OUT / src).read_bytes())
    write('original_late_reply_20261113.json', raw)
    vote = {**raw, 'actual_calls': 1, 'attempts': [{**raw, 'attempt_no': 1}],
            'recovered_from_original_attempt': True, 'recovery_model_calls': 0}
    repo.ontology_recovered_vote(vote)
    saved = deepcopy(old_summary['budget'])
    saved['usage']['unknown_token_requests'] = 0
    before = [repo.case_status(case) for case in saved['case_ids']]
    budget = CaseBudget({'max_llm_calls_per_candidate': 3, 'max_total_tokens': 300000,
                         'max_retries_per_request': 0, 'total_timeout_seconds': 420},
                        repo, saved['case_ids'], task_type='ontology_extension', task_id=p['audit_id'], saved=saved)
    budget.tokens(raw)
    decision = consensus([vote], p)
    task = repo.extension_status(p['task_id'])
    result = {'extension_id': p['task_id'], 'recommendation': decision['result'], 'task_status': 'needs_human',
              'selected_round_id': p['round_id'], 'selected_proposal_digest': p['proposal_digest'],
              'consensus': decision, 'stop_reason': 'stopped_on_timeout_original_late_reply_reconciled',
              'full_chapter_review_executed': False, 'zero_call_reconciliation': True,
              'original_timeout_history_retained': True}
    repo.finish_ontology_audit(p['audit_id'], result, task['row_version'])
    summary = {**old_summary, 'observed_at_utc': now(), 'modeling_approvals': 0,
               'consensus_including_all_publication_gates': decision,
               'stop_reason': result['stop_reason'],
               'attempts': [{'attempt_id': attempt['attempt_id'], 'state': 'completed', 'voter_id': vote['voter_id'],
                             'counted_calls': 1, 'gateway_request_id': vote.get('gateway_request_id')}],
               'budget': budget.snapshot(), 'original_timeout_history_retained': True,
               'original_late_reply_reconciled': True, 'reconciliation_model_calls': 0,
               'unused_authorized_calls': 2, 'further_dispatch_automatically_resumed': False}
    write('modeling_votes_20261113.json', [vote])
    write('modeling_summary_20261113.json', summary)
    write('progress_20261113.json', {'active': False, **summary})
    receipt = {'observed_at_utc': now(), 'original_attempt_id': attempt['attempt_id'],
               'request_key': vote['request_key'], 'gateway_request_id': vote.get('gateway_request_id'),
               'original_wait_timeout_ms': initial['latency_ms'], 'original_response_latency_ms': vote['latency_ms'],
               'source': 'same_original_request_durable_result', 'request_reissued': False,
               'model_calls_added': 0, 'cumulative_model_calls': 20, 'decision': vote['decision'],
               'original_timeout_snapshot': 'modeling_votes_at_stop_20261113.json',
               'recovery_table_preserves_prior_vote': True,
               'reported_tokens_charged_once': (raw.get('input_tokens') or 0) + (raw.get('output_tokens') or 0),
               'case_accounts_before': before, 'case_accounts_after': budget.snapshot()['case_accounts'],
               'further_seats_dispatched': False}
    write('zero_call_reconciliation_20261113.json', receipt)
    print(json.dumps({k: receipt[k] for k in ('decision', 'model_calls_added', 'cumulative_model_calls',
                     'reported_tokens_charged_once', 'further_seats_dispatched')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
