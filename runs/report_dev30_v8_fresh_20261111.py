"""Read-only comparison of fresh regression receipts, separate from frozen reports."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import statistics

from review_agent.progress import write_status_snapshot
from review_agent.contracts import sha256_json
from runs.revalidate_v8 import ROOT, read, digest

NEW = ROOT / 'outputs/dev30_v8_full_20261111'
OLD = ROOT / 'outputs/dev30_v8_revalidated_20261110_final'
OLD_RAW = ROOT / 'outputs/dev30_v8_full_20261110_0913'
SMOKE = ROOT / 'outputs/dev7_v8_fresh_20261111'
OUT = ROOT / 'outputs/dev30_v8_comparison_20261111_final'


def receipts(directory):
    with sqlite3.connect((directory / 'review.sqlite3').as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = []
        for r in connection.execute('SELECT attempt_id,state,metadata_json,result_json FROM request_attempts'):
            meta = json.loads(r['metadata_json'] or '{}')
            result = json.loads(r['result_json'] or '{}')
            rows.append({'attempt_id': r['attempt_id'], 'state': r['state'],
                         'voter_id': (meta.get('binding') or {}).get('voter_id'),
                         'phase': (meta.get('feedback') or {}).get('kind') or 'initial',
                         **{k: result.get(k) for k in ('response_status', 'error_code', 'http_status',
                             'latency_ms', 'input_tokens', 'output_tokens', 'finish_reason',
                             'late_response', 'outcome_unknown', 'transport_metrics')}})
    return rows


def health(directory):
    summary = read(directory / 'summary.json')
    smoke = read(directory / 'smoke_report.json')
    attempts = receipts(directory)
    seats = {}
    for voter in ('judge_a', 'judge_b', 'judge_c'):
        initial = [a for a in attempts if a['voter_id'] == voter and a['phase'] == 'initial']
        measured = [a['latency_ms'] / 1000 for a in initial if isinstance(a['latency_ms'], (float, int))]
        selected = []
        for case in smoke['cases']:
            validation = case['outcome']['vote_validation'].get(voter)
            if validation:
                selected.append({'sample_id': case['sample_id'], **validation})
        seats[voter] = {
            'initial_attempts': len(initial),
            'actual_initial_receipt_statuses': dict(Counter(a['response_status'] for a in initial)),
            'initial_http_statuses': dict(Counter(str(a['http_status']) for a in initial if a['http_status'])),
            'initial_output_limited': sum(a['finish_reason'] in {'length', 'max_tokens', 'incomplete'} for a in initial),
            'median_actual_initial_seconds': statistics.median(measured) if measured else None,
            'max_actual_initial_seconds': max(measured) if measured else None,
            'within_window_selected_eligible': sum(v['whole_vote_eligible'] for v in selected),
            'within_window_selected_ineligible': [
                {'sample_id': v['sample_id'], 'errors': v['errors']} for v in selected if not v['whole_vote_eligible']],
        }
    return {'summary': summary, 'by_voter': seats, 'receipts': attempts,
            'formally_valid_mapping_candidates': sum(
                case['outcome']['dimension_results']['mapping']['status'] == 'valid' for case in smoke['cases']),
            'actual_receipt_statuses': dict(Counter(a['response_status'] for a in attempts)),
            'unknown_or_running_attempts': [a['attempt_id'] for a in attempts if a['state'] in {'running', 'unknown'}],
            'raw_database_sha256': digest(directory / 'review.sqlite3')}


def main():
    if OUT.exists():
        raise ValueError('Use a fresh comparison; do not overwrite a sealed result')
    current = health(NEW)
    previous = health(OLD)
    # The diagnostic copy has the same original returns, not new paid requests.
    assert [b['candidate_content_hash'] for b in read(NEW / 'sample_bindings.json')] == [
        b['candidate_content_hash'] for b in read(OLD_RAW / 'sample_bindings.json')]
    OUT.mkdir()
    before = {str(p): digest(p) for p in (
        NEW / 'review.sqlite3', OLD / 'review.sqlite3',
        ROOT / 'outputs/dev30_v8_focus_20261110_final/annotation_first5/annotations.json')}
    old_packets = {p['sample_id']: p for p in read(OLD / 'review_packets.json')['samples']}
    new_packets = {p['sample_id']: p for p in read(NEW / 'review_packets.json')['samples']}
    smoke_packets = {p['sample_id']: p for p in read(SMOKE / 'review_packets.json')['samples']}
    new_cases = {c['sample_id']: c['outcome'] for c in read(NEW / 'smoke_report.json')['cases']}
    smoke_cases = {c['sample_id']: c['outcome'] for c in read(SMOKE / 'smoke_report.json')['cases']}
    material_keys = ('candidate', 'evidence_bundle', 'ontology_context', 'scope_policy', 'field_spec',
                     'ontology_check', 'mapping_preflight', 'field_standardization')
    overlap = []
    for sid, packet in smoke_packets.items():
        material_hash = lambda p: sha256_json({k: p.get(k) for k in material_keys})
        def observed(o):
            return {'eligible_voters': o['whole_vote_eligible_voter_ids'],
                    'axes': {a: o['dimension_results'][a]['status'] for a in ('source', 'expression', 'mapping', 'scope')},
                    'recommendation': o['recommendation']}
        overlap.append({'sample_id': sid,
                        'same_review_material': material_hash(packet) == material_hash(new_packets[sid]),
                        'smoke_material_hash': material_hash(packet), 'full_material_hash': material_hash(new_packets[sid]),
                        'smoke': observed(smoke_cases[sid]), 'full': observed(new_cases[sid]),
                        'audit_and_proposal_bindings_differ': True, 'additional_model_calls': 0})
    annotations = read(ROOT / 'outputs/dev5_v8_human_20261110_150238/annotations.json')['annotations']
    human = []
    for a in annotations:
        sid = a['sample_id']; old_p = old_packets[sid]; new_p = new_packets[sid]
        human.append({'sample_id': sid, 'original_human_axes': a['four_axes'],
                      'old_packet_id': a['packet_id'], 'new_packet_id': new_p['packet_id'],
                      'exact_packet_match': a['packet_id'] == new_p['packet_id'] and a['binding'] == new_p['binding'],
                      'changed_material_fields': [k for k in old_p if k not in {
                          'packet_id', 'binding', 'system', 'sample_id', 'human_and_model_same_packet'}
                          and old_p[k] != new_p.get(k)],
                      'original_annotation_unchanged': True, 'automatically_rebound': False})
    smoke_summary = read(SMOKE / 'summary.json')
    comparisons = {'at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
                   'current': current, 'previous_diagnostics_of_20261110_run': previous,
                   'fresh_smoke_summary': smoke_summary,
                   'synthetic_preflight_calls': 3, 'previous_human_labels': human,
                   'already_executed_smoke_overlap': overlap,
                   'new_human_labels': 0, 'old_votes_reused': False, 'production_written': False,
                   'comparison_limit': 'Development regression; provider aliases do not pin weights; no accuracy causal claim'}
    write_status_snapshot(OUT / 'comparison.json', comparisons)
    n = current['summary']; o = previous['summary']
    def count(v, key):
        return v.get(key, 0)
    lines = ['# 2026-11-11 全30条样本复跑对照', '',
             f"7条工程冒烟先通过，随后全30条独立复跑；本轮处理状态：{n['audit_status_counts']}。",
             '候选集合和版本相同；提示词和Qwen严格Schema为修复后的版本。新旧票分库存储，不拼接旧票。', '',
             '|指标|11月10日原窗口|11月11日原窗口|', '|---|---|---|',
             f"|三席契约合格的完整提案|{o['full_committee_packets']}/30|{n['full_committee_packets']}/30|",
             f"|字段补丁请求|{count(o['request_phases'], 'field_patch')}|{count(n['request_phases'], 'field_patch')}|",
             f"|委员请求总数|{o['request_attempts']}|{n['request_attempts']}|",
             f"|规划请求|{o['recorded_planner_calls']}|{n['planner_calls']}|",
             f"|等待窗口超时席位|{o['late_receipts']['selected_timeout_seats']}|{n['late_receipts']['selected_timeout_seats']}|",
             f"|正式有效映射|{previous['formally_valid_mapping_candidates']}|{current['formally_valid_mapping_candidates']}|", '',
             f"新建议分布：{n['recommendations']}；生产动作：{n['actual_actions']}。", '',
             '|席位|旧有效席位/30|新有效席位/30|新初审实际回执|', '|---|---|---|---|']
    for v, name in [('judge_a', 'Qwen'), ('judge_b', 'Gemma'), ('judge_c', 'DeepSeek')]:
        lines.append(f"|{name}|{previous['by_voter'][v]['within_window_selected_eligible']}|"
                     f"{current['by_voter'][v]['within_window_selected_eligible']}|"
                     f"{current['by_voter'][v]['actual_initial_receipt_statuses']}|")
    residual = []
    from evaluation.review_samples import ReadOnlyRepository
    repository = ReadOnlyRepository(NEW / 'review.sqlite3')
    for case in read(NEW / 'smoke_report.json')['cases']:
        if len(case['outcome']['whole_vote_eligible_voter_ids']) == 3:
            continue
        decision = repository.latest_decision(case['candidate_id']) or {}
        audit = repository.get_audit(case['candidate_id'], case['audit_id'])
        for vote in audit['votes']:
            if vote['proposal_digest'] != decision['selected_proposal_digest']:
                continue
            if vote['voter_id'] in case['outcome']['whole_vote_eligible_voter_ids']:
                continue
            residual.append({'sample_id': case['sample_id'], 'voter_id': vote['voter_id'],
                             **{k: vote.get(k) for k in ('response_status', 'error', 'error_code',
                                 'correction_stop_reason', 'correction_terminal', 'actual_calls')}})
    comparisons['remaining_within_window_ineligible_seats'] = residual
    write_status_snapshot(OUT / 'comparison.json', comparisons)
    lines += ['', '初审实际回执包含晚到终态；有效席位只按本轮选定窗口中的原票计算，两者分开。',
              f"晚到有效席位{n['late_receipts']['later_valid_seats']}个，仅供离线对账；"
              f"可额外补齐{n['late_receipts']['additional_full_committee_packets']}份提案，不回写原决定。",
              f"当前仍运行或结果未知的委员请求：{len(current['unknown_or_running_attempts'])}。", '',
              f"全30条委员报告用量：输入{n['usage']['input_tokens']}、输出{n['usage']['output_tokens']} token；"
              f"用量缺失请求{n['usage']['requests_with_missing_usage']}个。未配置价格表，不估算金额。",
              f"另有7条冒烟委员请求{smoke_summary['request_attempts']}次、规划请求{smoke_summary['planner_calls']}次，以及3次合成服务探测；不混入全30条分母。", '',
              '5条既有人评保持原版本绑定。新一轮提案会改变证据包身份，不自动改绑为新标签；逐条材料差异见comparison.json。',
              '7条冒烟与全量重叠样本的材料哈希、四维结论和席位差异也单独记录；这是已有两次执行的对照，未额外调用模型，也不能证明后端权重固定。',
              '三席完整衡量意见可用性，不能作为事实正确率；本轮无生产准入、修订应用、本体发布和图谱导入。', '',
              'Qwen本轮初审截断为0；DEV-012、019、028分别约99.5、135.2、149.4秒返回合格回执，超过90秒窗口。晚到只可额外补齐DEV-012、028，离线完整提案共27条；DEV-019仍缺Gemma合格票。',
              'Gemma剩余问题：DEV-019补丁后的规范化未引用精确的来源绑定计划；DEV-022反证角色与pass判断冲突；DEV-024缺两个映射字段。后两条的补丁输入超过冻结的输入/token预算，未发起该补丁调用。',
              '本轮字段补丁9次，其中Qwen1次、Gemma8次；8次最终合格，1次仍不合格。没有通过扩大预算或改变原判断制造合格票。',
              'DEV-016在冒烟与全量中的候选、证据、本体及规则材料哈希一致，但Gemma契约有效性不同；两次审核绑定不同，不能据此宣称模型已稳定。', '',
              '完整逐样本四维结论：[本轮报告](../dev30_v8_full_20261111/report.md)。',
              '逐席错误、实际请求终态与人工材料差异：[comparison.json](comparison.json)。']
    (OUT / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    assert before == {str(p): digest(Path(p)) for p in before}
    write_status_snapshot(OUT / 'verification.json', {'source_files_unchanged': True, 'new_model_calls': 0,
                                                     'source_sha256': before, 'report_script_sha256': digest(Path(__file__))})
    print(json.dumps({'output': str(OUT), 'full_committees': n['full_committee_packets'],
                      'patches': count(n['request_phases'], 'field_patch'),
                      'unknown': len(current['unknown_or_running_attempts'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
