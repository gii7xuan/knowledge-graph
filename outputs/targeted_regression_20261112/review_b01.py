"""One authorized B01 modeling pass. No publication or sample-review path exists."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
from ontology_review.contracts import proposal_digest, RESPONSE_SCHEMA
from ontology_review.publisher import current_release_id
from ontology_review.review_payload import validator_payload
from ontology_review.validators import judge, consensus, prompt_for, response_errors
from review_agent.cli import _configured_validators
from review_agent.contracts import sha256_json
from review_agent.repository import ReviewRepository
from review_agent.case_budget import CaseBudget
from review_agent.service import load_config


def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def write(name, value): (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
def now(): return datetime.now(timezone.utc).isoformat()


def scoped_attempts(repo, manifest):
    with repo.connect() as c:
        rows = c.execute('SELECT attempt_id,state,metadata_json,result_json FROM request_attempts').fetchall()
    result = []
    for row in rows:
        binding = json.loads(row['metadata_json']).get('binding', {})
        if binding.get('task_id') == manifest['extension_id'] and binding.get('round_id') == manifest['round_id']:
            vote = json.loads(row['result_json'] or '{}')
            result.append({'attempt_id': row['attempt_id'], 'state': row['state'], 'voter_id': binding.get('voter_id'),
                           'counted_calls': 0 if vote.get('actual_calls') == 0 else 1,
                           'gateway_request_id': vote.get('gateway_request_id')})
    return result


def preflight():
    m = read(OUT / 'prepared_manifest.json')
    assert all(digest(ROOT / path) == value for path, value in m['source_bundle_code_fingerprints'].items()), 'Frozen runtime/config changed'
    assert digest(OUT / 'prepare.py') == m['preparation_script_sha256']
    assert all(digest(OUT / path) == value for path, value in m['artifact_hashes'].items()), 'Frozen regression or proposal changed'
    p = read(OUT / 'prepared_b01_proposal.json')
    assert proposal_digest(p) == p['proposal_digest'] == m['proposal_digest']
    assert p['round_id'] == m['round_id'] and p['round_id'] >= 2 and len(p['operations']) == 10
    assert p['check_records']['regression']['status'] == 'pass' and not p['check_records']['regression']['independent_semantic_approval']
    protected = read(OUT / 'protected_inputs.json')
    assert all(digest(Path(path)) == value for path, value in protected.items()), 'Protected history changed'
    assert current_release_id(ROOT / 'runs/ontology_releases') == current_release_id(Path(m['release_root'])) == p['base_release_id']
    repo_path = Path(m['database']).resolve()
    assert repo_path.is_relative_to(OUT) and repo_path != ROOT / 'runs/v3/review.sqlite3'
    repo = ReviewRepository(repo_path)
    audit = repo.ontology_audit(p['audit_id'])
    stored = next(x for x in audit['proposals'] if x['round_id'] == p['round_id'])
    assert stored == p and not any(v['round_id'] == p['round_id'] for v in audit['votes'])
    assert not scoped_attempts(repo, m), 'Existing requests require reconciliation; never relaunch automatically'
    task = repo.extension_status(p['task_id'])
    assert task['delta_revision'] == p['object_revision'] and task['delta']['delta_hash'] == p['delta_hash']
    voters = _configured_validators(load_config())
    roster = [{k: v[k] for k in ('id', 'model_id', 'model_family')} | {
        'model_identity': str(v.get('model_identity') or v['model_id']).casefold()} for v in voters]
    assert roster == p['committee_members'] and len(roster) == 3
    payload = validator_payload(p, delivery_version='ontology_validator_payload_v3')
    payload.update(output_contract=RESPONSE_SCHEMA, allowed_evidence_ids=[e['evidence_id'] for e in p['evidence']])
    prompt = prompt_for(p)
    with repo.connect() as c:
        cases = [r[0] for r in c.execute('SELECT case_id FROM review_cases WHERE candidate_id IN (SELECT candidate_id FROM ontology_extension_subscriptions WHERE extension_id=? AND status=?)', (p['task_id'], 'active'))]
    assert cases and all(repo.case_status(case)['usage']['tokens'] < repo.case_status(case)['limits']['tokens'] for case in cases)
    safe_settings = []
    for v in voters:
        client = v['client']
        assert client.base_url.rstrip('/') in {'https://43.143.151.41:3000/v1', 'https://api.deepseek.com'}
        safe_settings.append({k: v.get(k) for k in ('id', 'model_id', 'model_family', 'max_output_tokens', 'request_timeout_seconds', 'http_timeout_seconds')} | {
            'base_url': client.base_url, 'stream': client.stream_responses, 'retry_count': 0})
    result = {'at': now(), 'status': 'ready_for_authorization', 'proposal_digest': p['proposal_digest'],
        'round_id': p['round_id'], 'operations': len(p['operations']), 'evidence_spans': len(p['evidence']),
        'request_payload_sha256': sha256_json(payload), 'prompt_sha256': sha256_json(prompt),
        'request_json_utf8_bytes': len(json.dumps(payload, ensure_ascii=False).encode()),
        'runtime_settings': safe_settings, 'case_ids': cases,
        'max_new_calls': 3, 'previous_cumulative_calls': 13, 'requested_cumulative_limit': 16,
        'no_automatic_retry': True, 'stop_on_unknown': True, 'runner_sha256': digest(Path(__file__)),
        'sample_review_or_publication_implemented': False, 'model_calls_executed': 0}
    return m, p, repo, voters, cases, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    m, p, repo, voters, cases, ready = preflight()
    if not args.execute:
        write('review_preflight.json', ready)
        print(json.dumps(ready, ensure_ascii=False), flush=True); return
    # The assistant writes this only after a matching explicit user reply.
    grant_path = OUT / 'user_authorization.json'
    if not grant_path.exists(): raise ValueError('No additional real-model-call authorization')
    grant = read(grant_path)
    assert grant.get('approved') is True and grant.get('source') == 'explicit_user_reply_in_chat'
    assert grant.get('scope') == 'b01_three_modeling_calls' and grant.get('proposal_digest') == p['proposal_digest']
    assert grant.get('max_new_calls') == 3 and grant.get('previous_cumulative_calls') == 13 and grant.get('cumulative_limit') == 16
    sealed = read(OUT / 'dispatch_manifest.json')
    assert digest(Path(__file__)) == sealed['runner_sha256'] and digest(OUT / 'prepared_manifest.json') == sealed['prepared_manifest_sha256']
    assert ready['request_payload_sha256'] == sealed['request_payload_sha256'] and ready['prompt_sha256'] == sealed['prompt_sha256']
    assert sha256_json(ready['runtime_settings']) == sealed['runtime_settings_sha256']
    if (OUT / 'execution_started.json').exists(): raise ValueError('This one-pass launch was already started; reconcile instead of relaunching')
    write('execution_started.json', {'at': now(), 'proposal_digest': p['proposal_digest'],
        'authorization_sha256': digest(grant_path), 'max_new_calls': 3, 'prior_cumulative_calls': 13})
    budget = CaseBudget({'max_llm_calls_per_candidate': 3, 'max_total_tokens': 240000,
        'max_retries_per_request': 0, 'total_timeout_seconds': 420}, repo, cases,
        task_type='ontology_extension', task_id=p['audit_id'])
    votes, stop_reason = [], 'fixed_three_seats_attempted_once'
    # Gemma last so an unknown outcome never causes unattended further calls.
    for v in sorted(voters, key=lambda x: {'qwen': 0, 'deepseek': 1, 'gemma': 2}.get(x['model_family'].casefold(), 3)):
        assert len(scoped_attempts(repo, m)) < 3
        if not budget.reserve_calls(1): stop_reason = 'case_or_task_budget_exhausted'; break
        write('progress.json', {'active': True, 'phase': 'B01_modeling_only', 'voter_id': v['id'], 'at': now()})
        vote = judge({**v, 'ontology_delivery_version': 'ontology_validator_payload_v3'}, p, repo,
            retries=0, timeout=float(v.get('request_timeout_seconds', 180)), deadline_epoch=budget.deadline_epoch)
        votes.append(vote); repo.ontology_vote(vote)
        write('modeling_votes.json', votes)
        if not vote.get('actual_calls'): budget.refund_calls(1)
        for attempt in vote.get('attempts') or []: budget.tokens(attempt)
        print(json.dumps({'voter_id': v['id'], 'response_status': vote['response_status'], 'decision': vote.get('decision'),
            'outcome_unknown': vote.get('outcome_unknown', False), 'gateway_request_id': vote.get('gateway_request_id')}, ensure_ascii=False), flush=True)
        if vote.get('outcome_unknown') or vote.get('pause_job'):
            stop_reason = 'unknown_outcome_or_account_pause_preserved'; break
    result = consensus(votes, p)
    approvals = [v for v in votes if v.get('response_status') == 'ok' and v.get('decision') == 'approve'
        and not response_errors({k: v[k] for k in RESPONSE_SCHEMA['required']}, p)]
    modeling_pass = (len(approvals) == 3 and len({v['model_family'] for v in approvals}) == 3
        and result['counts']['approve'] == 3 and len(result['eligible_voter_ids']) == 3)
    task = repo.extension_status(p['task_id'])
    repo.finish_ontology_audit(p['audit_id'], {'extension_id': p['task_id'], 'recommendation': result['result'],
        'task_status': 'needs_human', 'selected_round_id': p['round_id'], 'selected_proposal_digest': p['proposal_digest'],
        'consensus': result, 'stop_reason': stop_reason, 'full_chapter_review_executed': False}, task['row_version'])
    attempts = scoped_attempts(repo, m)
    count = sum(a['counted_calls'] for a in attempts)
    assert len(attempts) <= 3 and count <= 3
    summary = {'at': now(), 'scope': 'B01_modeling_only', 'new_model_calls': count, 'cumulative_model_calls': 13 + count,
        'authorized_cumulative_limit': 16, 'modeling_approvals': len(approvals), 'modeling_three_seats_approve': modeling_pass,
        'consensus_including_all_publication_gates': result, 'stop_reason': stop_reason, 'attempts': attempts,
        'full_chapter_review_executed': False, 'sample_model_review_executed': False,
        'published': False, 'production_written': False, 'budget': budget.snapshot(),
        'protected_inputs_unchanged': all(digest(Path(path)) == value for path, value in read(OUT / 'protected_inputs.json').items())}
    assert summary['protected_inputs_unchanged']
    write('modeling_summary.json', summary)
    write('progress.json', {'active': False, **summary})
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__': main()
