"""Review mappings and run the current source Agent on four development revisions.

All writes stay in a new development repository. Production, the completed
workbook and the previous pilot repository remain read-only inputs.
"""
import argparse
from copy import deepcopy
from datetime import datetime
from importlib.util import spec_from_file_location, module_from_spec
import json
from pathlib import Path
import sqlite3

from review_agent.admission import load_admission_artifacts
from review_agent.case_budget import CaseBudget
from review_agent.cli import _configured_validators
from review_agent.consensus import Committee
from review_agent.contracts import sha256_json
from review_agent.jobs import JobRunner
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.progress import write_status_snapshot
from review_agent.repository import ReviewRepository, NotFoundError
from review_agent.revision_service import RevisionService
from review_agent.service import ReviewService, load_config

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/dev30_mapping_source_20261103'
PREVIOUS = ROOT / 'outputs/dev30_revision_pilot_20261103'
spec = spec_from_file_location('dev4_pilot', ROOT / 'runs/prepare_dev30_revision_pilot_20261103.py')
pilot = module_from_spec(spec); spec.loader.exec_module(pilot)


def dump(name, value):
    write_status_snapshot(OUT / name, value)


def inputs():
    data = json.loads((pilot.SOURCE / 'sample_data.json').read_text(encoding='utf-8'))
    return pilot.verify_inputs(data)


def prepare():
    OUT.mkdir(parents=True, exist_ok=True); inputs()
    frozen = json.loads((PREVIOUS / 'frozen_targets.json').read_text(encoding='utf-8'))
    assert sha256_json({k: v for k, v in frozen.items() if k != 'targets_hash'}) == frozen['targets_hash']
    db = OUT / 'development_review.sqlite3'
    if not db.exists():
        with sqlite3.connect((PREVIOUS / 'development_review.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(db) as target: source.backup(target)
    repo = ReviewRepository(db); service = RevisionService(repo)
    if (OUT / 'mapping_targets.json').exists():
        return json.loads((OUT / 'mapping_targets.json').read_text(encoding='utf-8'))
    revised = []
    reasons = {
        'DEV-017': '保留已拟定的由...组成表面表述，新增正式关系映射到现有R_f99e5a41297d701d83ca。实际属性具有部件，domain为风偏，range为射程偏差，方向为整体到组成项。请独立核验完整原文及映射语义；约束相容并不自动证明语义相容。二元关系只记录射程偏差这一组成项，不声称排除方向偏差；不新增同义别名、不改本体。',
        'DEV-020': '保留具有特征—章动角（或攻角）很小的完整文字修订，以及条件、总是量词、字面量值。只补充主体类型为正式类弹丸C_85d7c2e6732f8a304f88，其实际定义和原文均提供给你；原文主体明确称为能保证飞行稳定的弹丸。不得由属性domain推断类型，也不得据此声称属性映射已齐全；当前本体没有数据属性，正式关系ID仍留空等待本体扩展。请逐字段核验，依据不足时反对或保留不确定。',
    }
    for item in frozen['items']:
        if item['sample_id'] not in reasons: continue
        sid = item['sample_id']; candidate = repo.get_revision(item['base_binding']['candidate_id'])
        assert sha256_json(candidate) == item['base_binding']['candidate_content_hash']
        changes = deepcopy(item['changes'])
        if sid == 'DEV-017': changes['relation_id'] = 'R_f99e5a41297d701d83ca'
        else: changes['head_type'] = 'C_85d7c2e6732f8a304f88'
        _, document = load_admission_artifacts(repo, candidate)
        old = service.draft(item['draft_id'])
        draft = service.propose(candidate, repo.get_candidate(candidate['candidate_id'])['row_version'], changes,
                                old['evidence_ids'], reasons[sid], item['evidence_bundle'], document,
                                protected_ids=item['protected_evidence_ids'], source_audit_id=old.get('source_audit_id'))
        revised.append({'sample_id': sid, 'candidate_id': candidate['candidate_id'], 'draft_id': draft['draft_id'],
                        'draft_hash': draft['draft_hash'], 'after_content_hash': sha256_json(draft['after']),
                        'complete_statement': item['complete_statement'], 'changed_fields': draft['changed_fields'],
                        'changes': changes, 'after': draft['after']})
    plan = {'created_at': datetime.now().astimezone().isoformat(), 'previous_targets_hash': frozen['targets_hash'],
            'items': revised, 'production_applied': False, 'human_labels_in_review_input': False}
    plan['mapping_targets_hash'] = sha256_json(plan); dump('mapping_targets.json', plan)
    return plan


def review_mappings(plan):
    config = load_config(); voters = _configured_validators(config); policy = config['consensus']
    assert len(voters) == 3 and policy['required_votes'] == 3 and policy['minimum_distinct_families'] == 3
    committee = Committee([v['id'] for v in voters], required_votes=3, minimum_distinct_families=3)
    repo = ReviewRepository(OUT / 'development_review.sqlite3'); service = RevisionService(repo)
    state = {'status': 'running', 'results': [], 'production_applied': False}
    if (OUT / 'mapping_review_status.json').exists(): state = json.loads((OUT / 'mapping_review_status.json').read_text(encoding='utf-8'))
    for item in plan['items']:
        previous = next((r for r in state['results'] if r['sample_id'] == item['sample_id']), None)
        if previous: continue  # Never reroll a valid negative or an unknown response.
        budget = CaseBudget({'max_llm_calls_per_candidate': 3, 'max_total_tokens': 100000, 'total_timeout_seconds': 420},
                            repo, [repo.ensure_case(item['candidate_id'])], task_type='candidate_revision_review', task_id=item['draft_id'])
        result = service.review(item['draft_id'], voters, committee, budget)
        result['sample_id'] = item['sample_id']; result['first_pass_budget'] = budget.snapshot()
        dump(item['sample_id'] + '_mapping_first_pass.json', result)
        failed = [v for v in result.get('votes', []) if v.get('response_status') != 'ok']
        if (result['status'] == 'review_incomplete' and failed and
                all(not v.get('outcome_unknown') and v.get('response_status') in
                    {'invalid_contract', 'invalid_json', 'invalid_reference'} for v in failed) and service.recovery_available(item['draft_id'])):
            valid = {v['voter_id']: v for v in result['votes'] if v['response_status'] == 'ok'}
            retry_budget = CaseBudget({'max_llm_calls_per_candidate': len(failed), 'max_total_tokens': 100000,
                                       'total_timeout_seconds': 420}, repo, [repo.ensure_case(item['candidate_id'])],
                                      task_type='candidate_revision_review', task_id=item['draft_id'])
            result = service.review(item['draft_id'], voters, committee, retry_budget)
            after = {v['voter_id']: v for v in result.get('votes', [])}
            assert all(after[k] == v for k, v in valid.items())
            result.update(sample_id=item['sample_id'], format_correction_only=True)
        dump(item['sample_id'] + '_mapping_review.json', result)
        state['results'].append(result); dump('mapping_review_status.json', state)
        print(json.dumps({'sample_id': item['sample_id'], 'mapping_review': result['status'],
                          'votes': [{k: v.get(k) for k in ('voter_id', 'decision', 'response_status', 'outcome_unknown')}
                                    for v in result.get('votes', [])]}, ensure_ascii=False), flush=True)
    state['status'] = 'completed'; dump('mapping_review_status.json', state)


def apply_and_audit(plan):
    repo = ReviewRepository(OUT / 'development_review.sqlite3'); service = RevisionService(repo)
    frozen = json.loads((PREVIOUS / 'frozen_targets.json').read_text(encoding='utf-8'))
    mappings = {i['sample_id']: i for i in plan['items']}
    application = {'results': [], 'production_applied': False, 'development_only': True}
    if (OUT / 'application_status.json').exists(): application = json.loads((OUT / 'application_status.json').read_text(encoding='utf-8'))
    for item in frozen['items']:
        sid = item['sample_id']
        if any(r['sample_id'] == sid for r in application['results']): continue
        selected = mappings.get(sid, item); draft = service.draft(selected['draft_id'])
        if draft['status'] != 'approved':
            application['results'].append({'sample_id': sid, 'status': draft['status'], 'applied': False})
            dump('application_status.json', application); continue
        result = service.apply(draft['draft_id'], draft['draft_hash']); result['sample_id'] = sid
        # Consolidate only these development jobs into one bounded, leased batch.
        repo.request_cancel(result['reaudit_job_id'])
        candidate = repo.get_revision(item['base_binding']['candidate_id'])
        result.update(candidate_id=candidate['candidate_id'], applied=True,
                      applied_candidate_content_hash=sha256_json(candidate))
        application['results'].append(result); dump('application_status.json', application)
    applied = [r['candidate_id'] for r in application['results'] if r.get('applied')]
    if not applied: return
    config = deepcopy(load_config()); config['database_path'] = str(repo.database_path); config['mode'] = 'shadow'
    config.setdefault('routing', {})['auto_accept_enabled'] = False
    config.setdefault('ontology_extension', {})['auto_publish_enabled'] = False
    # Preserve current planner, tool and independent committee contracts.
    assert config['planner']['enabled'] and config['planner']['strategy'] == 'llm_react'
    if not (OUT / 'source_batch_job.json').exists():
        candidate = repo.get_revision(applied[0])
        job = repo.create_job('audit', {'run_id': candidate['run_id'], 'candidate_ids': applied,
                                       'mode': 'shadow', 'force': False,
                                       'status_file': str(OUT / 'source_batch_progress.json')})
        dump('source_batch_job.json', {'job_id': job['job_id'], 'candidate_ids': applied})
    job_ref = json.loads((OUT / 'source_batch_job.json').read_text(encoding='utf-8'))
    job = repo.get_job(job_ref['job_id'])
    if job['status'] == 'failed' and '[WinError 5]' in str(job.get('progress', {}).get('error', '')):
        with repo.connect() as connection:
            count = connection.execute('SELECT COUNT(*) FROM audit_runs').fetchone()[0]
            requests = connection.execute("SELECT COUNT(*) FROM request_attempts WHERE json_extract(metadata_json,'$.task_type')='triple_review'").fetchone()[0]
        if count == 0 and requests == 0:
            # Windows IPC was denied before creating any audit or source request.
            dump('source_batch_pre_dispatch_failure.json', job)
            replacement = repo.create_job('audit', {**job['payload'], 'execution_recovery': 'pre_dispatch_windows_ipc_permission'})
            job_ref = {'job_id': replacement['job_id'], 'candidate_ids': applied,
                       'replaces_pre_dispatch_failed_job': job['job_id']}
            dump('source_batch_job.json', job_ref); job = replacement
    if job['status'] == 'queued':
        runner = JobRunner(repo, ReviewService(repo, config=config).execute_audit_job, max_workers=1, job_types=['audit'])
        try: job = runner.run_available_once(chain=False)
        finally: runner.shutdown()
    dump('source_batch_result.json', job)
    final = []
    for identity in applied:
        current = repo.get_candidate(identity); decision = repo.latest_decision(identity) or {}
        try: audit = repo.get_audit(identity)
        except NotFoundError: audit = {'audit_id': None, 'context': {}}
        final.append({'candidate_id': identity, 'revision': current['revision'],
                      'source_support_result': decision.get('source_support_result'),
                      'workflow_state': current.get('workflow_state'), 'pending_extension_id': current.get('pending_extension_id'),
                      'audit_id': audit['audit_id'], 'independent_review_completed': audit['context'].get('independent_review_completed'),
                      'review_completion_status': audit['context'].get('review_completion_status')})
    dump('source_summary.json', {'results': final, 'production_applied': False, 'input_integrity': inputs()})
    print(json.dumps({'source_results': final, 'production_applied': False}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--run', action='store_true'); args = parser.parse_args()
    plan = prepare()
    if args.run:
        review_mappings(plan); apply_and_audit(plan)
    else: print(json.dumps({'prepared_mapping_targets': len(plan['items']), 'production_applied': False}, ensure_ascii=False))
