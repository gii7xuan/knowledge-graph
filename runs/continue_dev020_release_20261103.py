"""Semantic/ontology review and gated publication on an isolated dev release head."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from ontologyBuild.release_service import load_release
from ontology_review.contracts import versions, proposal_digest
from ontology_review.evidence import load_sources
from ontology_review.publisher import OntologyPublisher, current_release_id
from ontology_review.runtime import OntologyReviewRuntime
from ontology_review.semantic_competency import review as review_semantic, verified_result
from ontology_review.validators import judge_independently, consensus
from review_agent.case_budget import CaseBudget
from review_agent.cli import _configured_validators
from review_agent.contracts import sha256_json
from review_agent.jobs import JobRunner
from review_agent.progress import write_status_snapshot
from review_agent.repository import ReviewRepository
from review_agent.service import ReviewService, load_config

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'outputs/dev30_mapping_source_20261103'
OUT = ROOT / 'outputs/dev020_release_20261103'
RELEASES = OUT / 'ontology_releases'
EXT = 'ext_16c7db3c60544e8d92a3a70c40e2a358'
REASON = '用户要求继续020：核验当前数据属性草案、保留有效反对票及未知请求栅栏，仅在开发分支全部门槛通过后发布并重审。'


def dump(name, value): write_status_snapshot(OUT / name, value)


def readonly(path):
    c = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def prepare():
    OUT.mkdir(parents=True, exist_ok=True); RELEASES.mkdir(exist_ok=True)
    db = OUT / 'development_review.sqlite3'
    if not db.exists():
        with readonly(SOURCE / 'development_review.sqlite3') as source:
            with sqlite3.connect(db) as dest: source.backup(dest)
    repo = ReviewRepository(db); task = repo.extension_status(EXT)
    base_id = task['base_release_id']; base_source = ROOT / 'runs/ontology_releases' / base_id
    assert current_release_id(ROOT / 'runs/ontology_releases') == base_id, 'production base changed'
    if not (RELEASES / base_id).exists(): shutil.copytree(base_source, RELEASES / base_id)
    if not (RELEASES / 'release_catalog.sqlite3').exists():
        with readonly(ROOT / 'runs/ontology_releases/release_catalog.sqlite3') as source:
            with sqlite3.connect(RELEASES / 'release_catalog.sqlite3') as dest: source.backup(dest)
    config = deepcopy(load_config()); config['mode'] = 'shadow'; config['database_path'] = str(db)
    config['ontology_release_root'] = str(RELEASES)
    config.setdefault('ontology_extension', {})['auto_publish_enabled'] = False
    config['ontology_extension'].setdefault('budget', {}).update(per_call_timeout_seconds=300, total_timeout_seconds=600)
    for slot in config['validators']:
        if slot.get('family', '').casefold() in {'qwen', 'gemma'}:
            slot.update(request_timeout_seconds=300, http_timeout_seconds=300)
    voters = [{**v, 'ontology_delivery_version': 'ontology_validator_payload_v3'} for v in _configured_validators(config)]
    assert len(voters) == 3 and config['consensus']['required_votes'] == 3
    return repo, config, voters


def freeze(repo, config, voters):
    if (OUT / 'frozen_proposal.json').exists():
        p = json.loads((OUT / 'frozen_proposal.json').read_text(encoding='utf-8'))
        assert proposal_digest(p) == p['proposal_digest']; return p
    task = repo.extension_status(EXT)
    with repo.connect() as c:
        assert not c.execute("SELECT 1 FROM ontology_proposals p JOIN ontology_audits a ON a.audit_id=p.audit_id WHERE a.extension_id=? AND json_extract(p.proposal_json,'$.object_revision')=?", (EXT, task['delta_revision'])).fetchone(), 'recover existing frozen review'
    evidence, documents, candidates, cases = load_sources(repo, task)
    runtime = OntologyReviewRuntime(repo, RELEASES, voters, None, config)
    runtime.base = load_release(RELEASES / task['base_release_id']); runtime.documents = documents; runtime.candidates = candidates
    members = [{k: v[k] for k in ('id', 'model_id', 'model_family')} |
               {'model_identity': str(v.get('model_identity') or v['model_id']).casefold()} for v in voters]
    runtime.context = {'versions': versions(), 'committee_members': members,
                       'committee_policy': {k: config['consensus'][k] for k in ('committee_size', 'required_votes', 'minimum_distinct_families')},
                       'planner_model': None, 'planner_family': None, 'options_hash': sha256_json(runtime.options),
                       'mode': 'shadow', 'operator_reason': REASON, 'executed_strategy': 'prepared_delta_real_tools'}
    audit_id = repo.start_ontology_audit(EXT, runtime.context, 'ontology_audit_' + uuid4().hex)
    # Only checks run here. Each independent task charges its own durable budget.
    runtime.budget = CaseBudget({'max_tool_calls': 4, 'max_llm_calls_per_candidate': 0,
                                'max_total_tokens': 500000, 'total_timeout_seconds': 600},
                               repo, cases, task_type='ontology_extension', task_id=audit_id)
    runtime.state = {'audit_id': audit_id, 'task': task, 'delta': task['delta'], 'evidence': evidence,
                     'checks': {}, 'history': [], 'validations': {}, 'validation': None,
                     'fingerprints': {}, 'critical_issues': [], 'full_schema_searched': False,
                     'stop_reason': '', 'step_no': 0, 'prior_counter_evidence_ids': []}
    methods = [('search_existing_schema', lambda: runtime.tools.search_existing_schema('具有特征')),
               ('validate_schema_structure', runtime.tools.validate_schema_structure),
               ('analyze_schema_impact', runtime.tools.analyze_schema_impact),
               ('run_competency_checks', runtime.tools.run_competency_checks)]
    for n, (name, action) in enumerate(methods, 1):
        assert runtime.budget.consume_tool(); result = action(); runtime.state['step_no'] = n
        repo.ontology_step(audit_id, n, 'tool', {'tool': name, **result})
    assert runtime.state['checks']['structure']['status'] == runtime.state['checks']['impact']['status'] == 'pass'
    runtime.budget.usage['validation_rounds'] = 1
    p = runtime.freeze_proposal(runtime.state); repo.ontology_proposal(p); runtime.checkpoint('frozen_before_semantic_review')
    dump('frozen_proposal.json', p)
    dump('branch_manifest.json', {'created_at_beijing': datetime.now(timezone(timedelta(hours=8))).isoformat(),
                                  'source_database': str(SOURCE / 'development_review.sqlite3'),
                                  'development_database': str(repo.database_path), 'development_release_root': str(RELEASES),
                                  'production_base_release_id': task['base_release_id'], 'proposal_digest': p['proposal_digest'],
                                  'delta_hash': p['delta_hash'], 'production_mutation_authorized': False})
    return p


def run(repo, config, voters, p):
    dump('status.json', {'stage': 'semantic_competency_review', 'proposal_digest': p['proposal_digest'], 'production_applied': False})
    semantic = review_semantic(repo, RELEASES, p, voters, reason=REASON)
    dump('semantic_review.json', semantic)
    proof = verified_result(repo, RELEASES, p); dump('semantic_verified_proof.json', proof)
    print(json.dumps({'stage': 'semantic_completed', 'assessment': semantic['assessment'],
                      'seats': [{k: v.get(k) for k in ('voter_id', 'response_status', 'coverage', 'outcome_unknown')} for v in semantic['votes']]}, ensure_ascii=False), flush=True)

    # A long request with unknown outcome is not followed by another long request
    # to that same model/service. Other independent seats may still progress.
    unknown_models = {v['voter_id'] for v in semantic['votes'] if v.get('outcome_unknown')}
    audit = repo.ontology_audit(p['audit_id']); saved = {v['voter_id']: v for v in audit['votes'] if v['round_id'] == p['round_id']}
    selected = [v for v in voters if v['id'] not in unknown_models and v['id'] not in saved]
    _, _, _, cases = load_sources(repo, repo.extension_status(EXT))
    for case in cases:
        account = repo.case_status(case)
        additions = {k: max(0, account['usage'][k] + n - account['limits'][k]) for k, n in {'llm_calls': len(selected), 'tokens': 300000}.items()}
        additions = {k: n for k, n in additions.items() if n}
        if additions:
            with repo.connect() as c: cid = c.execute('SELECT candidate_id FROM review_cases WHERE case_id=?', (case,)).fetchone()[0]
            repo.authorize_case_budget(cid, additions, reason=REASON, actor='codex_dev020', authorization_key='dev020-ontology:' + p['proposal_digest'] + ':' + case)
    budget = CaseBudget({'max_llm_calls_per_candidate': max(1, len(selected)), 'max_total_tokens': 500000,
                         'total_timeout_seconds': 420}, repo, cases, task_type='ontology_extension', task_id=p['audit_id'])
    dump('status.json', {'stage': 'independent_ontology_review', 'selected_voters': [v['id'] for v in selected],
                         'deferred_unknown_model_ids': sorted(unknown_models), 'production_applied': False})
    if selected:
        assert budget.reserve_calls(len(selected))
        votes = judge_independently(selected, p, repo, retries=0, timeout=300,
                                    deadline_epoch=budget.deadline_epoch, on_vote=repo.ontology_vote)
        for vote in votes:
            for attempt in vote.get('attempts') or [vote]: budget.tokens(attempt)
            saved[vote['voter_id']] = vote
    votes = [saved[m['id']] for m in p['committee_members'] if m['id'] in saved]
    assessed = consensus(votes, p, semantic_competency=proof)
    result = {'task_type': 'ontology_extension', 'extension_id': EXT, 'audit_id': p['audit_id'],
              'delta_revision': p['object_revision'], 'recommendation': assessed['result'] if assessed['result'] in {'approve', 'reject'} else 'needs_human',
              'task_status': 'needs_human', 'selected_round_id': p['round_id'], 'selected_proposal_digest': p['proposal_digest'],
              'stop_reason': 'dev020_fixed_review_complete_or_held', 'budget': budget.snapshot(), 'release_published': False,
              'independent_review_completed': len(assessed['eligible_voter_ids']) == 3, 'consensus': assessed,
              'semantic_competency': proof, 'deferred_unknown_model_ids': sorted(unknown_models), 'mode': 'shadow'}
    current = repo.extension_status(EXT); repo.finish_ontology_audit(p['audit_id'], result, current['row_version'])
    dump('ontology_review.json', {'result': result, 'votes': votes})
    print(json.dumps({'stage': 'ontology_reviewed', 'consensus': assessed,
                      'seats': [{k: v.get(k) for k in ('voter_id', 'response_status', 'decision', 'outcome_unknown')} for v in votes]}, ensure_ascii=False), flush=True)
    if assessed['result'] != 'approve':
        dump('status.json', {'stage': 'held_at_publication_gates', 'result': result, 'production_applied': False}); return

    publisher = OntologyPublisher(repo, RELEASES, config)
    preview = publisher.preview(EXT); dump('publication_preview.json', preview)
    publication = publisher.publish(EXT, {'expected_row_version': preview['expected_row_version'],
                                        'proposal_digest': preview['proposal_digest'],
                                        'expected_base_release_id': preview['base_release_id'],
                                        'approved_by': 'user_authorized_dev020',
                                        'confirm_difference': True, 'reason': REASON})
    dump('publication.json', publication)
    runner = JobRunner(repo, ReviewService(repo, config=config).execute_audit_job, max_workers=1, job_types=['audit'])
    try: fresh = runner.run_available_once(chain=False)
    finally: runner.shutdown()
    dump('post_release_reaudit.json', fresh)
    dump('status.json', {'stage': 'development_publication_and_reaudit_completed', 'publication': publication,
                         'reaudit_job_status': fresh['status'], 'production_applied': False})


def recover_known_formats(repo, config, voters, p):
    """One bounded correction of failed quotes/IDs; valid judgments are immutable."""
    for name in ('semantic_review.json', 'ontology_review.json'):
        dest = OUT / ('first_pass_' + name)
        if not dest.exists(): dest.write_bytes((OUT / name).read_bytes())
    if (OUT / 'format_recovery.json').exists():
        print('Known format recovery already attempted; retained original judgments.'); return
    _, _, _, recovery_cases = load_sources(repo, repo.extension_status(EXT))
    for case in recovery_cases:
        with repo.connect() as c: cid = c.execute('SELECT candidate_id FROM review_cases WHERE case_id=?', (case,)).fetchone()[0]
        repo.authorize_case_budget(cid, {'tokens': 1000000}, reason=REASON + ' 为已返回的长上下文请求补齐实际token记账，并完成有限格式补正。',
                                   actor='codex_dev020', authorization_key='dev020-format-accounting:' + p['proposal_digest'] + ':' + case)
    before = json.loads((OUT / 'semantic_review.json').read_text(encoding='utf-8'))
    semantic = review_semantic(repo, RELEASES, p, voters, reason=REASON + ' 仅补正引文格式，不重投有效通过或反对。')
    old_valid = {v['voter_id']: v for v in before['votes'] if v['response_status'] == 'ok'}
    after = {v['voter_id']: v for v in semantic['votes']}
    assert all(after[k] == v for k, v in old_valid.items())
    dump('semantic_review.json', semantic); proof = verified_result(repo, RELEASES, p)
    dump('semantic_verified_proof.json', proof)
    audit = repo.ontology_audit(p['audit_id']); saved = {v['voter_id']: v for v in audit['votes'] if v['round_id'] == p['round_id']}
    valid_before = {k: v for k, v in saved.items() if v['response_status'] == 'ok'}
    selected = [v for v in voters if saved.get(v['id'], {}).get('response_status') in
                {'invalid_contract', 'invalid_json', 'invalid_reference'} and not saved[v['id']].get('outcome_unknown')]
    _, _, _, cases = load_sources(repo, repo.extension_status(EXT))
    budget = CaseBudget({'max_llm_calls_per_candidate': max(1, len(selected)), 'max_total_tokens': 400000,
                         'total_timeout_seconds': 420}, repo, cases, task_type='ontology_extension', task_id=p['audit_id'])
    if selected:
        assert budget.reserve_calls(len(selected))
        for vote in judge_independently(selected, p, repo, retries=0, timeout=300,
                                        deadline_epoch=budget.deadline_epoch, on_vote=repo.ontology_recovered_vote):
            for attempt in vote.get('attempts') or [vote]: budget.tokens(attempt)
            saved[vote['voter_id']] = vote
    assert all(saved[k] == v for k, v in valid_before.items())
    votes = [saved[m['id']] for m in p['committee_members'] if m['id'] in saved]
    # Idempotent event IDs settle any returned response whose earlier accounting
    # was interrupted after its immutable vote had already been persisted.
    for vote in votes:
        for attempt in vote.get('attempts') or [vote]: budget.tokens(attempt)
    assessed = consensus(votes, p, semantic_competency=proof)
    previous = repo.ontology_audit(p['audit_id'])['result']
    result = {**previous, 'consensus': assessed, 'semantic_competency': proof,
              'independent_review_completed': len(assessed['eligible_voter_ids']) == 3,
              'recommendation': assessed['result'] if assessed['result'] in {'approve', 'reject'} else 'needs_human',
              'stop_reason': 'dev020_known_formats_corrected_substantive_votes_preserved'}
    task = repo.extension_status(EXT); repo.finish_ontology_audit(p['audit_id'], result, task['row_version'])
    dump('ontology_review.json', {'result': result, 'votes': votes})
    dump('format_recovery.json', {'semantic_valid_votes_preserved': len(old_valid),
                                   'ontology_valid_votes_preserved': len(valid_before), 'bounded': True})
    dump('status.json', {'stage': 'held_at_publication_gates', 'result': result, 'production_applied': False})
    print(json.dumps({'semantic': semantic['assessment'], 'ontology': assessed,
                      'seats': [{k: v.get(k) for k in ('voter_id', 'response_status', 'decision', 'outcome_unknown')} for v in votes]}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--run', action='store_true')
    parser.add_argument('--recover-known-failures', action='store_true'); args = parser.parse_args()
    repo, config, voters = prepare(); p = freeze(repo, config, voters)
    if args.run: run(repo, config, voters, p)
    elif args.recover_known_failures: recover_known_formats(repo, config, voters, p)
    else: print(json.dumps({'frozen': p['proposal_digest'], 'development_release_root': str(RELEASES)}, ensure_ascii=False))
