"""Freeze four development revisions and review them in an isolated repository.

The completed annotation workbook and production repository are read-only inputs.
No human labels, old votes, or old reviewer rationales enter the review payload.
This is changed-field review, not production admission or a held-out evaluation.
"""
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

from document.hierarchy_index import load_document_index
from evidence.verification import make_evidence_bundle, verify_evidence_bundle
from ontology_review.delta import catalog
from review_agent.admission import load_admission_artifacts
from review_agent.case_budget import CaseBudget
from review_agent.cli import _configured_validators
from review_agent.consensus import Committee
from review_agent.contracts import sha256_json
from review_agent.human_evidence import source_span
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.progress import write_status_snapshot
from review_agent.repository import ReviewRepository, _ClosingConnection
from review_agent.revision_service import RevisionService
from review_agent.service import load_config

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/dev30_revision_pilot_20261103'
SOURCE = ROOT / 'outputs/dev30_20261031'
BOOK = SOURCE / '第一章30条开发样本_人工填写.xlsx'
EXPECTED_BOOK_HASH = '99fa28fb1efc37ccbe1e1193bd0f3fae01e2a473a054cdcb0e86bf250da63e9e'
IDS = ('DEV-004', 'DEV-013', 'DEV-017', 'DEV-020')


class ReadOnly(ReviewRepository):
    def connect(self):
        c = sqlite3.connect(self.database_path.resolve().as_uri() + '?mode=ro', uri=True,
                            factory=_ClosingConnection)
        c.row_factory = sqlite3.Row
        return c


def dump(name, value):
    write_status_snapshot(OUT / name, value)


def settings():
    return {
        'DEV-004': {
            'changes': {'qualifiers': {'condition': '弹轴与相对速度矢量重合且风速为常量'},
                        'qualifier_status': {'condition': 'extracted'}},
            'statement': '在弹轴与相对速度矢量重合且风速为常量的条件下，横风引起侧偏。',
            'reason': '原候选证据来自式（1-98）、（1-99）的推导。本次明确保留该推导的两个假设，只提出这一受限事实，不声称横风产生侧偏普遍必须满足这两个条件；不混入后续使用无风射程和时间时的小量近似，也不把任意点限定为落点。请核验新增条件是否有原文依据。',
            'spans': [('得横风引起的侧偏为', '由方程组（1-95）第一、二式看出')],
            'open_questions': ['人工未指定缺少的具体条件；上述条件是依据原推导提出的修订稿，尚不能继承L列肯定标签。'],
        },
        'DEV-013': {
            'changes': {'tail': '弹丸表面粗糙度'},
            'statement': '摩擦阻力与弹丸表面粗糙度有关。',
            'reason': '只将宾语明确为弹丸表面粗糙度，不把原文仅修饰阻力占比的亚声速/超声速条件强加给一般相关关系。现有尾类型实际为表面粗糙度，予以保留。请独立核验文字修改；不能据此宣称原有其他问题均已解决。',
            'spans': [('在亚声速时，弹丸的摩擦阻力', '# (2）涡流阻力')],
            'open_questions': ['人工F列仍为否、L列仍为否，具体缺失限定及否定原因尚未给出；保留原判，文字修订通过也不自动改为是。'],
        },
        'DEV-017': {
            'changes': {'surface_relation': '由...组成'},
            'statement': '风偏由风引起的射程偏差和方向偏差组成；本条二元候选记录其中的射程偏差这一组成项。',
            'reason': '原文是“风引起的射程偏差和方向偏差统称为风偏”。本条仍以风偏为整体、射程偏差为组成项，保留“风引起”条件。二元关系由...组成在此记录一个组成项，不断言仅由射程偏差组成；方向偏差在完整语境中保留为另一个组成项。本次不新增第二条生产候选，也不猜测正式关系ID。请核验这种二元表达是否忠实，若需改为分类等表达应给出反对。',
            'spans': [('方程组（1-95）可以用来计算', '# 3. 风速对弹道影响的物理本质')],
            'open_questions': ['正式关系ID仍待确定；完整组成说明是语境，不能把两个组成项合成一个虚构实体。', '最终文本与人工原关系建议的完整语义对应仍需确认，L列不自动绑定新稿。'],
        },
        'DEV-020': {
            'changes': {'surface_relation': '具有特征', 'tail': '章动角（或攻角）很小',
                        'value': '章动角（或攻角）很小'},
            'statement': '对于能保证飞行稳定的弹丸，总是具有“章动角（或攻角）很小”这一特征。',
            'reason': '将不完整关系改为具有特征时，宾语和字符串值同时明确为章动角（或攻角）很小。保留原条件“对于能保证飞行稳定的弹丸”和量词“总是”，保持字面量类型及中文语言，不推测主体正式类型或关系ID。请核验三个修改字段是否完整保留原意。',
            'spans': [('严格地说，弹丸在空气中的运动', '所谓质点弹道的运动规律')],
            'open_questions': ['主体正式类型、正式关系ID仍未指定；文字质量与形式准入分开处理。', 'L列仅有原字段片段对应判断，不能自动作为本次完整新稿的人类真值。'],
        },
    }


def verify_inputs(data):
    assert hashlib.sha256(BOOK.read_bytes()).hexdigest() == EXPECTED_BOOK_HASH, 'annotation workbook changed; re-read it first'
    live = ReadOnly(ROOT / 'runs/v3/review.sqlite3')
    for binding, candidate in zip(data['bindings'], data['selected_raw_candidates']):
        assert sha256_json(candidate) == binding['candidate_content_hash']
        assert sha256_json(live.get_revision(binding['candidate_id'])) == binding['candidate_content_hash'], binding['sample_id']
    return {'checked_candidates': 30, 'workbook_sha256': EXPECTED_BOOK_HASH,
            'production_candidates_changed': False, 'human_labels_written': False}


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    data = json.loads((SOURCE / 'sample_data.json').read_text(encoding='utf-8'))
    check = verify_inputs(data)
    if (OUT / 'frozen_targets.json').exists():
        frozen = json.loads((OUT / 'frozen_targets.json').read_text(encoding='utf-8'))
        assert sha256_json({k: v for k, v in frozen.items() if k != 'targets_hash'}) == frozen['targets_hash']
        for item in frozen['items']:
            assert sha256_json(item['reviewed_after_candidate']) == item['target_content_hash']
        return frozen
    original = ReadOnly(data['source_database'])
    candidates = dict(zip((b['sample_id'] for b in data['bindings']), data['selected_raw_candidates']))
    bindings = {b['sample_id']: b for b in data['bindings']}
    repo = ReviewRepository(OUT / 'development_review.sqlite3')
    repo.initialize()
    run = original.get_run(candidates[IDS[0]]['run_id'])
    repo.create_run(run)
    service = RevisionService(repo)
    document = load_document_index(run['document_path'], candidates[IDS[0]]['source_index_hash'], require_verified=True)
    assert document['source_snapshot']['canonical_text'] == data['source_canonical_text']
    text = data['source_canonical_text']
    items = []
    for sid in IDS:
        c = deepcopy(candidates[sid]); b = bindings[sid]; spec = settings()[sid]
        repo.add_candidate(c)
        audit = original.get_audit(c['candidate_id'], b['audit_id'])
        agent = (audit.get('agent_state') or {}).get('state', {})
        archive = {e['evidence_id']: e for p in audit['rounds'] for e in p['evidence_bundle']['items']}
        archive.update({e['evidence_id']: e for e in agent.get('archive', [])})
        archive.update({e['evidence_id']: e for e in c.get('evidence_items', [])})
        protected = set(agent.get('protected_ids', []))
        protected.update(eid for v in audit['votes'] if v.get('response_status') == 'ok' for eid in v.get('counter_evidence_ids', []))
        protected.update(eid for issue in audit['context'].get('issue_history', []) if issue.get('status', 'open') != 'resolved' for eid in issue.get('evidence_ids', []))
        assert protected <= archive.keys(), (sid, 'historical evidence missing')
        supporting = []
        for begin, end in spec['spans']:
            assert text.count(begin) == 1 and text.count(end) == 1, sid
            start = text.index(begin); stop = text.index(end, start)
            e = source_span(document, start, stop)
            e['retrieval_level'] = 'development_source_context'
            archive[e['evidence_id']] = e; supporting.append(e['evidence_id'])
        bundle = verify_evidence_bundle(make_evidence_bundle(archive.values(), ['frozen_audit', 'development_source_context']), document, c)
        assert not bundle['rejected_items'], (sid, bundle['rejected_items'])
        draft = service.propose(c, repo.get_candidate(c['candidate_id'])['row_version'], spec['changes'], supporting,
                                spec['reason'], bundle, document, protected_ids=protected, source_audit_id=b['audit_id'])
        beforeq = c['triple']['qualifiers']; afterq = draft['after']['triple']['qualifiers']
        for field in beforeq:
            if sid == 'DEV-004' and field == 'condition': continue
            assert beforeq[field] == afterq[field], (sid, field)
        release, _ = load_admission_artifacts(repo, c)
        terms = catalog(release.ontology)
        guard = OntologyGuard(load_snapshot(release.ontology)).check(draft['after'])
        item = {'sample_id': sid, 'base_binding': b, 'draft_id': draft['draft_id'], 'draft_hash': draft['draft_hash'],
                'changes': spec['changes'], 'complete_statement': spec['statement'],
                'reviewed_after_candidate': draft['after'], 'target_content_hash': sha256_json(draft['after']),
                'changed_fields': draft['changed_fields'], 'evidence_bundle': bundle,
                'protected_evidence_ids': sorted(protected), 'ontology_guard_after': guard,
                'actual_type_labels': {field: (terms.get(draft['after']['triple'].get(field)) or {}).get('label')
                                       for field in ('head_type', 'tail_type', 'relation_id')},
                'open_questions': spec['open_questions'], 'human_target_gold': None,
                'scope': 'development_changed_field_review_only', 'production_applied': False}
        items.append(item)
    frozen = {'schema': 'dev30_revision_targets_v1', 'created_at': datetime.now().astimezone().isoformat(),
              **check, 'items': items, 'human_labels_in_review_input': False,
              'prior_reviewer_votes_in_review_input': False, 'production_admission_authorized': False}
    frozen['targets_hash'] = sha256_json(frozen)
    dump('frozen_targets.json', frozen)
    report(frozen)
    return frozen


def report(frozen, state=None):
    lines = ['# 四条开发修订稿与独立核验', '',
             '本轮只冻结修订稿并在独立开发数据库核验修改字段。原Excel、人工原判、生产候选、历史票和本体发布状态均保留。', '',
             '新稿未获人工重新标注，human_target_gold均为空；不能把本轮模型批准率称为人工准确率。正式准入还需要新版本完整来源审核及正式ID/约束检查。', '']
    if state:
        lines.extend(['| 样本 | 修改字段审核 | 正式约束检查 |', '|---|---|---|'])
        for item in frozen['items']:
            result = next((r for r in state['results'] if r['sample_id'] == item['sample_id']), {})
            lines.append(f"| {item['sample_id']} | {result.get('status', '待审')} | {item['ontology_guard_after']['status']} |")
        lines.append('')
    reuse_path = OUT / 'DEV-017_existing_property_reuse_check.json'
    if reuse_path.exists():
        reuse = json.loads(reuse_path.read_text(encoding='utf-8'))
        lines.extend(['本体复用补查：DEV-017的两端类型与既有 `R_f99e5a41297d701d83ca`（具有部件）相容，局部约束检查通过。该属性可作为后续正式映射审核的复用目标；本轮未修改目标内容、添加别名或直接应用映射。', ''])
    for item in frozen['items']:
        sid = item['sample_id']; t = item['reviewed_after_candidate']['triple']
        lines.extend([f'## {sid}', '', item['complete_statement'], '',
                      f"结构：{t['head']} → {t['surface_relation']} → {t['tail']}；尾项类型：{t['tail_kind']}。",
                      '限定：' + json.dumps(t['qualifiers'], ensure_ascii=False),
                      '修改字段：' + '、'.join(item['changed_fields']),
                      '目标内容SHA-256：' + item['target_content_hash'],
                      '本体检查：' + item['ontology_guard_after']['status'], ''])
        clarification_path = OUT / 'DEV-013_human_clarification.json'
        if sid == 'DEV-013' and clarification_path.exists():
            lines.extend(['人工后续说明：原候选缺少“弹丸”，原“否”针对原候选这一措辞缺失。完整修订稿已明确为“摩擦阻力与弹丸表面粗糙度有关”；不再将原“否”当作新稿的负例，也不自动补成新的总体肯定标签。', ''])
        else:
            for q in item['open_questions']: lines.append('- ' + q)
        lines.extend(['', '本次补充的连续原文：', ''])
        for e in item['evidence_bundle']['items']:
            if e.get('retrieval_level') == 'development_source_context':
                lines.extend([f"`{e['evidence_id']}`，字符区间[{e['start']}, {e['end']})", '', '> ' + e['quote'].replace('\n', '\n> '), ''])
        if state:
            r = next((r for r in state['results'] if r['sample_id'] == sid), None)
            if r:
                lines.extend(['独立修改字段审核状态：' + r['status'], ''])
                for v in r.get('votes', []):
                    lines.append(f"- {v['voter_id']}：{v.get('decision', v.get('response_status'))}；{v.get('rationale_summary') or v.get('error', '')}")
                lines.append('')
    if not (OUT / 'DEV-013_human_clarification.json').exists():
        lines.extend(['人工待补充：DEV-013按照“摩擦阻力与弹丸表面粗糙度有关”修改后，仍判否的具体原因，以及所缺限定。原文明确出现这一相关关系，现有类型ID也确为表面粗糙度；两者均不替代人工解释。', ''])
    lines.extend([
                  'DEV-017的完整组成句是核对语境，实际二元目标仍只记录射程偏差这一组成项；若独立审核认为该表达不忠实，应保留反对，另起有依据的修订，而不是重抽赞成票。', '',
                  '原填写文件SHA-256：' + EXPECTED_BOOK_HASH])
    (OUT / '四条修订稿与独立核验.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def review(frozen):
    config = load_config(); voters = _configured_validators(config)
    assert len(voters) == 3, 'original three-seat committee required'
    policy = config['consensus']
    committee = Committee([v['id'] for v in voters], required_votes=policy['required_votes'], minimum_distinct_families=policy['minimum_distinct_families'])
    assert committee.required_votes == 3
    repo = ReviewRepository(OUT / 'development_review.sqlite3'); service = RevisionService(repo)
    state = {'status': 'running', 'selected': 4, 'processed': 0, 'results': [], 'production_applied': False,
             'human_gold_used': False, 'targets_hash': frozen['targets_hash']}
    dump('review_status.json', state)
    for item in frozen['items']:
        budget = CaseBudget({'max_llm_calls_per_candidate': 3, 'max_total_tokens': 100000, 'total_timeout_seconds': 420},
                            repo, [repo.ensure_case(item['base_binding']['candidate_id'])],
                            task_type='candidate_revision_review', task_id=item['draft_id'])
        result = service.review(item['draft_id'], voters, committee, budget)
        # Never apply, reroll valid opposition, or automatically repeat unknown calls.
        result['sample_id'] = item['sample_id']; result['budget'] = budget.snapshot()
        dump(item['sample_id'] + '_revision_review.json', result)
        state['results'].append(result); state['processed'] += 1
        dump('review_status.json', state); report(frozen, state)
        print(json.dumps({'sample_id': item['sample_id'], 'status': result['status'],
                          'votes': [{k: v.get(k) for k in ('voter_id', 'response_status', 'decision', 'outcome_unknown')} for v in result.get('votes', [])]}, ensure_ascii=False), flush=True)
    state['status'] = 'completed'; state['input_integrity'] = verify_inputs(json.loads((SOURCE / 'sample_data.json').read_text(encoding='utf-8')))
    dump('review_status.json', state); report(frozen, state)


def recover_known_failures(frozen):
    """One bounded format correction; preserve every valid and unknown result."""
    state = json.loads((OUT / 'review_status.json').read_text(encoding='utf-8'))
    if not (OUT / 'first_pass_review_status.json').exists():
        dump('first_pass_review_status.json', state)
    config = load_config(); voters = _configured_validators(config); policy = config['consensus']
    assert len(voters) == 3 and policy['required_votes'] == 3
    committee = Committee([v['id'] for v in voters], required_votes=3,
                          minimum_distinct_families=policy['minimum_distinct_families'])
    repo = ReviewRepository(OUT / 'development_review.sqlite3'); service = RevisionService(repo)
    for n, old in enumerate(list(state['results'])):
        if old['status'] != 'review_incomplete' or old.get('format_recovery_attempted'): continue
        failed = [v for v in old.get('votes', []) if v.get('response_status') != 'ok']
        if not failed or any(v.get('outcome_unknown') or v.get('response_status') not in
                             {'invalid_contract', 'invalid_json', 'invalid_reference'} for v in failed): continue
        if not service.recovery_available(old['draft_id']): continue
        item = next(i for i in frozen['items'] if i['sample_id'] == old['sample_id'])
        budget = CaseBudget({'max_llm_calls_per_candidate': len(failed), 'max_total_tokens': 100000,
                             'total_timeout_seconds': 420}, repo,
                            [repo.ensure_case(item['base_binding']['candidate_id'])],
                            task_type='candidate_revision_review', task_id=item['draft_id'])
        result = service.review(item['draft_id'], voters, committee, budget)
        previous_valid = {v['voter_id']: v for v in old['votes'] if v.get('response_status') == 'ok'}
        after = {v['voter_id']: v for v in result.get('votes', [])}
        assert all(after[k] == v for k, v in previous_valid.items()), 'valid vote changed'
        result.update(sample_id=old['sample_id'], format_recovery_attempted=True,
                      first_pass_status=old['status'], recovery_budget=budget.snapshot())
        dump(old['sample_id'] + '_revision_review.json', result)
        state['results'][n] = result
        dump('review_status.json', state); report(frozen, state)
        print(json.dumps({'sample_id': old['sample_id'], 'status': result['status'],
                          'valid_votes_preserved': len(previous_valid),
                          'votes': [{k: v.get(k) for k in ('voter_id', 'response_status', 'decision', 'outcome_unknown')}
                                    for v in result.get('votes', [])]}, ensure_ascii=False), flush=True)
    state['input_integrity'] = verify_inputs(json.loads((SOURCE / 'sample_data.json').read_text(encoding='utf-8')))
    dump('review_status.json', state); report(frozen, state)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--review', action='store_true')
    parser.add_argument('--recover-known-failures', action='store_true')
    args = parser.parse_args(); frozen = prepare()
    if args.review: review(frozen)
    elif args.recover_known_failures: recover_known_failures(frozen)
    else: print(json.dumps({'prepared': len(frozen['items']), 'targets_hash': frozen['targets_hash'], 'production_applied': False}, ensure_ascii=False))
