"""Read a saved immutable review snapshot and record additional diagnostics."""
from collections import Counter
import json
from pathlib import Path
import sqlite3

from document.hierarchy_index import load_document_index
from evidence.verification import verify_evidence_bundle


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'runs/review_quality_checks/chapter1_v3_20261028_latest'


def parsed(value):
    return json.loads(value) if value else {}


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    summary = parsed((OUT / 'summary.json').read_text(encoding='utf-8'))
    connection = sqlite3.connect('file:' + summary['snapshot'] + '?immutable=1', uri=True)
    connection.row_factory = sqlite3.Row
    audits = [dict(r) for r in connection.execute("SELECT * FROM audit_runs WHERE status='completed'")
              if parsed(r['context_json']).get('execution_id') == summary['execution_id']]
    run_id = connection.execute('SELECT run_id FROM candidates WHERE candidate_id=?',
                                (audits[0]['candidate_id'],)).fetchone()[0]
    manifest = parsed(connection.execute('SELECT manifest_json FROM runs WHERE run_id=?', (run_id,)).fetchone()[0])
    document = load_document_index(manifest['document_path'], manifest['source_index_hash'], require_verified=True)
    verified_items = bundles = 0
    unique_items = set()
    source_failures = []
    extension_links = []
    vote_statuses = Counter()
    incomplete_causes = Counter()
    outcomes = []
    spot_checks = []
    for audit in audits:
        aid, cid = audit['audit_id'], audit['candidate_id']
        context = parsed(audit['context_json'])
        decision = parsed(connection.execute('SELECT decision_json FROM decisions WHERE audit_id=? '
                                             'ORDER BY decision_version DESC LIMIT 1', (aid,)).fetchone()[0])
        proposals = {r['round_id']: parsed(r['proposal_json']) for r in
                     connection.execute('SELECT * FROM proposals WHERE audit_id=?', (aid,))}
        for proposal in proposals.values():
            bundle = proposal['evidence_bundle']
            checked = verify_evidence_bundle(bundle, document, proposal['candidate'])
            bundles += 1
            verified_items += len(checked['items'])
            unique_items.update(e['evidence_id'] for e in checked['items'])
            old_rejects = len(bundle.get('rejected_items', []))
            for item in checked['rejected_items'][old_rejects:]:
                source_failures.append({'candidate_id': cid, 'evidence_id': item.get('evidence_id'),
                                        'anchor_status': item.get('anchor_status')})
        final_round = decision.get('final_round_id')
        proposal = proposals.get(final_round)
        votes = [parsed(r[0]) for r in connection.execute('SELECT vote_json FROM validator_votes WHERE audit_id=?', (aid,))]
        for vote in votes:
            vote_statuses[(vote['model_id'], vote['response_status'])] += 1
        current_votes = [v for v in votes if v.get('round_id') == final_round]
        candidate = proposal['candidate'] if proposal else parsed(connection.execute(
            'SELECT candidate_json FROM candidate_revisions WHERE candidate_id=? AND revision=?',
            (cid, audit['revision'])).fetchone()[0])
        unknown = [k for k, v in candidate['triple'].get('qualifier_status', {}).items() if v == 'unknown']
        failures = [{'model_id': v.get('model_id'), 'status': v.get('response_status'), 'error': v.get('error')}
                    for v in current_votes if v.get('response_status') != 'ok']
        if not context.get('independent_review_completed'):
            if not current_votes:
                incomplete_causes['planner_failure_without_votes'] += 1
            else:
                timed_out = any(v['status'] == 'timeout' for v in failures)
                invalid = any(v['status'] == 'invalid_contract' for v in failures)
                incomplete_causes['timeout_and_invalid_contract' if timed_out and invalid else
                                  'timeout_only' if timed_out else 'invalid_contract_only' if invalid else 'other'] += 1
        unanimous_support = (len(current_votes) == 3 and all(v.get('response_status') == 'ok' and
                             v.get('source_support') == 'supported' for v in current_votes))
        outcomes.append({'candidate_id': cid, 'review_completion_status': context.get('review_completion_status'),
                         'source_support_result': decision.get('source_support_result'),
                         'workflow': decision.get('workflow_state'), 'stop_reason': decision.get('stop_reason'),
                         'reason_codes': decision.get('reason_codes'), 'unknown_qualifiers': unknown,
                         'all_three_raw_source_support': unanimous_support,
                         'current_vote_failures': failures})
        extension_id = decision.get('pending_extension_id')
        if extension_id:
            task = connection.execute('SELECT * FROM ontology_extension_tasks WHERE extension_id=?',
                                      (extension_id,)).fetchone()
            subscription = connection.execute('SELECT * FROM ontology_extension_subscriptions '
                'WHERE extension_id=? AND candidate_id=? AND revision=?',
                (extension_id, cid, audit['revision'])).fetchone()
            extension_links.append({'candidate_id': cid, 'extension_id': extension_id,
                'task_status': task['status'] if task else None,
                'base_release_matches': bool(task and task['base_release_id'] == decision['ontology_release_id']),
                'subscription_exists': subscription is not None,
                'source_proposal_matches': bool(subscription and proposal and
                    subscription['source_proposal_digest'] == proposal['proposal_digest'])})
        if cid in {'cand_036a0237af4a944afc6c', 'cand_05c2bf196c34fd2d1656',
                   'cand_07364ae53742e434e3d0', 'cand_43809d045ba9207db5ee'}:
            spot_checks.append({'candidate_id': cid, 'triple': candidate['triple'],
                'workflow': decision.get('workflow_state'), 'source_consensus': decision.get('source_support_result'),
                'evidence': [{'evidence_id': e['evidence_id'], 'quote': e.get('quote')} for e in
                             (proposal or {}).get('evidence_bundle', {}).get('items', [])],
                'votes': [{k: v.get(k) for k in ('model_id', 'source_support', 'schema_status', 'schema_issue',
                                               'reason_codes', 'suggested_revision', 'checks')} for v in current_votes]})
    additional = {'verified_proposal_bundles': bundles, 'verified_source_items': verified_items,
        'unique_source_evidence_ids': len(unique_items), 'new_source_anchor_rejections': source_failures,
        'extension_links': extension_links, 'distinct_extension_tasks': len({e['extension_id'] for e in extension_links}),
        'model_vote_statuses': [{'model_id': key[0], 'status': key[1], 'count': count}
                               for key, count in sorted(vote_statuses.items())],
        'incomplete_candidate_causes': dict(incomplete_causes),
        'full_committee_but_unresolved': sum(o['review_completion_status'] == 'independent_review_completed'
                                           and o['source_support_result'] == 'unresolved' for o in outcomes),
        'unanimous_raw_support_but_unknown_qualifier': [o['candidate_id'] for o in outcomes if
            o['all_three_raw_source_support'] and o['unknown_qualifiers']],
        'database_modified_by_inspection': False, 'additional_model_calls': 0}
    save('additional_checks.json', additional)
    save('outcome_diagnostics.json', outcomes)
    save('semantic_spot_checks.json', spot_checks)
    connection.close()
    print(json.dumps({**additional, 'extension_links_count': len(extension_links), 'extension_links': 'see JSON'},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
