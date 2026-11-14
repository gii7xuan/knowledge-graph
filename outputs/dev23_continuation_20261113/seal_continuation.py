"""Build read-only combined receipts and pending human-review packets; no API calls."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from evaluation.continue_regression import collect_manual_holds, physical_calls
from evaluation.review_quality import annotation_template, quality_report
from evaluation.review_samples import ReadOnlyRepository
from review_agent.contracts import sha256_json
from runs.regress_v8 import ROOT, fingerprints
from runs.revalidate_v8 import digest


OUT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def stage(directory):
    repository = ReadOnlyRepository(directory / 'review.sqlite3')
    summary = read(directory / 'summary.json')
    rows = [r for r in read(directory / 'smoke_report.json')['cases'] if r['audit_id']]
    samples = {r['sample_id'] for r in rows}
    packets = [p for p in read(directory / 'review_packets.json')['samples'] if p['sample_id'] in samples]
    with repository.connect() as connection:
        committee = [json.loads(r[0] or '{}') for r in connection.execute(
            "SELECT result_json FROM request_attempts WHERE state != 'not_sent'")]
        planner = [a for r in connection.execute('SELECT record_json FROM planner_steps')
                   for a in json.loads(r[0]).get('attempts') or []
                   if not a.get('cached') and a.get('actual_calls') != 0]
        assert not connection.execute("SELECT 1 FROM audit_runs WHERE status='running' LIMIT 1").fetchone()
        assert not connection.execute("SELECT 1 FROM request_attempts WHERE state='running' LIMIT 1").fetchone()
    committee = [r for r in committee if r.get('actual_calls') != 0]
    known_invalid = []
    for row in rows:
        audit = repository.get_audit(row['candidate_id'], row['audit_id'])
        decision = repository.latest_decision(row['candidate_id'])
        for vote in audit['votes']:
            if vote['proposal_digest'] != decision.get('selected_proposal_digest'):
                continue
            validation = row['outcome']['vote_validation'][vote['voter_id']]
            if vote['response_status'] not in {'invalid_contract', 'invalid_json', 'invalid_reference', 'output_incomplete'} or validation['whole_vote_eligible']:
                continue
            known_invalid.append({'kind': 'invalid_selected_response', 'sample_id': row['sample_id'],
                'candidate_id': row['candidate_id'], 'audit_id': row['audit_id'],
                'role': vote['voter_id'], 'response_status': vote['response_status'],
                'attempt_id': vote.get('attempt_id'), 'errors': validation['errors'],
                'source_reference': str(directory / 'review.sqlite3') + '#request_attempts/' + str(vote.get('attempt_id')),
                'disposition': 'incomplete_requires_human_review', 'approval_vote': False})
    all_requests = committee + planner
    usage = {key: sum(r.get(key) or 0 for r in all_requests)
             for key in ('input_tokens', 'output_tokens')}
    usage['requests_with_missing_usage'] = sum(
        any(type(r.get(k)) is not int for k in ('input_tokens', 'output_tokens')) for r in all_requests)
    usage['scope'] = 'reported usage only; committee plus planner; missing usage is not zero cost'
    manifest = read(directory / 'code_fingerprints.json')
    with ZipFile(directory / 'execution_code_snapshot.zip') as archive:
        archive_ok = all(hashlib.sha256(archive.read(name)).hexdigest() == expected
                         for name, expected in manifest.items())
    assert archive_ok
    physical = physical_calls(repository)
    assert physical == len(all_requests)
    return {'rows': rows, 'packets': packets, 'invalid': known_invalid,
            'summary': summary, 'receipt': {
                'directory': str(directory), 'freeze_hash': read(directory / 'experiment.json')['freeze_hash'],
                'code_hash': sha256_json(manifest), 'processed': len(rows),
                'committee_requests': len(committee), 'planner_requests': len(planner),
                'physical_model_requests': physical, 'usage': usage,
                'execution_code_archive_verified': archive_ok}}


def main():
    plan = read(OUT / 'experiment.json')
    source = Path(plan['source_run'])
    assert digest(source / 'review.sqlite3') == plan['source_database_sha256']
    assert read(OUT / 'code_fingerprints.json') == fingerprints()
    old = stage(source)
    new = stage(OUT)
    original = read(source / 'sample_bindings.json')
    assert {r['candidate_id'] for r in old['rows']}.isdisjoint(r['candidate_id'] for r in new['rows'])
    rows = old['rows'] + new['rows']
    assert len(rows) == len(original) == 30
    assert {r['candidate_id'] for r in rows} == {b['candidate_id'] for b in original}
    carried = read(OUT / 'carried_manual_holds.json')
    assert carried == collect_manual_holds(ReadOnlyRepository(source / 'review.sqlite3'), original)
    current = read(OUT / 'new_manual_holds.json')
    assert current == collect_manual_holds(ReadOnlyRepository(OUT / 'review.sqlite3'), read(OUT / 'sample_bindings.json'))
    holds = carried + current
    save(OUT / 'final_unknown_manual_holds.json', {
        'status': 'incomplete_requires_human_review', 'request_count': len(holds),
        'provider_execution_reconciled': False, 'not_executed_confirmed': False,
        'automatic_resend': False, 'human_annotations_created': False, 'holds': holds})
    packets = sorted(old['packets'] + new['packets'], key=lambda p: p['sample_id'])
    quality = quality_report(packets)
    quality['execution_stages'] = [old['receipt'], new['receipt']]
    quality['human_annotations_created'] = False
    save(OUT / 'combined_quality_report.json', quality)
    save(OUT / 'combined_review_packets.json', {'samples': packets})
    save(OUT / 'combined_annotation_template.json', {'instruction': '独立审核完整材料；不可判断可保留空值；勿照抄模型结论',
                                                   'annotations': [annotation_template(p) for p in packets]})
    tasks = holds + old['invalid'] + new['invalid']
    manual = OUT / 'manual_review_all'
    manual.mkdir(exist_ok=True)
    index = []
    for packet in packets:
        parts = [t for t in tasks if t.get('candidate_id') == packet['binding']['candidate_id']]
        if not parts:
            continue
        target = manual / packet['sample_id']
        target.mkdir(exist_ok=True)
        blinded = deepcopy(packet)
        blinded.pop('system', None)
        save(target / 'review_packet.json', blinded)
        save(target / 'unresolved_parts.json', {'status': 'incomplete_requires_human_review', 'parts': parts,
            'unknown_requests_are_not_semantic_negative_votes': True, 'approval_vote': False})
        save(target / 'annotation_template.json', annotation_template(packet))
        index.append({'sample_id': packet['sample_id'], 'candidate_id': packet['binding']['candidate_id'],
                      'pending_parts': len(parts), 'directory': str(target)})
    assert sum(r['pending_parts'] for r in index) == len(tasks)
    save(manual / 'index.json', {'scope': 'unreconciled requests and selected invalid responses; not all knowledge disagreements',
        'cases': len(index), 'parts': len(tasks), 'items': index, 'human_labels_created': False})
    (manual / 'index.md').write_text(
        '# 技术未完成项人工审核清单\n\n保留结果未知状态，不推断服务端未执行，不自动重发，不计批准票。'
        '审核原文和本体材料后填写各目录的空白模板；无法判断可继续留空。\n\n'
        '|样本|未完成部分|材料|\n|---|---:|---|\n' + ''.join(
            f"|{r['sample_id']}|{r['pending_parts']}|[审核目录]({r['sample_id']}/)|\n" for r in index), encoding='utf-8')
    protection = read(OUT / 'protection_verification.json')
    assert protection['historical_file_hashes_unchanged'] and protection['production_candidate_hashes_unchanged']
    assert protection['execution_code_unchanged'] and not protection['production_written']
    calls = sum(s['receipt']['physical_model_requests'] for s in (old, new))
    assert calls <= plan['original_authorized_call_upper_bound']
    selected = sum(s['summary']['full_committee_packets'] for s in (old, new))
    late = sum(s['summary']['late_receipts']['additional_full_committee_packets'] for s in (old, new))
    receipt = {'status': 'all_30_cases_processed_with_manual_review_pending',
        'initial_processed': len(old['rows']), 'continuation_processed': len(new['rows']),
        'total_processed': len(rows), 'unstarted': 0, 'old_cases_redispatched': 0,
        'stages': [old['receipt'], new['receipt']], 'total_model_requests': calls,
        'original_unknown_requests_held': len(carried), 'new_unknown_requests_held': len(current),
        'total_unknown_request_holds': len(holds), 'remaining_invalid_response_parts': len(old['invalid'] + new['invalid']),
        'technical_manual_review_cases': len(index), 'technical_manual_review_parts': len(tasks),
        'selected_full_committee_packets': selected, 'additional_full_packets_from_original_late_receipts': late,
        'committee_material_complete_including_zero_call_late_reaggregation': selected + late,
        'recommendations': dict(Counter(r['outcome']['recommendation'] for r in rows)),
        'human_gold': 0, 'semantic_accuracy_improvement_established': False,
        'previous_database_unchanged': True, 'original_unknown_outcomes_cleared': False,
        'original_unknowns_automatically_resent': False, 'requests_settled': False,
        'strict_engineering_gate_not_overridden': True, 'engineering_gate_passed': False,
        'protection': protection, 'model_calls_during_reporting': 0,
        'production_written': False, 'graph_imported': False, 'monetary_cost': None}
    save(OUT / 'continuation_receipt.json', receipt)
    text = f'''# 原30条开发回归续跑回执

按用户要求，无法取得网关对账资料的请求保留为未完成、需人工审核，继续未启动样本。

- 原先7条、本批23条，合计30条均已处理，未启动0条；未重跑原7条。
- 原3笔、本批新增25笔，共28笔结果未知请求保留原状态，未自动重发，未当作否定或批准意见。
- 另有{receipt['remaining_invalid_response_parts']}份最终选中回复不符合契约；与未知请求合计{len(tasks)}项，涉及{len(index)}条样本，均已列入[人工审核清单](manual_review_all/index.md)。
- 两批共{calls}次真实模型请求，本批{new['receipt']['physical_model_requests']}次；本次整理报告未新增调用。
- 原时限内委员材料完整{selected}/30；已有晚到原回执可零调用补齐{late}条，合计{selected + late}/30。离线补齐未改写原票或决策，不代表四维审核全部完成。
- 模型建议：转人工{receipt['recommendations'].get('manual_review', 0)}条、等待映射{receipt['recommendations'].get('wait_mapping', 0)}条、排除{receipt['recommendations'].get('exclude', 0)}条；均未自动发布或入图。

处理结束不等于审核通过。网关执行结果仍未对账，严格工程门禁保持未通过；知识定义、映射分歧和人工真值仍按原待办处理，未宣称准确率改善。

旧数据库、历史材料与生产候选哈希均未改变；两个执行代码归档已逐项核验。

全部30条的完整材料见[合并审核包](combined_review_packets.json)，空白表见[人工标注模板](combined_annotation_template.json)，指标见[合并质量报告](combined_quality_report.json)，逐项计量与校验见[JSON回执](continuation_receipt.json)。缺失的用量不是零费用，当前不能完成金额对账。
'''
    (OUT / 'continuation_receipt.md').write_text(text, encoding='utf-8')
    print(json.dumps({k: receipt[k] for k in ('total_processed', 'total_model_requests',
        'total_unknown_request_holds', 'remaining_invalid_response_parts',
        'technical_manual_review_cases', 'technical_manual_review_parts',
        'committee_material_complete_including_zero_call_late_reaggregation', 'recommendations')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
