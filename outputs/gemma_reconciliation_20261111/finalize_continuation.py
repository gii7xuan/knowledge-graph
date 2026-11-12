"""Deliver actual receipts and coverage results; no network calls or publication."""
from copy import deepcopy
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from manual_gemma_reassessment import ROOT, HERE, OUT, ATTEMPT, read, write, file_hash, now
from complete_whole_chapter import protected_unchanged
from evaluation.review_samples import ReadOnlyRepository


def actual_body(vote):
    value = vote if vote.get('response_status') == 'ok' else vote.get('raw_response', {})
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    return value if isinstance(value, dict) else {}


def main():
    completed = read(HERE / 'completion_summary.json')
    manifest = read(OUT / 'prepared_manifest.json')
    proposal = read(OUT / 'prepared_proposal.json')
    protected_unchanged(manifest)
    repository = ReadOnlyRepository(OUT / 'review.sqlite3')
    with repository.connect() as connection:
        all_rows = connection.execute('SELECT * FROM request_attempts ORDER BY started_at').fetchall()
    ledger = []
    for row in all_rows:
        metadata = json.loads(row['metadata_json'])
        if (metadata.get('proposal') or {}).get('task_id') != manifest['extension_id']:
            continue
        response = json.loads(row['result_json'] or '{}')
        ledger.append({**{k: row[k] for k in ('attempt_id', 'request_key', 'state', 'started_at',
                      'response_received_at', 'provider_request_id', 'recovery_of')},
            'task_type': metadata['task_type'], 'voter_id': metadata['binding']['voter_id'],
            'response_status': response.get('response_status'), 'decision': response.get('decision'),
            'input_tokens': response.get('input_tokens'), 'output_tokens': response.get('output_tokens'),
            'error_code': response.get('error_code'), 'http_status': response.get('http_status'),
            'response_id': response.get('response_id'), 'actual_receipt': response})
    assert len(ledger) == completed['total_calls'] <= 9
    assert file_hash(OUT / 'prepared_proposal.json') == manifest['proposal_sha256']
    assert file_hash(OUT / 'prepared_competency_request.json') == manifest['competency_request_sha256']
    modeling = repository.ontology_audit(manifest['audit_id'])['votes']
    cq = read(HERE / 'whole_chapter_result.json')
    assert len(cq['votes']) == 3
    unknown = [{k: row[k] for k in ('attempt_id', 'request_key', 'task_type', 'voter_id',
                                   'state', 'started_at', 'http_status', 'provider_request_id')}
               | {'automatic_resend_allowed': False, 'local_request_key_is_provider_id': False}
               for row in ledger if row['state'] in {'unknown', 'running'}]
    summary = {'at': now(), 'definitions_prepared': 8, 'local_guard_pass': 8,
        'new_model_calls': len(ledger), 'calls_in_this_continuation': len(ledger) - 5,
        'authorized_max_calls': 9,
        'effective_modeling_votes': [{k: v.get(k) for k in ('voter_id', 'response_status', 'decision', 'error_code', 'outcome_unknown')} for v in modeling],
        'valid_modeling_approvals': completed['modeling_approvals'],
        'semantic_competency_executed': True,
        'semantic_competency_passed': (completed.get('semantic_competency') or {}).get('status') == 'pass',
        'semantic_competency_assessment': cq['assessment'],
        'semantic_competency_votes': [{k: v.get(k) for k in ('voter_id', 'response_status', 'coverage', 'error_code', 'outcome_unknown')} for v in cq['votes']],
        'whole_chapter_call_count': sum(row['task_type'] == 'ontology_competency_review' for row in ledger),
        'remaining_unknown_requests': unknown, 'original_gemma_provider_outcome_reconciled': False,
        'manual_reassessment_authorization': read(HERE / 'manual_reassessment_authorization.json'),
        'original_gemma_attempt_and_result_preserved': read(HERE / 'manual_reassessment_result.json')['original_result_unchanged'],
        'isolated_release_published': bool(completed.get('isolated_publication')),
        'production_release_changed': False, 'production_written': False,
        'formal_definition_coverage_in_production': 0,
        'protected_inputs_unchanged': True, 'frozen_proposal_and_cq_unchanged': True,
        'costs': {'reported_input_tokens': sum(r['input_tokens'] or 0 for r in ledger),
                  'reported_output_tokens': sum(r['output_tokens'] or 0 for r in ledger),
                  'usage_missing_requests': sum(r['input_tokens'] is None or r['output_tokens'] is None for r in ledger),
                  'currency_cost': None}, 'consensus': completed['final_consensus']}
    for name in ('final_summary.json', 'report.md', 'new_request_ledger.json', 'gateway_reconciliation_required.json'):
        original = OUT / name
        backup = OUT / (original.stem + '_before_manual_continuation' + original.suffix)
        if original.exists() and not backup.exists():
            backup.write_bytes(original.read_bytes())
    write(HERE / 'final_summary.json', summary)
    write(HERE / 'all_request_receipts.json', ledger)
    write(OUT / 'final_summary.json', summary)
    write(OUT / 'new_request_ledger.json', ledger)
    write(OUT / 'gateway_reconciliation_required.json', unknown)
    write(OUT / 'progress.json', {'active': False, **summary})
    issues = []
    for vote in cq['votes']:
        body = actual_body(vote)
        issues.append({'voter_id': vote['voter_id'], 'response_status': vote['response_status'],
            'coverage': body.get('coverage'), 'unresolved_terms': body.get('unresolved_terms'),
            'assessments': body.get('assessments'), 'contract_error': vote.get('error'),
            'valid_for_committee': vote.get('response_status') == 'ok'})
    write(HERE / 'whole_chapter_findings.json', issues)
    text = ['# Gemma 对账、受控重评及三席整章审核结果', '',
        '已完成一次用户明确授权的Gemma重评及三席整章审核调用。是否达到通过门槛，以以下有效回执为准。原Gemma请求仍未完成网关侧对账，没有把未知状态记成未执行或批准。', '',
        '## 建模审核', '', '|席位|回执状态|实质决定|', '|---|---|---|']
    for vote in modeling:
        text.append(f"|{vote['model_id']}|{vote['response_status']}|{vote.get('decision') or '无有效决定'}|")
    text.extend(['', 'Qwen、DeepSeek原有效票保持不变。Gemma原请求及一次受控重评均返回504；原attempt的状态与结果正文保留，人工授权事件和新attempt记录在同一request_key下。没有改变全局自动重试规则。', '',
        '## 三席整章审核', '',
        '原问题chapter1_source_coverage、55,505字完整冻结章节和完整本体交付保持不变。基线882条RDF加8条定义、删除0条，精确重建890条并通过图哈希校验；28个定位片段没有替代全文。各席独立判断，不传入其他席位意见。', '',
        '|席位|回执状态|覆盖|能力问题结果|', '|---|---|---|---|'])
    for vote in cq['votes']:
        body = actual_body(vote)
        statuses = ', '.join(a.get('status', '') for a in body.get('assessments', [])) or '无有效决定'
        text.append(f"|{vote['model_id']}|{vote['response_status']}|{body.get('coverage') or '未知'}|{statuses}|")
    text.extend(['', f"有效整章席位：{', '.join(cq['assessment']['eligible_voter_ids']) or '无'}；通过席位：{', '.join(cq['assessment']['passed_voter_ids']) or '无'}。整章汇总：{cq['assessment']['status']}。", '',
        'whole_chapter_findings.json保存每席完整问题判断、引用和未决术语；无效契约的原始模型判断不计为有效通过。', '',
        '## 调用、审批与发布', '',
        f"本批累计{len(ledger)}/9次调用，本次续办{len(ledger)-5}次；其中受控重评1次、整章审核3次，没有自动重试。", '',
        f"建模有效批准{completed['modeling_approvals']}/3；最终共识{completed['final_consensus']['result']}；隔离版本生成：{'是' if completed.get('isolated_publication') else '否'}；生产发布头未改变。", '',
        '旧请求可能已计费且用量缺失。用户明确接受该风险后才执行一次受控重评；新的未知请求仍保留围栏，未追加重评。', '',
        f"已报告输入{summary['costs']['reported_input_tokens']}、输出{summary['costs']['reported_output_tokens']} token；{summary['costs']['usage_missing_requests']}次请求用量缺失，不将其按零计费。", '',
        '## 核验与证据', '',
        '- manual_reassessment_authorization.json：用户对未知结果一次受控重评的明确授权。',
        '- manual_claim_verification.json：副本验证只允许原请求身份、只能消费一次授权，原未知attempt保持不变；模型调用0次。',
        '- cq_mechanism_verification.json：副本模拟验证三席独立、全文及问题不变、保留有效fail、不会误判通过；网络调用0次。',
        '- all_request_receipts.json：本批全部真实尝试、原始模型回执和用量缺失。',
        '- completion_summary.json / final_summary.json：实际汇总与发布门槛。', '',
        '正式定义、证据、代码指纹及原冻结整章请求均未改。源审核库、历史标签和生产文件的保护哈希核验通过。8项定义仍以原精确补丁交付；未发布时，不宣称正式定义已生效。', ''])
    report = '\n'.join(text)
    (HERE / 'report.md').write_text(report, encoding='utf-8')
    (OUT / 'report.md').write_text(report, encoding='utf-8')
    print(json.dumps({'total_calls': len(ledger), 'modeling_approvals': completed['modeling_approvals'],
        'whole_chapter_assessment': cq['assessment'], 'unknown_requests': len(unknown),
        'published': bool(completed.get('isolated_publication'))}, ensure_ascii=False))


if __name__ == '__main__':
    main()
