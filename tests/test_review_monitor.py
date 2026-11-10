"""Real persisted review records, late requests, read-only sources and HTTP routing."""
import copy
import hashlib
import json
import sqlite3
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import build_opener, ProxyHandler
from urllib.error import HTTPError
from pathlib import Path

from review_agent.monitor import ReviewMonitor, MonitorUnavailable, dimensions
from review_agent.repository import NotFoundError
from tests import test_review_v6_integration as fixtures


class ReviewMonitorTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ReviewV6IntegrationTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        candidate, result, _, _ = fixture.run_review(failed_dimension='qualifiers', version='validator_response_v6_2')
        self.repo = fixture.f.repo
        self.root = fixture.f.folder
        self.database = self.repo.database_path
        self.candidate = candidate
        self.audit_id = result['audit_id']
        with self.repo.transaction(immediate=True) as c:
            row = c.execute('SELECT context_json FROM audit_runs WHERE audit_id=?', (self.audit_id,)).fetchone()
            context = json.loads(row[0]); context['execution_id'] = 'monitor_execution'
            c.execute('UPDATE audit_runs SET context_json=? WHERE audit_id=?', (json.dumps(context), self.audit_id))
        registry = {'default_source_id': 'experiment', 'sources': [
            {'id': 'experiment', 'label': 'Test experiment', 'kind': 'experiment', 'database': 'review.sqlite3', 'experiment_dir': '.'}]}
        self.registry = self.root / 'monitor_sources.json'
        self.registry.write_text(json.dumps(registry), encoding='utf-8')
        self.monitor = ReviewMonitor(self.registry, root=self.root)
        self.batch = next(b for b in self.monitor.batches('experiment')['items'] if b['execution_id'])

    def snapshot(self):
        return self.monitor.snapshot('experiment', self.audit_id)

    def alter_vote(self, voter, change):
        with self.repo.transaction(immediate=True) as c:
            row = c.execute('SELECT vote_json FROM validator_votes WHERE audit_id=? AND voter_id=?', (self.audit_id, voter)).fetchone()
            value = json.loads(row[0]); change(value)
            c.execute('UPDATE validator_votes SET vote_json=? WHERE audit_id=? AND voter_id=?',
                      (json.dumps(value), self.audit_id, voter))

    def test_sources_are_registered_and_reads_never_initialize_or_write(self):
        before = hashlib.sha256(Path(self.database).read_bytes()).hexdigest()
        with patch('review_agent.repository.ReviewRepository.initialize', side_effect=AssertionError('must not initialize')):
            sources = self.monitor.sources()
            self.monitor.summary('experiment', self.batch['id'])
            self.snapshot()
            self.monitor.opinion('experiment', self.audit_id, 'a')
        self.assertTrue(sources['items'][0]['read_only'])
        self.assertNotIn('database', sources['items'][0])
        self.assertEqual(hashlib.sha256(Path(self.database).read_bytes()).hexdigest(), before)
        with self.monitor._connect(self.monitor._source('experiment')) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("UPDATE audit_runs SET status='running'")

    def test_quality_panel_requires_the_same_population_revision_and_audits(self):
        summary=self.monitor.summary('experiment',self.batch['id'])
        report={'version':'four_axis_quality_v1','population':len(summary['candidates']),
            'audit_bindings':[{k:row.get(k) for k in ('candidate_id','revision','audit_id')} for row in summary['candidates']],
            'metrics':[],'human_resolved':0}
        (self.root/'quality_report.json').write_text(json.dumps(report),encoding='utf-8')
        self.assertIn('quality_report',self.monitor.summary('experiment',self.batch['id']))
        report['audit_bindings'][0]['audit_id']='stale-audit'
        (self.root/'quality_report.json').write_text(json.dumps(report),encoding='utf-8')
        self.assertNotIn('quality_report',self.monitor.summary('experiment',self.batch['id']))

    def test_unregistered_source_and_outside_workspace_are_rejected(self):
        with self.assertRaises(NotFoundError):
            self.monitor.batches('../review.sqlite3')
        data = json.loads(self.registry.read_text())
        data['sources'][0]['database'] = '../outside.sqlite3'
        self.registry.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.monitor.sources()

    def test_real_negative_opinion_counts_and_remains_separate_from_admission(self):
        result = self.snapshot()
        self.assertTrue(result['three_seats_ready'])
        self.assertTrue(result['independent_review_completed'])
        self.assertEqual(result['accepted_seats'], 3)
        self.assertEqual(result['slots'][0]['dimensions']['qualifiers']['status'], 'fail')
        self.assertEqual(result['decision']['workflow_state'], 'revision_required')
        self.assertEqual(self.monitor.summary('experiment', self.batch['id'])['counts']['three_seats_ready'], 1)

    def test_completed_request_is_visible_before_vote_commit_and_does_not_create_a_decision(self):
        with self.repo.transaction(immediate=True) as c:
            c.execute('DELETE FROM validator_votes WHERE audit_id=?', (self.audit_id,))
            c.execute('DELETE FROM decisions WHERE audit_id=?', (self.audit_id,))
        result = self.snapshot()
        self.assertTrue(all(s['state'] == 'validated_pending_commit' for s in result['slots']))
        self.assertEqual(result['accepted_seats'], 0)
        self.assertEqual(result['received_seats'], 3)
        self.assertFalse(result['three_seats_ready'])
        self.assertEqual(result['decision'], {})

    def test_unknown_request_is_not_a_negative_vote_and_valid_other_seats_are_preserved(self):
        with self.repo.transaction(immediate=True) as c:
            c.execute("DELETE FROM validator_votes WHERE audit_id=? AND voter_id='b'", (self.audit_id,))
            row = c.execute("SELECT attempt_id,result_json FROM request_attempts WHERE json_extract(metadata_json,'$.binding.audit_id')=? AND json_extract(metadata_json,'$.binding.voter_id')='b'", (self.audit_id,)).fetchone()
            value = json.loads(row['result_json'])
            value.update(response_status='api_error', outcome_unknown=True, error_code='GATEWAY_REQUEST_OUTCOME_UNKNOWN')
            c.execute("UPDATE request_attempts SET state='unknown',result_json=? WHERE attempt_id=?", (json.dumps(value), row['attempt_id']))
        result = self.snapshot()
        self.assertEqual(result['accepted_seats'], 2)
        slot = next(s for s in result['slots'] if s['id'] == 'b')
        self.assertEqual(slot['state'], 'outcome_unknown')
        self.assertFalse(slot['recorded_valid'])

    def test_mismatched_evidence_binding_is_excluded(self):
        self.alter_vote('a', lambda vote: vote.update(evidence_hash='other-frozen-bundle'))
        with self.repo.transaction(immediate=True) as c:
            c.execute("DELETE FROM request_attempts WHERE json_extract(metadata_json,'$.binding.voter_id')='a'")
        result = self.snapshot()
        slot = next(s for s in result['slots'] if s['id'] == 'a')
        self.assertEqual(result['accepted_seats'], 2)
        self.assertEqual(slot['state'], 'binding_mismatch')
        self.assertEqual(slot['dimensions'], {})

    def test_current_round_does_not_inherit_old_round_votes_or_old_decision(self):
        with self.repo.transaction(immediate=True) as c:
            row = dict(c.execute('SELECT * FROM proposals WHERE audit_id=?', (self.audit_id,)).fetchone())
            row.update(round_id=2, proposal_digest='new-round-digest', evidence_hash='new-evidence')
            p = json.loads(row['proposal_json']); p.update(round_id=2, proposal_digest='new-round-digest')
            p['evidence_bundle']['evidence_hash'] = 'new-evidence'; row['proposal_json'] = json.dumps(p)
            c.execute('INSERT INTO proposals(' + ','.join(row) + ') VALUES(' + ','.join('?' for _ in row) + ')', tuple(row.values()))
        result = self.snapshot()
        self.assertEqual(result['binding']['round_id'], 2)
        self.assertEqual(result['accepted_seats'], 0)
        self.assertEqual(result['decision'], {})
        self.assertEqual(self.monitor.snapshot('experiment', self.audit_id, round_id=1)['accepted_seats'], 3)

    def test_current_audit_is_selected_even_when_old_decision_exists(self):
        with self.repo.transaction(immediate=True) as c:
            row = dict(c.execute('SELECT * FROM audit_runs WHERE audit_id=?', (self.audit_id,)).fetchone())
            row.update(audit_id='new_running_audit', status='running', created_at='9999999999', completed_at=None)
            context = json.loads(row['context_json']); context['audit_id'] = row['audit_id']; row['context_json'] = json.dumps(context)
            c.execute('INSERT INTO audit_runs(' + ','.join(row) + ') VALUES(' + ','.join('?' for _ in row) + ')', tuple(row.values()))
        entry = next(e for e in self.monitor.summary('experiment', self.batch['id'])['candidates'] if e['candidate_id'] == self.candidate['candidate_id'])
        self.assertEqual(entry['audit_id'], 'new_running_audit')
        self.assertFalse(entry['three_seats_ready'])

    def test_cursor_unchanged_and_sensitive_request_metadata_not_exported(self):
        with self.repo.transaction(immediate=True) as c:
            for row in c.execute('SELECT attempt_id,metadata_json FROM request_attempts').fetchall():
                metadata = json.loads(row['metadata_json']); metadata['provider_private'] = 'SECRET_METADATA_MARKER'
                c.execute('UPDATE request_attempts SET metadata_json=? WHERE attempt_id=?', (json.dumps(metadata), row['attempt_id']))
        result = self.snapshot()
        unchanged = self.monitor.snapshot('experiment', self.audit_id, after=result['cursor'])
        self.assertTrue(unchanged['not_modified'])
        self.assertNotIn('slots', unchanged)
        detail = self.monitor.opinion('experiment', self.audit_id, 'a')
        self.assertNotIn('SECRET_METADATA_MARKER', json.dumps([result, detail]))
        self.assertNotIn('_opinion', json.dumps(result))

    def test_queued_population_and_pause_are_visible(self):
        candidates = self.repo.list_candidates(self.candidate['run_id'])
        (self.root / 'sample_bindings.json').write_text(json.dumps([{'candidate_id': c['candidate_id'], 'sample_id': 'DEV-' + str(i)} for i, c in enumerate(candidates)]))
        (self.root / 'progress.json').write_text(json.dumps({'run_id': self.candidate['run_id'], 'execution_id': 'monitor_execution',
            'selected':len(candidates), 'paused': True, 'status': 'paused', 'pause_reason': {'reason_code': 'INSUFFICIENT_BALANCE', 'vote': {'model_id': 'm'}}}))
        result = self.monitor.summary('experiment', self.batch['id'])
        self.assertTrue(result['paused'])
        self.assertEqual(result['counts']['total'], len(candidates))
        self.assertTrue(any(e['status'] == 'not_started' for e in result['candidates']))

    def test_full_experiment_population_is_not_assigned_to_every_batch(self):
        (self.root / 'sample_bindings.json').write_text(json.dumps([{'candidate_id': c['candidate_id'], 'sample_id': str(i)}
            for i, c in enumerate(self.repo.list_candidates(self.candidate['run_id']))]))
        with self.repo.transaction(immediate=True) as c:
            row = dict(c.execute('SELECT * FROM audit_runs WHERE audit_id=?', (self.audit_id,)).fetchone())
            row.update(audit_id='other_execution_audit')
            context = json.loads(row['context_json']);context['execution_id']='other_execution';row['context_json']=json.dumps(context)
            c.execute('INSERT INTO audit_runs(' + ','.join(row) + ') VALUES(' + ','.join('?' for _ in row) + ')', tuple(row.values()))
        result = self.monitor.summary('experiment', self.batch['id'])
        self.assertEqual(result['counts']['total'], 1)
        self.assertFalse(result['population_complete'])

    def test_same_candidate_in_other_source_never_reuses_votes(self):
        other = self.root / 'other.sqlite3'
        with sqlite3.connect(self.database) as source, sqlite3.connect(other) as target:
            source.backup(target)
        with sqlite3.connect(other) as target:
            target.execute('DELETE FROM validator_votes'); target.execute('DELETE FROM request_attempts')
        registry = json.loads(self.registry.read_text())
        registry['sources'].append({'id': 'other', 'database': 'other.sqlite3'})
        self.registry.write_text(json.dumps(registry))
        self.assertEqual(self.monitor.snapshot('other', self.audit_id)['accepted_seats'], 0)
        self.assertEqual(self.snapshot()['accepted_seats'], 3)

    def test_field_patch_provenance_is_available_without_rewriting_opinions(self):
        raw = {'dimensions': {'qualifiers': {'status': 'fail', 'note': 'missing condition'}}}
        def change(vote):
            vote.update(raw_response=raw, corrected_response={**raw, 'mapping': 'unassigned'},
                        raw_correction_response={'repairs': {'field_0': {}}},
                        correction_trace={'protected_judgments_unchanged': True, 'plan': {
                            'targets': [{'path': '/dimensions/mapping', 'kind': 'missing', 'actual_type': 'missing'}]}})
        self.alter_vote('a', change)
        detail = self.monitor.opinion('experiment', self.audit_id, 'a')
        self.assertTrue(detail['correction']['protected_judgments_unchanged'])
        self.assertEqual(detail['correction']['targets'][0]['path'], '/dimensions/mapping')
        self.assertEqual(detail['raw_response']['dimensions']['qualifiers']['status'], 'fail')

    def test_http_route_uses_monitor_without_creating_default_service(self):
        import workbench_app
        server = ThreadingHTTPServer(('127.0.0.1', 0), workbench_app.WorkbenchHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        with patch.object(workbench_app, 'ReviewMonitor', return_value=self.monitor), patch.object(workbench_app, 'get_default_service', side_effect=AssertionError('must not initialize service')):
            url = 'http://127.0.0.1:' + str(server.server_port) + '/api/review-monitor/audits/' + self.audit_id + '/snapshot?source_id=experiment'
            with build_opener(ProxyHandler({})).open(url) as response:
                result = json.load(response)
        self.assertEqual(result['accepted_seats'], 3)
        self.assertEqual(dimensions({'raw_response': ['invalid root']}), {})
        frontend = self.root / 'frontend'
        frontend.mkdir()
        (frontend / 'index.html').write_text('<div>monitor entry</div>', encoding='utf-8')
        with patch.object(workbench_app, 'FRONTEND_DIR', frontend):
            with build_opener(ProxyHandler({})).open('http://127.0.0.1:' + str(server.server_port) + '/review-monitor') as response:
                self.assertEqual(response.status, 200)
                self.assertIn(b'monitor entry', response.read())
        with patch.object(workbench_app, 'ReviewMonitor', side_effect=MonitorUnavailable('read-only source busy')):
            with self.assertRaises(HTTPError) as error:
                build_opener(ProxyHandler({})).open(url)
            with error.exception as response:
                self.assertEqual(response.code, 503)
                self.assertEqual(json.load(response)['reason_codes'], ['MONITOR_SOURCE_UNAVAILABLE'])


if __name__ == '__main__':
    unittest.main()
