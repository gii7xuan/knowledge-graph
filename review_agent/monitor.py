"""Read-only projections of persisted agent events and independent requests.

No service initialization, migration, recovery, model client or consumer is used.
Source identifiers resolve only through the server-owned registry.
"""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from .repository import NotFoundError

ROOT = Path(__file__).resolve().parents[1]
DIMENSIONS = ('triple_support', 'entity_reference', 'relation_semantics', 'relation_direction',
              'qualifiers', 'evidence_sufficiency', 'counterevidence', 'mapping', 'knowledge_scope')
CLOSED = {'completed', 'failed', 'interrupted', 'timeout', 'paused', 'superseded'}


class MonitorUnavailable(RuntimeError):
    pass


def obj(value, default=None):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value else ({} if default is None else default)
    except (ValueError, TypeError):
        return {} if default is None else default


def pick(value, keys):
    return {k: value[k] for k in keys if k in value} if isinstance(value, dict) else {}


def stamp(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        try:
            return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
        except (ValueError, TypeError):
            return None


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def dimensions(vote):
    raw = vote.get('dimensions')
    if not isinstance(raw, dict):
        parsed = obj(vote.get('corrected_response') or vote.get('raw_response'))
        raw = parsed.get('dimensions') if isinstance(parsed, dict) else None
    if isinstance(raw, dict):
        return {name: pick(raw[name], ('status', 'note', 'evidence_ids', 'issue_code', 'relation_id',
                                      'competing_relation_ids'))
                for name in DIMENSIONS if isinstance(raw.get(name), dict)}
    # Historical opinions remain explicitly historical, without inventing missing checks.
    result = {c['check']: pick(c, ('status', 'note', 'evidence_ids', 'issue_code'))
              for c in vote.get('checks', []) if isinstance(c, dict) and c.get('check') in DIMENSIONS}
    if 'source_support' in vote:
        result['triple_support'] = {**result.get('triple_support', {}), 'status': vote['source_support']}
    if isinstance(vote.get('mapping'), dict):
        result['mapping'] = pick(vote['mapping'], ('status', 'note', 'relation_id', 'competing_relation_ids'))
    if vote.get('knowledge_scope'):
        result['knowledge_scope'] = {'status': vote['knowledge_scope']}
    return result


class ReviewMonitor:
    def __init__(self, registry=None, *, root=ROOT):
        self.root = Path(root).resolve()
        self.registry = Path(registry) if registry else self.root / 'config/review_monitor_sources.json'

    def _path(self, value):
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('监控数据源必须位于工作区内')
        return path

    def _registry(self):
        data = obj(self.registry.read_text(encoding='utf-8-sig'))
        sources = data.get('sources', [])
        if len({s['id'] for s in sources}) != len(sources):
            raise ValueError('重复的监控数据源标识')
        return data

    def sources(self):
        data = self._registry()
        return {'default_source_id': data.get('default_source_id'), 'items': [
            {**pick(s, ('id', 'label', 'kind')), 'read_only': True,
             'available': self._path(s['database']).is_file()} for s in data['sources']]}

    def _source(self, ident):
        source = next((s for s in self._registry()['sources'] if s['id'] == ident), None)
        if not source:
            raise NotFoundError('未登记的监控数据源')
        return source

    @contextmanager
    def _connect(self, source):
        path = self._path(source['database'])
        if not path.is_file():
            raise NotFoundError('监控数据库尚不存在')
        connection = None
        try:
            connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            yield connection
        except sqlite3.Error as exc:
            raise MonitorUnavailable('只读审核记录暂时不可用，请稍后刷新') from exc
        finally:
            if connection:
                connection.close()

    @staticmethod
    def _rows(c, table, suffix='', params=()):
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return []
        return [dict(r) for r in c.execute('SELECT * FROM ' + table + ' ' + suffix, params)]

    def _file(self, source, name):
        if not source.get('experiment_dir'):
            return {}
        folder = self._path(source['experiment_dir'])
        path = (folder / name).resolve()
        if not path.is_relative_to(folder):
            return {}
        try:
            return obj(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            return {}

    def _population(self, source, batch, progress, known, all_ids, execution_count):
        """Bind queued candidates to the execution, not the whole experiment."""
        record = self._file(source, 'batch_' + batch['execution_id'] + '.json')
        if not record or record.get('execution_id') != batch['execution_id']:
            record = progress if progress.get('execution_id') == batch['execution_id'] else {}
        started = stamp(record.get('started_at_epoch'))
        matches = []
        if started and source.get('experiment_dir'):
            folder = self._path(source['experiment_dir'])
            for path in folder.glob('batch_settings_*.json'):
                settings = self._file(source, path.name)
                when = stamp(settings.get('started_at_beijing'))
                ids = settings.get('candidate_ids')
                if when is None or not isinstance(ids, list):
                    continue
                selected = set(ids)
                if -1 <= started - when <= 60 and known <= selected <= all_ids and (
                        record.get('selected') is None or len(selected) == record['selected']):
                    matches.append((abs(started - when), selected))
        if matches:
            matches.sort(key=lambda item: item[0])
            if len(matches) == 1 or matches[0][0] < matches[1][0]:
                return matches[0][1], True
        bindings = self._file(source, 'sample_bindings.json')
        ids = {b['candidate_id'] for b in bindings} if isinstance(bindings, list) else set()
        if execution_count == 1 and ids and record.get('selected') == len(ids) and known <= ids <= all_ids:
            return ids, True
        return known, False

    def _batches(self, c, source):
        runs = self._rows(c, 'runs', 'ORDER BY created_at DESC')
        audits = self._rows(c, 'audit_runs', 'ORDER BY created_at DESC, rowid DESC')
        by_candidate = {r['candidate_id']: r['run_id'] for r in self._rows(c, 'candidates')}
        groups = {}
        for audit in audits:
            run = by_candidate.get(audit['candidate_id'])
            execution = obj(audit['context_json']).get('execution_id')
            if run and execution:
                groups.setdefault((run, execution), []).append(audit)
        items = []
        for run in runs:
            manifest = obj(run['manifest_json'])
            for (rid, execution), entries in groups.items():
                if rid != run['run_id']:
                    continue
                items.append({'id': digest([rid, execution])[:24], 'run_id': rid,
                              'execution_id': execution, 'scope': 'execution',
                              'created_at': entries[0]['created_at'], 'audits_started': len(entries),
                              'label': str(manifest.get('document_id') or rid) + ' · ' + execution[-8:]})
            items.append({'id': digest([run['run_id'], 'latest'])[:24], 'run_id': run['run_id'],
                          'execution_id': None, 'scope': 'latest',
                          'created_at': run['created_at'], 'label': str(manifest.get('document_id') or run['run_id']) + ' · 各候选最新审核'})
        return items

    def batches(self, source_id, candidate_id=None):
        source = self._source(source_id)
        with self._connect(source) as c:
            items = self._batches(c, source)
            if candidate_id:
                candidates = self._rows(c, 'candidates', 'WHERE candidate_id=?', (candidate_id,))
                if not candidates:
                    raise NotFoundError('该数据源中不存在此候选')
                items = [b for b in items if b['run_id'] == candidates[0]['run_id']]
                items.sort(key=lambda b: b['scope'] != 'latest')
            return {'source_id': source_id, 'items': items, 'read_only': True}

    def _binding_ok(self, value, audit, proposal, member):
        expected = {'audit_id': audit['audit_id'], 'task_id': audit['candidate_id'],
                    'object_revision': audit['revision'], 'round_id': proposal['round_id'],
                    'proposal_digest': proposal['proposal_digest'],
                    'evidence_hash': proposal.get('evidence_hash') or proposal.get('evidence_bundle', {}).get('evidence_hash'),
                    'model_id': member.get('model_id'), 'model_family': member.get('model_family'),
                    'voter_id': member['id']}
        for key in ('ontology_version', 'ontology_release_id', 'source_index_hash', 'source_snapshot_hash', 'profile_hash'):
            expected[key] = proposal.get('candidate', {}).get(key if key != 'source_snapshot_hash' else 'source_snapshot_id')
        return all(value.get(k) == v for k, v in expected.items() if v is not None)

    def _project(self, c, audit, round_id=None):
        context = obj(audit['context_json'])
        candidates = self._rows(c, 'candidates', 'WHERE candidate_id=?', (audit['candidate_id'],))
        current = candidates[0] if candidates else {}
        revisions = self._rows(c, 'candidate_revisions', 'WHERE candidate_id=? AND revision=?',
                               (audit['candidate_id'], audit['revision']))
        candidate = obj(revisions[0]['candidate_json']) if revisions else {}
        proposals = self._rows(c, 'proposals', 'WHERE audit_id=? ORDER BY round_id', (audit['audit_id'],))
        chosen = next((p for p in proposals if p['round_id'] == round_id), None) if round_id is not None else (proposals[-1] if proposals else None)
        if round_id is not None and chosen is None:
            raise NotFoundError('该审核轮次不存在')
        proposal = obj(chosen['proposal_json']) if chosen else {}
        if chosen:
            proposal.update(round_id=chosen['round_id'], proposal_digest=chosen['proposal_digest'],
                            evidence_hash=chosen['evidence_hash'])
        votes = self._rows(c, 'validator_votes', 'WHERE audit_id=? ORDER BY round_id', (audit['audit_id'],))
        requests = self._rows(c, 'request_attempts',
                              "WHERE json_extract(metadata_json,'$.binding.audit_id')=? ORDER BY started_at, rowid",
                              (audit['audit_id'],))
        roster = context.get('committee_members') or []
        if not roster:
            known = {}
            for row in votes:
                known[row['voter_id']] = {'id': row['voter_id'], 'model_id': row['model_id'], 'model_family': row['model_family']}
            roster = list(known.values())
        slots = []
        for member in roster:
            committed = []
            if chosen:
                committed = [obj(v['vote_json']) for v in votes
                             if v['voter_id'] == member['id'] and v['round_id'] == chosen['round_id']
                             and v['proposal_digest'] == chosen['proposal_digest']]
            valid = next((v for v in reversed(committed)
                          if v.get('response_status') == 'ok' and self._binding_ok(v, audit, proposal, member)), None)
            matched = []
            for request in requests:
                metadata = obj(request['metadata_json'])
                binding = metadata.get('binding') or {}
                if chosen and self._binding_ok(binding, audit, proposal, member):
                    matched.append((request, metadata))
            latest, metadata = matched[-1] if matched else ({}, {})
            result = obj(latest.get('result_json'))
            bound_result = bool(result and chosen and self._binding_ok(result, audit, proposal, member))
            opinion = valid or (result if bound_result else {}) or (committed[-1] if committed else {})
            recorded = valid is not None
            if recorded:
                state = 'validated'
            elif result.get('outcome_unknown') or latest.get('state') == 'unknown':
                state = 'outcome_unknown'
            elif latest.get('state') == 'running':
                state = 'correcting' if (metadata.get('feedback') or {}).get('kind') == 'field_patch' else 'running'
            elif bound_result and result.get('response_status') == 'ok':
                state = 'validated_pending_commit'
            elif opinion.get('error_code') == 'INSUFFICIENT_BALANCE':
                state = 'balance_paused'
            elif opinion.get('correction_terminal'):
                state = 'correction_stopped'
            elif opinion.get('response_status') in {'invalid_json', 'invalid_contract', 'invalid_reference'}:
                state = 'invalid_response'
            elif latest or committed:
                state = 'request_failed'
            else:
                state = 'queued'
            if not chosen:
                state = 'awaiting_proposal'
            dims = dimensions(opinion)
            # A mismatched binding is never displayed as an accepted opinion.
            binding_ok = bool(opinion and chosen and self._binding_ok(opinion, audit, proposal, member))
            if opinion and chosen and not binding_ok:
                state = 'binding_mismatch'
            correction_feedback = next(((m.get('feedback') or {}) for _, m in reversed(matched)
                                        if (m.get('feedback') or {}).get('kind') == 'field_patch'), {})
            slots.append({**pick(member, ('id', 'model_id', 'model_family')), 'state': state,
                          'recorded_valid': recorded, 'binding_verified': binding_ok,
                          'response_status': opinion.get('response_status'),
                          'dimensions': dims if binding_ok else {},
                          'received': bool(opinion.get('raw_response') is not None or opinion.get('response_status') == 'ok'),
                          'error_code': opinion.get('error_code'), 'error': opinion.get('error'),
                          'correction_stop_reason': opinion.get('correction_stop_reason'),
                          'response_origin': opinion.get('response_origin'),
                          'correction_attempted': sum((m.get('feedback') or {}).get('kind') == 'field_patch' for _, m in matched),
                          'cached': bool(opinion.get('cached')),
                          'attempt_id': latest.get('attempt_id') or opinion.get('attempt_id'),
                          'started_at': latest.get('started_at'), 'deadline_at': latest.get('deadline_at'),
                          'response_received_at': latest.get('response_received_at'),
                          'attempts': [{**pick(r, ('attempt_id', 'state', 'started_at', 'deadline_at', 'response_received_at')),
                                        'phase': 'field_patch' if (m.get('feedback') or {}).get('kind') == 'field_patch' else 'initial',
                                        **pick(obj(r.get('result_json')), ('input_tokens', 'output_tokens', 'error_code', 'response_status'))}
                                       for r, m in matched],
                          '_opinion': opinion, '_correction_feedback': correction_feedback})
        steps = self._rows(c, 'planner_steps', 'WHERE audit_id=? ORDER BY step_no', (audit['audit_id'],))
        calls = self._rows(c, 'agent_tool_calls', 'WHERE audit_id=? ORDER BY step_no, created_at', (audit['audit_id'],))
        checkpoints = self._rows(c, 'agent_checkpoints', 'WHERE audit_id=?', (audit['audit_id'],))
        checkpoint = obj(checkpoints[0]['checkpoint_json']) if checkpoints else {}
        events = []
        for step in steps:
            record = obj(step['record_json'])
            action = record.get('action') or {}
            events.append({'id': 'planner:' + str(step['step_no']), 'kind': 'planner',
                           'step_no': step['step_no'], 'at': step['created_at'],
                           **pick(record, ('role', 'status', 'model_id', 'error')),
                           'action': action.get('action'), 'summary': action.get('rationale_summary'),
                           'target_issue': action.get('target_issue')})
        for call in calls:
            record = obj(call['record_json'])
            events.append({'id': call['tool_call_id'], 'kind': 'tool', 'step_no': call['step_no'],
                           'at': call['created_at'], 'action': call['tool_name'], 'status': call['status'],
                           **pick(record, ('error', 'new_evidence_count', 'scope')),
                           'evidence_ids': record.get('new_evidence_ids', [])})
        events.sort(key=lambda e: (e['step_no'], 0 if e['kind'] == 'planner' else 1, str(e['at'])))
        decisions = self._rows(c, 'decisions', 'WHERE audit_id=? ORDER BY created_at DESC, rowid DESC', (audit['audit_id'],))
        decision = next((obj(d['decision_json']) for d in decisions
                         if chosen and obj(d['decision_json']).get('selected_proposal_digest') == chosen['proposal_digest']
                         and obj(d['decision_json']).get('selected_round_id') == chosen['round_id']), None)
        historical = current.get('current_revision') != audit['revision']
        failure = context.get('execution_failure') or {}
        stop_reason = context.get('agent_stop_reason') or failure.get('reason_code') or failure.get('error')
        evidence = proposal.get('evidence_bundle', {}).get('items', [])
        disagreed = [name for name in DIMENSIONS if len({s['dimensions'][name].get('status') for s in slots
                     if s['recorded_valid'] and name in s['dimensions']}) > 1]
        needs_revision = any(s['_opinion'].get('expression_status') == 'needs_revision' for s in slots if s['recorded_valid'])
        public = {
            'audit_id': audit['audit_id'], 'candidate_id': audit['candidate_id'], 'revision': audit['revision'],
            'current_revision': current.get('current_revision'), 'historical': historical, 'status': audit['status'],
            'created_at': audit['created_at'], 'completed_at': audit.get('completed_at'),
            'candidate': pick(candidate.get('triple') or candidate, ('head', 'tail', 'head_type', 'tail_type',
                              'surface_relation', 'relation_id', 'relation_label', 'qualifiers')),
            'versions': pick(context, ('response_contract_version', 'prompt_version', 'planner_model_id', 'configured_strategy', 'executed_strategy')),
            'rounds': [{'round_id': p['round_id'], 'proposal_digest': p['proposal_digest'], 'evidence_hash': p['evidence_hash']} for p in proposals],
            'binding': {'round_id': proposal.get('round_id'), 'proposal_digest': proposal.get('proposal_digest'),
                        'evidence_hash': proposal.get('evidence_hash'), **pick(candidate, ('ontology_release_id', 'ontology_version', 'source_index_hash'))},
            'phase': checkpoints[0]['phase'] if checkpoints else None,
            'pending_action': (checkpoint.get('state') or {}).get('pending_action', {}).get('action') if isinstance((checkpoint.get('state') or {}).get('pending_action'), dict) else None,
            'stop_reason': stop_reason, 'events': events, 'slots': slots, 'disagreement_dimensions': disagreed,
            'has_disagreement': bool(disagreed), 'needs_revision': needs_revision,
            'accepted_seats': sum(s['recorded_valid'] for s in slots), 'required_seats': len(roster),
            'received_seats': sum(s['received'] for s in slots),
            'three_seats_ready': len(slots) > 0 and all(s['recorded_valid'] for s in slots),
            'independent_review_completed': bool(decision and decision.get('independent_review_completed')),
            'decision': pick(decision, ('decision', 'source_support_result', 'workflow_state', 'review_completion_status',
                                      'independent_review_completed', 'stop_reason', 'reason_codes', 'source_support_consensus',
                                      'admission_consensus', 'explanation', 'schema_status')),
            'budget': pick(checkpoint.get('budget') or context.get('agent_budget') or {}, ('usage', 'remaining_llm_calls', 'max_planner_steps', 'max_validation_rounds')),
            'correction_limits': (context.get('correction_policy') or {}).get('limits', {}),
            '_evidence': evidence, '_proposal': proposal,
        }
        return public

    @staticmethod
    def _public(snapshot):
        return {k: ([{key: value for key, value in slot.items() if not key.startswith('_')} for slot in v]
                    if k == 'slots' else v) for k, v in snapshot.items() if not k.startswith('_')}

    def _brief(self, c, audit, candidate):
        """Batch polling avoids loading request prompts and checkpoint payloads."""
        context = obj(audit['context_json'])
        latest = c.execute('SELECT round_id,proposal_digest,evidence_hash FROM proposals WHERE audit_id=? ORDER BY round_id DESC LIMIT 1',
                           (audit['audit_id'],)).fetchone()
        roster = context.get('committee_members') or []
        valid = []
        if latest:
            proposal = {**dict(latest), 'candidate': candidate}
            rows = self._rows(c, 'validator_votes', 'WHERE audit_id=? AND round_id=? AND proposal_digest=?',
                              (audit['audit_id'], latest['round_id'], latest['proposal_digest']))
            for member in roster:
                value = next((obj(r['vote_json']) for r in rows if r['voter_id'] == member['id']), {})
                if value.get('response_status') == 'ok' and self._binding_ok(value, audit, proposal, member):
                    valid.append(value)
        checkpoint = c.execute('SELECT phase FROM agent_checkpoints WHERE audit_id=?', (audit['audit_id'],)).fetchone()
        complete = False
        for row in self._rows(c, 'decisions', 'WHERE audit_id=?', (audit['audit_id'],)):
            value = obj(row['decision_json'])
            if latest and value.get('selected_round_id') == latest['round_id'] and value.get('selected_proposal_digest') == latest['proposal_digest']:
                complete = bool(value.get('independent_review_completed'))
        opinions = [dimensions(v) for v in valid]
        disagreements = any(len({d[name].get('status') for d in opinions if name in d}) > 1 for name in DIMENSIONS)
        return {'phase': checkpoint['phase'] if checkpoint else None,
                'stop_reason': context.get('agent_stop_reason') or (context.get('execution_failure') or {}).get('reason_code'),
                'accepted_seats': len(valid), 'required_seats': len(roster),
                'three_seats_ready': len(roster) > 0 and len(valid) == len(roster),
                'independent_review_completed': complete, 'has_disagreement': disagreements,
                'needs_revision': any(v.get('expression_status') == 'needs_revision' for v in valid)}

    def summary(self, source_id, batch_id):
        source = self._source(source_id)
        with self._connect(source) as c:
            batch = next((b for b in self._batches(c, source) if b['id'] == batch_id), None)
            if not batch:
                raise NotFoundError('该数据源中不存在此批次')
            candidates = self._rows(c, 'candidates', 'WHERE run_id=? ORDER BY created_at, candidate_id', (batch['run_id'],))
            all_audits = self._rows(c, 'audit_runs', 'ORDER BY created_at DESC, rowid DESC')
            audits = [a for a in all_audits if not batch['execution_id'] or obj(a['context_json']).get('execution_id') == batch['execution_id']]
            selected = {a['candidate_id'] for a in audits}
            progress = self._file(source, 'progress.json')
            include_frozen = bool(batch['execution_id'] and progress.get('execution_id') == batch['execution_id'])
            progress_matches = include_frozen or (batch['scope'] == 'latest' and progress.get('run_id') == batch['run_id'])
            bindings = self._file(source, 'sample_bindings.json')
            labels = {b['candidate_id']: b.get('sample_id') for b in bindings} if isinstance(bindings, list) else {}
            population_complete = not bool(batch['execution_id'])
            if batch['execution_id']:
                selected, population_complete = self._population(
                    source, batch, progress, selected,
                    {candidate['candidate_id'] for candidate in candidates},
                    sum(b['scope'] == 'execution' and b['run_id'] == batch['run_id'] for b in self._batches(c, source)))
            if batch['execution_id']:
                candidates = [r for r in candidates if r['candidate_id'] in selected]
            entries = []
            for candidate in candidates:
                audit = next((a for a in audits if a['candidate_id'] == candidate['candidate_id']
                              and a['revision'] == candidate['current_revision']), None)
                revisions = self._rows(c, 'candidate_revisions', 'WHERE candidate_id=? AND revision=?',
                                       (candidate['candidate_id'], candidate['current_revision']))
                frozen = obj(revisions[0]['candidate_json']) if revisions else {}
                snapshot = self._brief(c, audit, frozen) if audit else None
                triple = frozen.get('triple') or frozen
                entries.append({'candidate_id': candidate['candidate_id'], 'revision': candidate['current_revision'],
                                'sample_id': labels.get(candidate['candidate_id']), 'audit_id': audit['audit_id'] if audit else None,
                                'head': triple.get('head'), 'relation': triple.get('relation_label') or triple.get('surface_relation') or triple.get('relation_id'),
                                'tail': triple.get('tail'), 'status': audit['status'] if audit else 'not_started',
                                **(pick(snapshot, ('phase', 'stop_reason', 'accepted_seats', 'received_seats', 'required_seats', 'three_seats_ready',
                                                  'independent_review_completed', 'has_disagreement', 'needs_revision')) if snapshot else {})})
            entries.sort(key=lambda e: (e['sample_id'] or '', e['candidate_id']))
            counts = {'total': len(entries), 'started': sum(bool(e['audit_id']) for e in entries),
                      'processing_closed': sum(e['status'] in CLOSED for e in entries),
                      'three_seats_ready': sum(bool(e.get('three_seats_ready')) for e in entries),
                      'review_completed': sum(bool(e.get('independent_review_completed')) for e in entries),
                      'running': sum(e['status'] == 'running' for e in entries),
                      'disagreements': sum(bool(e.get('has_disagreement')) for e in entries)}
            pause = progress.get('pause_reason') or {}
            payload = {'source_id': source_id, 'batch': batch, 'counts': counts, 'candidates': entries,
                       'read_only': True, 'population_complete': population_complete,
                       'batch_status': progress.get('status') if progress_matches else ('running' if counts['running'] else 'idle'),
                       'paused': bool(progress_matches and progress.get('paused')),
                       'pause_reason': {**pick(pause, ('reason_code',)), **pick(pause.get('vote') or {}, ('model_id', 'http_status'))} if progress_matches else {}}
            return self._with_cursor(payload)

    @staticmethod
    def _with_cursor(payload, after=None):
        cursor = digest(payload)
        observed = datetime.now(timezone.utc).isoformat()
        if after == cursor:
            return {'not_modified': True, 'cursor': cursor, 'observed_at': observed, 'source_id': payload['source_id']}
        return {**payload, 'cursor': cursor, 'observed_at': observed}

    def audits(self, source_id, candidate_id):
        with self._connect(self._source(source_id)) as c:
            rows = self._rows(c, 'audit_runs', 'WHERE candidate_id=? ORDER BY created_at DESC, rowid DESC', (candidate_id,))
            return {'source_id': source_id, 'items': [pick(r, ('audit_id', 'revision', 'status', 'created_at')) for r in rows]}

    def _audit(self, c, audit_id):
        rows = self._rows(c, 'audit_runs', 'WHERE audit_id=?', (audit_id,))
        if not rows:
            raise NotFoundError('该数据源中不存在此审核')
        return rows[0]

    def snapshot(self, source_id, audit_id, *, round_id=None, after=None):
        with self._connect(self._source(source_id)) as c:
            snapshot = self._project(c, self._audit(c, audit_id), round_id)
            return self._with_cursor({'source_id': source_id, **self._public(snapshot), 'read_only': True}, after)

    def opinion(self, source_id, audit_id, voter_id, *, round_id=None):
        with self._connect(self._source(source_id)) as c:
            snapshot = self._project(c, self._audit(c, audit_id), round_id)
            slot = next((s for s in snapshot['slots'] if s['id'] == voter_id), None)
            if not slot:
                raise NotFoundError('该审核中不存在此席位')
            vote = slot['_opinion']
            trace = vote.get('correction_trace') or {}
            feedback = slot['_correction_feedback']
            plan = trace.get('plan') or feedback.get('repair_plan') or {}
            return {'source_id': source_id, 'audit_id': audit_id, 'voter_id': voter_id, 'binding': snapshot['binding'],
                    'evidence': [pick(e, ('evidence_id', 'quote', 'anchor_valid', 'anchor_status', 'block_type', 'scope_id'))
                                 for e in snapshot['_evidence']],
                    'dimensions': slot['dimensions'], 'binding_verified': slot['binding_verified'],
                    'raw_response': vote.get('raw_response', feedback.get('original_raw_response')),
                    'raw_correction_response': vote.get('raw_correction_response'),
                    'corrected_response': vote.get('corrected_response'),
                    'correction': {**pick(trace, ('correction_id', 'original_attempt_id', 'protected_judgments_unchanged')),
                                   'targets': plan.get('targets', []), 'limits': snapshot['correction_limits']},
                    'attempts': slot['attempts'], 'read_only': True}

    def get(self, path, query):
        import re
        source_id = (query.get('source_id') or [''])[0]
        if path == '/api/review-monitor/sources':
            return self.sources()
        if path == '/api/review-monitor/batches':
            return self.batches(source_id, (query.get('candidate_id') or [None])[0])
        match = re.fullmatch(r'/api/review-monitor/batches/([^/]+)/summary', path)
        if match:
            return self.summary(source_id, match[1])
        match = re.fullmatch(r'/api/review-monitor/candidates/([^/]+)/audits', path)
        if match:
            return self.audits(source_id, match[1])
        match = re.fullmatch(r'/api/review-monitor/audits/([^/]+)/(snapshot|opinion)', path)
        if match:
            round_id = int(query['round_id'][0]) if query.get('round_id') else None
            if match[2] == 'opinion':
                return self.opinion(source_id, match[1], (query.get('voter_id') or [''])[0], round_id=round_id)
            return self.snapshot(source_id, match[1], round_id=round_id, after=(query.get('after') or [None])[0])
        raise NotFoundError('监控接口不存在')
