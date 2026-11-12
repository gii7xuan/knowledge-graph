"""Read actual targeted rerun receipts and deliver the verified result."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from targeted_rerun import HERE, SOURCE, ROOT, MODEL_KEY, read, write, digest, now
from evaluation.review_samples import ReadOnlyRepository
from ontology_review.publisher import current_release_id, OntologyPublisher
from ontology_review import semantic_competency as cq
from ontology_review.validators import response_errors
from ontology_review.contracts import RESPONSE_SCHEMA
from runs.formal_definitions_20261111 import code_hashes


class VerifiedReadOnlyRepository(ReadOnlyRepository):
    def ensure_case(self, candidate_id, limits=None):
        with self.connect() as connection:
            row = connection.execute('SELECT case_id FROM review_cases WHERE candidate_id=?', (candidate_id,)).fetchone()
        if not row:
            raise ValueError('Expected existing frozen source case')
        return row[0]


def main():
    summary = read(HERE / 'final_summary.json')
    baseline = read(HERE / 'baseline_state.json')
    manifest = baseline['manifest']
    proposal = read(HERE / 'frozen_modeling_proposal.json')
    request = read(HERE / 'frozen_competency_request.json')
    repository = VerifiedReadOnlyRepository(HERE / 'review.sqlite3')
    receipts = read(HERE / 'new_request_receipts.json')
    assert len(receipts) <= 2 and summary['total_model_calls'] <= 11
    assert {row['task_type'] for row in receipts} == {'ontology_review', 'ontology_competency_review'}
    assert all(row['actual_receipt']['voter_id'] == 'judge_b' for row in receipts)
    for receipt in receipts:
        attempt = repository.request_attempt(receipt['attempt_id'])
        assert attempt['metadata']['proposal'] == (proposal if receipt['task_type'] == 'ontology_review' else request)
        assert attempt['result'] == receipt['actual_receipt']
        if receipt['actual_receipt']['response_status'] == 'ok':
            vote = receipt['actual_receipt']
            if receipt['task_type'] == 'ontology_review':
                assert not response_errors({key: vote[key] for key in RESPONSE_SCHEMA['required']}, proposal)
            else:
                assert not cq.errors({key: vote[key] for key in cq.SCHEMA['required']}, request)
    repaired = read(HERE / 'competency_repair_result.json')
    effective = read(HERE / 'effective_competency_result.json')
    for seat in ('judge_a', 'judge_c'):
        assert next(v for v in effective['votes'] if v['voter_id'] == seat) == next(v for v in baseline['competency_result']['votes'] if v['voter_id'] == seat)
    assert next(v for v in effective['votes'] if v['voter_id'] == 'judge_c')['assessments'][0]['status'] == 'fail'
    for old in baseline['original_modeling_attempts']:
        attempt = repository.request_attempt(old['attempt_id'])
        assert attempt['state'] == 'unknown' and attempt['result_json'] == old['result_json']
    assert digest(SOURCE / 'review.sqlite3') == baseline['source_database_sha256']
    assert all(digest(Path(path)) == value for path, value in read(SOURCE / 'protected_inputs.json').items())
    assert code_hashes() == manifest['code_fingerprints']
    assert current_release_id(ROOT / 'runs/ontology_releases') == manifest['production_release_head']
    assert current_release_id(SOURCE / 'releases') == manifest['production_release_head']
    try:
        OntologyPublisher(repository, SOURCE / 'releases').reviewed(proposal['task_id'])
    except ValueError as error:
        gate_error = str(error)
        assert 'approval quorum not satisfied' in gate_error
    else:
        raise AssertionError('The retained valid fail must prevent publication')
    verification = {'at': now(), 'new_calls': len(receipts), 'cumulative_calls': summary['total_model_calls'],
        'authorized_limit': 11, 'only_two_selected_gemma_jobs': True, 'same_frozen_inputs': True,
        'old_qwen_and_deepseek_votes_unchanged': True, 'deepseek_valid_fail_retained': True,
        'two_old_unknown_attempts_and_results_retained': True, 'source_database_unchanged': True,
        'protected_inputs_and_code_unchanged': True, 'production_and_isolated_release_heads_unchanged': True,
        'publication_gate_passed': False, 'publication_gate_error': gate_error, 'new_calls_in_verification': 0}
    write(HERE / 'delivery_verification.json', verification)
    lines = ['# Gemma定向复跑结果', '',
        f"已执行本次授权范围内的{summary['new_model_calls']}次调用，累计{summary['total_model_calls']}/11次。新隔离审核库：review.sqlite3；原9次审核库及全部历史记录保持不变。", '',
        '|项目|回执状态|决定|', '|---|---|---|',
        f"|Gemma建模重评|{summary['modeling_result']['response_status']}|{summary['modeling_result'].get('decision') or summary['modeling_result'].get('error_code') or '无有效决定'}|",
        f"|Gemma整章引用修复|{repaired['response_status']}|{', '.join(a['status'] for a in repaired.get('assessments', [])) or repaired.get('error_code') or '无有效决定'}|", '',
        '## 三席整章有效结果', '', '|席位|回执状态|覆盖|能力问题|', '|---|---|---|---|']
    for vote in effective['votes']:
        lines.append(f"|{vote['model_id']}|{vote['response_status']}|{vote.get('coverage') or '未知'}|{', '.join(a['status'] for a in vote.get('assessments', [])) or '无有效决定'}|")
    lines.extend(['', f"整章汇总：{effective['assessment']['status']}；有效建模批准{summary['valid_modeling_approvals']}/3；最终共识：{summary['final_consensus']['result']}。", '',
        'Qwen与DeepSeek已有的有效建模票、整章票完整保留；DeepSeek的有效fail没有复投或覆盖。两条旧Gemma未知请求仍为unknown，没有改成未执行或批准。', '',
        '引用修复只把Gemma本人的旧响应、错误位置及已冻结章节内唯一匹配的原文候选发送给同一模型；完整章节、完整本体和原能力问题不变，不向模型发送其他席位意见。原非法引用未由程序改成有效票。', '',
        '本次没有发布新版本，也未切换生产。已实际检查发布入口，因独立审核门槛未满足而拒绝。', '',
        f"本次已报告输入{summary['new_reported_input_tokens']}、输出{summary['new_reported_output_tokens']} token；{summary['new_usage_missing_requests']}次请求用量缺失，不能按零计费。", '',
        '证据文件：authorization.json、baseline_state.json、modeling_rerun_result.json、competency_repair_result.json、new_request_receipts.json、effective_competency_result.json、final_summary.json、delivery_verification.json。', ''])
    (HERE / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    print(verification)


if __name__ == '__main__':
    main()
