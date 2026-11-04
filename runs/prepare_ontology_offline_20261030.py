"""Prepare source-verified ontology dossiers from an immutable DB snapshot.

This helper does not construct model clients, write to the review DB, invent a
schema delta, or turn deterministic checks into semantic review votes.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3

from ontologyBuild.ontology_adapter import normalize_name
from ontologyBuild.release_service import load_release
from ontology_review import evidence as source_tools
from ontology_review.delta import catalog
from ontology_review.publisher import current_release_id
from ontology_review.tools import OntologyTools
from review_agent.consensus import Committee, aggregate_votes
from review_agent.contracts import sha256_json
from review_agent.proposal_resolver import selected_proposal
from review_agent.repository import ReviewRepository, _ClosingConnection

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'runs/review_followup_20261030/offline_preparation'
OUT.mkdir(exist_ok=True)
snapshot = Path(json.loads((OUT.parent / 'completion_summary.json').read_text(encoding='utf-8'))['snapshot'])


class ReadOnlySnapshotRepository(ReviewRepository):
    def connect(self):
        connection = sqlite3.connect(self.database_path.as_uri() + '?immutable=1',
                                    uri=True, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        return connection

    def ensure_case(self, candidate_id, limits=None):
        with self.connect() as connection:
            row = connection.execute('SELECT case_id FROM review_cases WHERE candidate_id=?', (candidate_id,)).fetchone()
        if not row:
            raise ValueError('existing case account required; offline preparation cannot create one')
        return row[0]


@lru_cache(maxsize=4)
def base_release(identity):
    return load_release(ROOT / 'runs/ontology_releases' / identity)


# Reuse only verified document indexes. Every evidence bundle is verified again.
source_tools.load_document_index = lru_cache(maxsize=4)(source_tools.load_document_index)
repo = ReadOnlySnapshotRepository(snapshot)
before_hash = sha256(snapshot.read_bytes()).hexdigest()
current_release = current_release_id(ROOT / 'runs/ontology_releases')
with repo.connect() as connection:
    ids = [r[0] for r in connection.execute("SELECT extension_id FROM ontology_extension_tasks WHERE status IN ('proposed','needs_human','in_review','approved') ORDER BY extension_id")]

results = []
for number, identity in enumerate(ids, 1):
    task = repo.extension_status(identity)
    row = {'extension_id': identity, 'task_row_version': task['row_version'],
           'delta_revision': task['delta_revision'], 'change_kind': task['change_kind'],
           'base_release_id': task['base_release_id'], 'model_calls': 0,
           'blockers': [], 'warnings': [], 'description': task['description']}
    try:
        base = base_release(task['base_release_id'])
        if task['base_release_id'] != current_release:
            row['blockers'].append('base_release_changed_rebase_required')
        if (base.manifest['ontology_version'] != task['base_ontology_version'] or
                base.manifest['profile_hash'] != task['base_profile_hash']):
            raise ValueError('base ontology/profile binding mismatch')
        if task.get('review_invalidated_by_human'):
            row['blockers'].append('human_revision_required')
        if any(e.get('action') == 'reject' for e in task.get('human_events', [])):
            row['blockers'].append('human_rejection_preserved')
        prior = repo.ontology_counter_history(identity)
        combined = {e['evidence_id']: e for e in [*task.get('review_evidence', []), *prior]}
        task['review_evidence'] = list(combined.values())
        evidence, documents, candidates, case_ids = source_tools.load_sources(repo, task)
        row.update(evidence_count=len(evidence), evidence_hash=sha256_json(evidence),
                   verified_evidence=evidence, current_candidates=[], source_consensus=[])
        for trigger in task['trigger_candidates']:
            candidate = candidates.get(trigger['candidate_id'])
            if not candidate or candidate['revision'] != trigger['revision']:
                continue
            audit = repo.get_audit(candidate['candidate_id'], trigger['audit_id'])
            original = next(p for p in audit['rounds'] if p['round_id'] == trigger['round_id'])
            proposal, votes = selected_proposal(repo, candidate, {
                'audit_id': trigger['audit_id'], 'selected_round_id': trigger['round_id'],
                'selected_proposal_digest': trigger['source_proposal_digest'],
                'selected_evidence_hash': trigger['evidence_hash'],
                'committee_version': original['committee_version']})
            policy = proposal['committee_policy']
            committee = Committee([m['id'] for m in proposal['committee_members']],
                                  policy['required_votes'], policy['minimum_distinct_families'],
                                  proposal['committee_version'])
            consensus = aggregate_votes(votes, committee, proposal=proposal,
                                        proposal_digest=proposal['proposal_digest'])
            row['source_consensus'].append({'candidate_id': candidate['candidate_id'],
                'revision': candidate['revision'], 'audit_id': trigger['audit_id'],
                'proposal_digest': proposal['proposal_digest'],
                'eligible_voter_ids': consensus['eligible_voter_ids'],
                'source_result': consensus.get('source_support_consensus', {}).get('result')})
            if consensus.get('source_support_consensus', {}).get('result') != 'supported':
                row['blockers'].append('source_support_not_confirmed')
            current = repo.get_candidate(candidate['candidate_id'])
            row['current_candidates'].append({'candidate_id': candidate['candidate_id'],
                'revision': candidate['revision'], 'row_version': current['row_version'],
                'triple': candidate['triple']})
        items = catalog(base.ontology)
        names = {normalize_name(value) for candidate in candidates.values()
                 for value in (candidate['triple'].get('surface_relation'), candidate['triple'].get('relation_id')) if value}
        row['existing_term_recall'] = {
            'catalog_items_scanned': len(items),
            'exact_matches': [item for identity, item in items.items()
                if any(normalize_name(value) in names for value in
                       [identity, item.get('label', ''), *(item.get('aliases') or [])] if value)],
            'scope': 'complete_formal_identity_catalog_exact_name_recall',
            'semantic_equivalence_or_gap_confirmed': False,
            'full_rdf_semantic_lookup_still_required': True}
        runtime = SimpleNamespace(repository=repo, base=base, candidates=candidates,
                                  documents=documents, state={'task': task,
                                  'delta': deepcopy(task.get('delta')), 'evidence': evidence,
                                  'checks': {}, 'validation': None})
        tools = OntologyTools(runtime)
        if task.get('delta'):
            row['existing_delta'] = {key: task['delta'].get(key) for key in ('delta_hash', 'operations', 'risk_category')}
            row['offline_delta_checks'] = {
                'structure': tools.validate_schema_structure(),
                'impact': tools.analyze_schema_impact(),
                'competency': tools.run_competency_checks()}
            row['warnings'].append('existing_delta_is_not_independently_approved')
        else:
            row['offline_delta_checks'] = {'status': 'not_run_no_delta'}
        row['case_accounts'] = [repo.case_status(case_id) for case_id in case_ids]
        with repo.connect() as connection:
            audit_ids = [r[0] for r in connection.execute('SELECT audit_id FROM ontology_audits WHERE extension_id=? ORDER BY rowid', (identity,))]
        row['prior_reviews'] = []
        for audit_id in audit_ids:
            audit = repo.ontology_audit(audit_id)
            row['prior_reviews'].append({'audit_id': audit_id,
                'stop_reason': (audit.get('result') or {}).get('stop_reason'),
                'proposal_digests': [p['proposal_digest'] for p in audit['proposals']],
                'votes': [{key: vote.get(key) for key in
                          ('voter_id', 'round_id', 'response_status', 'decision', 'outcome_unknown',
                           'error_code', 'reason_codes', 'suggested_changes', 'counter_evidence_ids', 'request_key')}
                          for vote in audit['votes']]})
        row['preparation_status'] = 'held' if row['blockers'] else 'source_verified_awaiting_model_investigation'
    except Exception as exc:
        row['preparation_status'] = 'held'
        row['blockers'].append(type(exc).__name__ + ': ' + str(exc))
    row['blockers'] = sorted(set(row['blockers']))
    (OUT / (identity + '.json')).write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
    results.append({key: row.get(key) for key in ('extension_id', 'change_kind', 'preparation_status',
                    'evidence_count', 'blockers', 'warnings')})
    if number % 10 == 0:
        print(json.dumps({'prepared': number, 'total': len(ids)}, ensure_ascii=False), flush=True)

after_hash = sha256(snapshot.read_bytes()).hexdigest()
if before_hash != after_hash:
    raise RuntimeError('read-only snapshot was unexpectedly modified')
summary = {'prepared_at_utc': datetime.now(timezone.utc).isoformat(), 'snapshot': str(snapshot),
    'snapshot_sha256': after_hash, 'snapshot_unchanged': True, 'production_database_modified': False,
    'external_model_calls': 0, 'tasks_examined': len(results),
    'statuses': dict(Counter(r['preparation_status'] for r in results)),
    'change_kinds': dict(Counter(r['change_kind'] for r in results)),
    'blockers': dict(Counter(reason for r in results for reason in r['blockers'])),
    'review_still_required': 'Full RDF lookup, semantic modeling and the original fixed independent committee remain required.',
    'items': results}
(OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({key: value for key, value in summary.items() if key != 'items'}, ensure_ascii=False, indent=2))
