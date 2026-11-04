"""Address real review objections with a new expression and a minimal dev delta.

Preserves the complete rejected generic-property branch. No production writes.
"""
from copy import deepcopy
from importlib.util import spec_from_file_location, module_from_spec
import json
from pathlib import Path
import shutil
import sqlite3

from document.hierarchy_index import load_document_index
from evidence.verification import make_evidence_bundle, verify_evidence_bundle
from ontologyBuild.release_service import load_release
from ontology_review.delta import apply_delta
from ontology_review.evidence import load_sources
from ontology_review.publisher import current_release_id
from ontology_review.runtime import OntologyReviewRuntime
from review_agent.admission import load_admission_artifacts
from review_agent.case_budget import CaseBudget
from review_agent.cli import _configured_validators
from review_agent.consensus import Committee
from review_agent.human_evidence import source_span
from review_agent.jobs import JobRunner
from review_agent.progress import write_status_snapshot
from review_agent.proposal_resolver import selected_proposal
from review_agent.repository import ReviewRepository
from review_agent.revision_service import RevisionService
from review_agent.service import load_config, ReviewService

ROOT = Path(__file__).resolve().parents[1]
FIRST = ROOT / 'outputs/dev020_release_20261103'
OUT = FIRST / 'revision2'
RELEASES = OUT / 'ontology_releases'
CID = 'cand_52159eb442b708f4e860'
LABEL = '章动角或攻角的定性大小'


def dump(name, data): write_status_snapshot(OUT / name, data)


def main():
    OUT.mkdir(parents=True, exist_ok=True); RELEASES.mkdir(exist_ok=True)
    db = OUT / 'development_review.sqlite3'
    if not db.exists():
        with sqlite3.connect((FIRST / 'development_review.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as src:
            with sqlite3.connect(db) as dest: src.backup(dest)
    repo = ReviewRepository(db); service = RevisionService(repo)
    config = deepcopy(load_config()); config.update(mode='shadow', database_path=str(db), ontology_release_root=str(RELEASES))
    config.setdefault('ontology_extension', {})['auto_publish_enabled'] = False
    old_manifest = json.loads((FIRST / 'branch_manifest.json').read_text(encoding='utf-8'))
    base_id = old_manifest['production_base_release_id']
    if not (RELEASES / base_id).exists(): shutil.copytree(FIRST / 'ontology_releases' / base_id, RELEASES / base_id)
    if not (RELEASES / 'release_catalog.sqlite3').exists():
        with sqlite3.connect((FIRST / 'ontology_releases/release_catalog.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as src:
            with sqlite3.connect(RELEASES / 'release_catalog.sqlite3') as dest: src.backup(dest)
    base = load_release(RELEASES / base_id); voters = _configured_validators(config)
    assert len(voters) == 3
    committee = Committee([v['id'] for v in voters], required_votes=3, minimum_distinct_families=3)
    case = repo.ensure_case(CID)
    repo.authorize_case_budget(CID, {'tokens': 500000, 'llm_calls': 12, 'tool_calls': 12, 'extension_requests': 1},
                               reason='用户继续020：针对具体有效反对，修订成角度定性属性并对新版本重新审核；开发副本内保留旧票。',
                               actor='codex_dev020', authorization_key='dev020-specific-expression-v2')
    if not (OUT / 'expression_draft.json').exists():
        c = repo.get_revision(CID); assert c['revision'] == 3
        d = repo.latest_decision(CID); p, votes = selected_proposal(repo, c, d)
        assert len(votes) == 3 and all(v['source_support'] == 'supported' for v in votes)
        _, document = load_admission_artifacts(repo, c)
        previous = json.loads((ROOT / 'outputs/dev30_revision_pilot_20261103/frozen_targets.json').read_text(encoding='utf-8'))
        target = next(t for t in previous['items'] if t['sample_id'] == 'DEV-020')
        items = {e['evidence_id']: e for e in [*target['evidence_bundle']['items'], *p['evidence_bundle']['items']]}
        bundle = verify_evidence_bundle(make_evidence_bundle(items.values(), ['current_source_review', 'prior_verified_context']), document, c)
        assert not bundle['rejected_items']
        supporting = [e['evidence_id'] for e in bundle['items'] if '其章动角（或攻角）总是很小' in e['quote']]
        protected = set(target['protected_evidence_ids']) | set(c['revision_provenance']['verification_scope']['evidence_ids'])
        draft = service.propose(c, repo.get_candidate(CID)['row_version'],
                                {'surface_relation': LABEL, 'tail': '很小', 'value': '很小'}, supporting,
                                '通用具有特征被本体审核认为过泛。改成具体角度定性属性，把原宾语中的章动角/攻角移到谓语并保留值很小。保持原主体、弹丸类型、稳定飞行条件及总是量词，不推导数值阈值，不把文字改成实体。请逐字段独立核验完整语义是否保持。',
                                bundle, document, protected_ids=protected, source_audit_id=d['audit_id'])
        dump('expression_draft.json', draft)
    draft = json.loads((OUT / 'expression_draft.json').read_text(encoding='utf-8'))
    current_draft = service.draft(draft['draft_id'])
    if current_draft['status'] == 'proposed':
        budget = CaseBudget({'max_llm_calls_per_candidate': 3, 'max_total_tokens': 200000, 'total_timeout_seconds': 420},
                            repo, [case], task_type='candidate_revision_review', task_id=draft['draft_id'])
        judged = service.review(draft['draft_id'], voters, committee, budget); dump('expression_review.json', judged)
        print(json.dumps({'stage': 'specific_expression_review', 'status': judged['status']}, ensure_ascii=False), flush=True)
    current_draft = service.draft(draft['draft_id'])
    if current_draft['status'] == 'review_incomplete' and not (OUT / 'expression_format_recovery.json').exists():
        previous = current_draft.get('review_result', {})
        failed = [v for v in previous.get('votes', []) if v.get('response_status') != 'ok']
        if failed and all(v.get('response_status') in {'invalid_contract', 'invalid_reference', 'invalid_json'}
                          and not v.get('outcome_unknown') for v in failed) and service.recovery_available(draft['draft_id']):
            dump('expression_first_pass.json', previous)
            budget = CaseBudget({'max_llm_calls_per_candidate': len(failed), 'max_total_tokens': 200000, 'total_timeout_seconds': 420},
                                repo, [case], task_type='candidate_revision_review', task_id=draft['draft_id'])
            recovered = service.review(draft['draft_id'], voters, committee, budget)
            dump('expression_format_recovery.json', {'attempted': True, 'recovered': recovered['status']})
            dump('expression_review.json', recovered)
            print(json.dumps({'stage': 'specific_expression_format_recovery', 'status': recovered['status']}, ensure_ascii=False), flush=True)
    if service.draft(draft['draft_id'])['status'] not in {'approved', 'applied'}: return
    if service.draft(draft['draft_id'])['status'] == 'approved':
        applied = service.apply(draft['draft_id'], draft['draft_hash']); dump('expression_application.json', applied)
    if not (OUT / 'source_review_job.json').exists():
        runner = JobRunner(repo, ReviewService(repo, config=config).execute_audit_job, max_workers=1, job_types=['audit'])
        try: result = runner.run_available_once(chain=False)
        finally: runner.shutdown()
        dump('source_review_job.json', result)
    candidate = repo.get_revision(CID); decision = repo.latest_decision(CID)
    proposal, source_votes = selected_proposal(repo, candidate, decision)
    assert candidate['revision'] == 4 and len(source_votes) == 3 and all(v['source_support'] == 'supported' for v in source_votes)
    if decision.get('source_support_result') != 'supported':
        from review_agent.validator_contracts import versions_for
        old_audit = repo.get_audit(CID, decision['audit_id']); method = versions_for(config)
        if old_audit['context'].get('prompt_hash') != method['prompt_hash'] and not (OUT / 'direction_scope_method_review.json').exists():
            job = repo.create_job('audit', {'run_id': candidate['run_id'], 'candidate_ids': [CID], 'mode': 'shadow',
                                           'force': False, 'review_method': 'source_direction_scope_v1'})
            runner = JobRunner(repo, ReviewService(repo, config=config).execute_audit_job, max_workers=1, job_types=['audit'])
            try: reviewed = runner.run_available_once(chain=False)
            finally: runner.shutdown()
            dump('direction_scope_method_review.json', {'old_audit_id_preserved': old_audit['audit_id'],
                                                        'new_method': method, 'job': reviewed})
            decision = repo.latest_decision(CID); proposal, source_votes = selected_proposal(repo, candidate, decision)
    if decision.get('source_support_result') != 'supported':
        dump('source_gate_hold.json', {'audit_id': decision['audit_id'], 'source_support_result': decision.get('source_support_result'),
                                       'old_votes_preserved': True, 'no_extension_or_publication_bypass': True})
        print('Current source consensus remains unresolved; no extension/publication bypass.', flush=True); return
    task_id = repo.get_candidate(CID)['pending_extension_id']
    if not task_id:
        audit = repo.get_audit(CID, decision['audit_id']); state = audit['agent_state']['state']
        lookup = state.get('ontology_lookup')
        assert lookup and lookup['scope'] == 'full_frozen_ontology' and lookup['candidate_revision'] == candidate['revision']
        support_ids = sorted({eid for v in source_votes for eid in v.get('supporting_evidence_ids', [])})
        result = repo.create_extension_request(candidate, expected_row_version=repo.get_candidate(CID)['row_version'],
            proposal=proposal, change_kind='add_data_property', description='完整独立来源共识及全本体检索后，申请具体章动角/攻角定性大小属性。',
            proposed_changes={'label': LABEL, 'domain': [candidate['triple']['head_type']], 'datatype': 'string'},
            evidence_ids=support_ids, full_lookup=lookup)
        task_id = result['task']['extension_id']
    assert task_id != 'ext_16c7db3c60544e8d92a3a70c40e2a358'
    task = repo.extension_status(task_id)
    dump('specific_source_result.json', {'candidate': candidate, 'decision': decision, 'extension_id': task_id,
                                         'source_votes': source_votes, 'source_proposal_digest': proposal['proposal_digest']})
    print(json.dumps({'stage': 'specific_source_review', 'revision': 4, 'valid_source_supported_votes': len(source_votes), 'extension_id': task_id}, ensure_ascii=False), flush=True)

    if not task.get('delta'):
        document = load_document_index(repo.get_run(candidate['run_id'])['document_path'], candidate['source_index_hash'], require_verified=True)
        text = document['source_snapshot']['canonical_text']
        air_start = text.index('由于弹丸飞行过程中表面上的边界层不断形成')
        air_end = text.index('由于弹丸速度很高', air_start)
        mass_start = text.index('在弹丸设计理论中，有一个弹丸质量系数')
        mass_end = text.index('等。', mass_start) + 2
        extra = [source_span(document, air_start, air_end), source_span(document, mass_start, mass_end)]
        evidence = {e['evidence_id']: e for e in [*proposal['evidence_bundle']['items'], *extra]}
        verified = verify_evidence_bundle(make_evidence_bundle(evidence.values(), ['specific_source', 'review_objection_investigation']), document, candidate)
        assert not verified['rejected_items']
        primary = [e['evidence_id'] for e in verified['items'] if '其章动角（或攻角）总是很小' in e['quote']]
        operations = [
            {'kind': 'add_data_property', 'target': None, 'changes': {
                'label': LABEL, 'label_language': 'zh',
                'definition': '记录弹丸章动角（或攻角）大小的原文定性描述。飞行状态、适用条件及量词随具体断言保留；不规定所有弹丸都有该属性，也不把“很小”改写为角度数值或阈值。',
                'definition_language': 'zh', 'domain': ['C_85d7c2e6732f8a304f88'], 'datatype': 'string'}, 'evidence_ids': primary},
            {'kind': 'revise_constraint', 'target': 'R_2a66075367666d54e7e7',
             'changes': {'range': ['C_a7877ae0fe8183f8b986']}, 'evidence_ids': [extra[0]['evidence_id']]},
            {'kind': 'revise_definition', 'target': 'R_2a66075367666d54e7e7',
             'changes': {'definition': '描述弹丸边界层形成时，沿途近表面的空气被带动而消耗弹丸动能的关系。', 'definition_language': 'zh'},
             'evidence_ids': [extra[0]['evidence_id']]},
        ]
        delta = apply_delta(base, operations, set(evidence))
        updated = repo.extension_update(task_id, task['row_version'], changes={'delta': delta, 'review_evidence': verified['items'],
            'operator_revision_reason': '具体化角度定性属性，修复前次全章能力核验指出并经原文确认的空气消耗关系range错误；质量系数关系有直接原文，不因错误反对修改。',
            'previous_rejected_generic_extension': 'ext_16c7db3c60544e8d92a3a70c40e2a358'}, status='in_review')
        dump('revised_delta.json', delta); dump('revised_evidence.json', verified)
        dump('revision_rationale.json', {'old_generic_votes_preserved': True, 'new_semantic_content': True,
            'specific_property': LABEL, 'old_property_not_published': True,
            'base_relationship_fix': {'id': 'R_2a66075367666d54e7e7', 'old_range': '弹丸', 'new_range': '动能'},
            'mass_coefficient_claim': '原文明确同类型弹丸具有变化范围不大的质量系数，保留已有关系。', 'production_applied': False})
    spec = spec_from_file_location('dev020_release_helper', ROOT / 'runs/continue_dev020_release_20261103.py')
    helper = module_from_spec(spec); spec.loader.exec_module(helper)
    helper.OUT = OUT; helper.RELEASES = RELEASES; helper.SOURCE = FIRST; helper.EXT = task_id
    config['ontology_extension'].setdefault('budget', {}).update(per_call_timeout_seconds=300, total_timeout_seconds=600, max_proposal_chars=500000)
    for slot in config['validators']:
        if slot.get('family', '').casefold() in {'qwen', 'gemma'}: slot.update(request_timeout_seconds=300, http_timeout_seconds=300)
    ontology_voters = [{**v, 'ontology_delivery_version': 'ontology_validator_payload_v3'} for v in _configured_validators(config)]
    frozen = helper.freeze(repo, config, ontology_voters)
    helper.run(repo, config, ontology_voters, frozen)


if __name__ == '__main__': main()
