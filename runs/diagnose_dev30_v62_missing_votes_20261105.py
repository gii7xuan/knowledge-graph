"""Read-only diagnosis of all 90 expected seats; never dispatch or recover."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
from statistics import median

from evaluation.review_samples import ReadOnlyRepository
from review_agent.contracts import sha256_json
from review_agent.response_corrections import make_plan
from review_agent.validators import _parse_content

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / 'outputs/dev30_v62_rerun_20261105'
OUT = ROOT / 'outputs/dev30_v62_missing_votes_diagnosis_20261105'
MODELS = [('judge_a', 'Qwen'), ('judge_b', 'Gemma'), ('judge_c', 'deepseek-flash')]


def stamp(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()


def main():
    repository = ReadOnlyRepository(EXPERIMENT / 'review.sqlite3')
    with repository.connect() as c:
        c.execute('BEGIN')
        audits = [dict(r) for r in c.execute('SELECT * FROM audit_runs ORDER BY created_at DESC,rowid DESC')]
        votes = [json.loads(r[0]) for r in c.execute('SELECT vote_json FROM validator_votes')]
        requests = [{**dict(r), 'metadata': json.loads(r['metadata_json']),
                     'result': json.loads(r['result_json'] or '{}')}
                    for r in c.execute('SELECT * FROM request_attempts ORDER BY started_at')]
        correction_count = c.execute('SELECT COUNT(*) FROM review_response_corrections').fetchone()[0]
        planner_attempts = [attempt for r in c.execute('SELECT record_json FROM planner_steps')
                            for attempt in json.loads(r[0]).get('attempts', [])]
    bindings = json.loads((EXPERIMENT / 'sample_bindings.json').read_text(encoding='utf-8'))
    all_seats, cases, late, failed_output = [], [], [], []
    by_model = defaultdict(list)
    payloads = []
    for binding in bindings:
        candidate_id = binding['candidate_id']
        audit = next((a for a in audits if a['candidate_id'] == candidate_id and a['revision'] == binding['revision']), None)
        cells = []
        for voter_id, model in MODELS:
            request = next((r for r in reversed(requests) if r['metadata']['binding']['task_id'] == candidate_id
                            and r['metadata']['binding']['voter_id'] == voter_id), None)
            original = next((v for v in votes if audit and v['audit_id'] == audit['audit_id'] and v['voter_id'] == voter_id), {})
            result = request['result'] if request else {}
            if original.get('response_status') == 'ok':
                kind, display = 'accepted', '有效票已记录'
            elif result.get('response_status') == 'ok':
                kind, display = 'valid_pending', '迟到有效票待采纳' if result.get('late_response') else '有效结果未汇总'
            elif result.get('http_status') == 504:
                kind, display = 'gateway_unknown', '504，执行结果未知'
            elif result.get('response_status') == 'invalid_reference':
                kind, display = 'invalid_reference', '证据ID错误，补正被停止'
            elif result.get('response_status') == 'invalid_contract':
                kind, display = 'inconsistent_opinion', '反证角色与维度意见冲突'
            elif result.get('error_code') == 'INSUFFICIENT_BALANCE':
                kind, display = 'insufficient_balance', '402，余额不足'
            elif not request:
                kind, display = 'not_started', '批次暂停后未开始'
            else:
                kind, display = 'other', result.get('error_code') or request['state']
            cell = {'sample_id': binding['sample_id'], 'candidate_id': candidate_id, 'voter_id': voter_id,
                    'model': model, 'category': kind, 'display': display,
                    'original_vote_status': original.get('response_status'), 'response_status': result.get('response_status'),
                    'error_code': result.get('error_code'), 'http_status': result.get('http_status'),
                    'audit_id': audit['audit_id'] if audit else None,
                    'attempt_id': request['attempt_id'] if request else None,
                    'late_response': bool(result.get('late_response')),
                    'correction_stop_reason': original.get('correction_stop_reason')}
            if request:
                elapsed = request['response_received_at'] - request['started_at'] if request['response_received_at'] else None
                cell.update(wait_cap_seconds=round(request['deadline_at'] - request['started_at'], 3),
                            elapsed_seconds=round(elapsed, 3) if elapsed is not None else None,
                            model_latency_ms=result.get('latency_ms'),
                            input_tokens=result.get('input_tokens'), output_tokens=result.get('output_tokens'))
                proposal = request['metadata']['proposal']
                payloads.append({'sample_id': binding['sample_id'], 'model': model,
                                 'catalog_json_chars': len(json.dumps(proposal['ontology_catalog'], ensure_ascii=False)),
                                 'evidence_chars': sum(len(e.get('quote', '')) for e in proposal['evidence_bundle']['items'])})
                if result.get('late_response'):
                    before_close = stamp(audit.get('completed_at')) - request['response_received_at'] if audit and audit.get('completed_at') else None
                    late.append({**cell, 'seconds_before_audit_end': round(before_close, 3) if before_close is not None else None})
                if kind in {'invalid_reference', 'inconsistent_opinion'}:
                    raw = _parse_content(result['raw_response'])
                    plan, errors = make_plan(raw, proposal, proposal['correction_policy'])
                    failed_output.append({**cell, 'error': result.get('error'), 'safe_field_plan_possible': bool(plan),
                                          'blocked': errors})
            cells.append(cell); all_seats.append(cell); by_model[model].append(cell)
        cases.append({'sample_id': binding['sample_id'], 'audit_status': audit['status'] if audit else 'not_started',
                      'valid_recorded_seats': sum(cell['category'] == 'accepted' for cell in cells),
                      'seats': cells})
    counts = dict(Counter(cell['category'] for cell in all_seats))
    assert len(all_seats) == 90 and len(requests) == 78
    assert counts == {'accepted': 42, 'gateway_unknown': 27, 'valid_pending': 5,
                      'inconsistent_opinion': 1, 'invalid_reference': 1,
                      'insufficient_balance': 2, 'not_started': 12}, counts
    model_summary = []
    for _, model in MODELS:
        cells = by_model[model]
        model_summary.append({'model': model, 'started': sum(c['category'] != 'not_started' for c in cells),
                              'categories': dict(Counter(c['category'] for c in cells))})
    known_usage = {k: sum(r['result'].get(k) or 0 for r in requests) for k in ('input_tokens', 'output_tokens')}
    planner_usage = {k: sum(a.get(k) or 0 for a in planner_attempts) for k in ('input_tokens', 'output_tokens')}
    unknown_usage = sum(r['result'].get('input_tokens') is None and r['result'].get('output_tokens') is None for r in requests)
    result = {'created_at': datetime.now(timezone.utc).isoformat(), 'read_only': True,
              'experiment': EXPERIMENT.name, 'candidate_population': 30, 'started': 26,
              'fully_recorded_candidates': [c['sample_id'] for c in cases if c['valid_recorded_seats'] == 3],
              'candidate_seat_counts': dict(Counter(str(c['valid_recorded_seats']) for c in cases)),
              'expected_seats': 90, 'provider_attempts': len(requests), 'seat_categories': counts,
              'model_summary': model_summary, 'late_responses': late, 'invalid_outputs': failed_output,
              'field_correction_calls': correction_count,
              'payload': {'ontology_catalog_chars': payloads[0]['catalog_json_chars'],
                          'evidence_chars_min': min(p['evidence_chars'] for p in payloads),
                          'evidence_chars_median': median(p['evidence_chars'] for p in payloads),
                          'evidence_chars_max': max(p['evidence_chars'] for p in payloads)},
              'committee_known_usage': known_usage, 'planner_known_usage': planner_usage,
              'committee_attempts_without_usage': unknown_usage,
              'cost_note': 'No currency or provider execution/billing claim is made for unknown requests.',
              'cases': cases}
    OUT.mkdir(exist_ok=True)
    (OUT / 'diagnosis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    with (OUT / 'seats.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for cell in all_seats for k in cell)))
        writer.writeheader(); writer.writerows(all_seats)
    lines = ['# 上一轮30条样本缺席原因核对', '', '核对时间：' + result['created_at'], '',
             '结论：缺席的首要原因是Qwen网关504。26条已启动候选均发起三模型请求，Qwen仅DEV-005返回有效结果，因此最多只有1条能收齐三席。模型反对或不确定意见只要满足原契约仍计入有效席位。', '',
             '|模型|已记录有效票|有效响应待采纳|504结果未知|其他失败|未开始|',
             '|---|---:|---:|---:|---|---:|',
             '|Qwen|1|0|25|无|4|', '|Gemma|18|5|2|1条反证意见冲突|4|',
             '|deepseek-flash|23|0|0|1条证据ID错误、2条余额不足|4|', '',
             '共78次已登记请求，90个预期席位。当前42个有效票已记录；其余48个席位由27个504、5个有效响应未采纳、2个无效输出、2个余额不足和12个尚未开始组成。', '',
             '## 已证实的原因', '',
             '1. Qwen的25个失败均返回Nginx HTTP504，模型调用计时为62.7至64.6秒，中位约63秒；客户端上限为90秒。一次同量级请求成功说明入口并非全部不可用。约60秒的上游等待限制是据现象作出的推断，需要服务端日志/配置才能确认是排队、推理速度还是网关配置导致，不能声称这些请求没有执行。', '',
             '2. Gemma的调用方等待上限为180秒；DEV-001、011、016、017分别在约188、189、317、225秒返回有效结果。4条原票仍记录HARD_REQUEST_TIMEOUT。001、011、017分别在审核收尾前约13、17、27秒已返回，仍未被收尾重新采纳；016在收尾后约112秒返回。这反映迟到结果对账/受控采纳的流程缺口。DEV-025另有一条正常有效响应因余额暂停未进入本轮投票表，合计5条。', '',
             '3. 请求偏重：每次发完整本体目录，序列化后99622字符，约占典型审核用户输入的87%至89%；每次输入约4.8万至6万tokens。教材证据只有81至1182字符，中位约334字符。Gemma有效响应的模型输出中位约3456 tokens，真实运行明显比合成结构探测慢。该负载可能放大延迟，但没有服务端时序日志，不能据此独立证明504的唯一成因。', '',
             '4. DEV-019的DeepSeek响应把证据ID ev_ae1313b73db3dba0094a 写成 ev_ae1313b73db3dba0094，并在11个位置重复引用。同一个ID错误被按位置计数，超过最多6个待补字段，故补正停止。这是纠错定位粒度过粗；本轮没有发起字段补正调用。', '',
             '5. DEV-022的Gemma一方面把材料标为反证，一方面将counterevidence维度判为pass。这属于已有判断冲突，按现有“不得改票”的纠错规则停止，不能当作无害格式差异强行改成有效票。', '',
             '6. DeepSeek在DEV-025、026返回HTTP402 Insufficient Balance。批次全局暂停，DEV-027至030尚未开始。两条并行候选都已发请求，所以记录了两次402。', '',
             '## 优先处理顺序', '',
             '先解决Qwen服务的网关/吞吐问题，并用一条真实体量请求验证；客户端单独加长超时不能确认能解决网关504。若切换流式，要重新验证该入口对当前严格Schema的支持，不能沿用非流式探测结论。', '',
             '其次，对5条已知有效但未采纳响应做原请求对账及受控恢复，并让收尾阶段刷新已完成请求。此步骤应复用已保存响应，不重发模型请求。', '',
             '再按实际耗时调整Gemma的总等待策略；为新实验设计本体按需上下文，保留全量查找依据与版本绑定；将重复同一无效ID的11处定位合并为一次受限补正，仍保持原判断锁定。真实反证冲突单独处理。', '',
             '余额恢复后才能推进未开始样本；27个504请求需服务端对账或明确授权的后续处理，不能当作已确认未执行直接重试。', '',
             '## 30条逐项核对', '',
             '|样本|Qwen|Gemma|DeepSeek|本轮已记录有效席位|', '|---|---|---|---|---:|']
    for case in cases:
        lines.append('|' + '|'.join([case['sample_id'], *[cell['display'] for cell in case['seats']], str(case['valid_recorded_seats']) + '/3']) + '|')
    lines += ['', '## 用量与核对边界', '',
              '委员请求已知输入/输出：' + json.dumps(known_usage, ensure_ascii=False) + '。',
              '规划器已知输入/输出：' + json.dumps(planner_usage, ensure_ascii=False) + '。',
              str(unknown_usage) + '个委员请求没有完整用量回执，未按零费用处理，也没有估算货币金额。',
              '本次仅对原数据库只读核对并生成诊断文件，没有改票、重试、恢复审核、发布本体或导入图谱。旧summary/monitor在批次暂停时停止更新，不能代替当前request_attempts的最新状态。']
    (OUT / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('started', 'fully_recorded_candidates', 'candidate_seat_counts',
                                           'seat_categories', 'field_correction_calls', 'committee_known_usage',
                                           'planner_known_usage', 'committee_attempts_without_usage')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
