"""Read-only run comparison; old annotations never enter the experiment DB."""
from collections import Counter
from datetime import datetime,timezone,timedelta
import csv
import hashlib
import json
from pathlib import Path

from evaluation.review_samples import ReadOnlyRepository,freeze_review_sample
from review_agent.contracts import sha256_json
from review_agent.progress import write_status_snapshot
from runs.rerun_dev30_v62_20261105 import OUT,DATA,DB,BOOK,EXPECTED_BOOK,summarize


def main():
    data=json.loads(DATA.read_text(encoding='utf-8'))
    repo=ReadOnlyRepository(DB)
    summary=summarize(data,repo)
    old=json.loads(Path('outputs/dev30_analysis_20261103/annotations_scoped_v4.json').read_text(encoding='utf-8'))
    old_by_id={x['sample_id']:x for x in old['annotations']}
    live=ReadOnlyRepository('runs/v3/review.sqlite3')
    assert hashlib.sha256(BOOK.read_bytes()).hexdigest()==EXPECTED_BOOK
    for binding,c in zip(data['bindings'],data['selected_raw_candidates']):
        assert sha256_json(c)==binding['candidate_content_hash']==sha256_json(live.get_revision(c['candidate_id']))
        assert sha256_json(c)==sha256_json(repo.get_revision(c['candidate_id']))
    packets=[];comparison=[];request_rows=[]
    for result,c in zip(summary['cases'],data['selected_raw_candidates']):
        historical=old_by_id[result['sample_id']]
        decision=repo.latest_decision(c['candidate_id']) or {}
        audit=repo.get_audit(c['candidate_id'],result['audit_id']) if result['audit_id'] else {}
        issues=[]
        for v in audit.get('votes',[]):
            for ch in v.get('checks',[]):
                if ch.get('status') in {'fail','unknown'}:
                    issues.append({'voter_id':v['voter_id'],'check':ch['check'],'status':ch['status'],
                        'issue_code':ch.get('issue_code'),'note':ch.get('note'),'evidence_ids':ch.get('evidence_ids')})
        row={**result,'triple':c['triple'],
            'historical_source_result':historical.get('machine_source_result'),
            'historical_human_source':historical.get('effective_human_source_support'),
            'historical_human_scope_exclusion':historical.get('knowledge_selection_excluded'),
            'comparison_status':'qualitative_only_changed_review_packet',
            'old_evidence_labels_not_rebound':True,'issues':issues,
            'annotation_override':old['scope'].get('item_specific_overrides',{}).get(result['sample_id']),
            'decision_reason_codes':decision.get('reason_codes',[])}
        comparison.append(row)
        if decision.get('selected_proposal_digest'):
            packets.append({'sample_id':result['sample_id'],**freeze_review_sample(repo,c['candidate_id'])})
    with repo.connect() as conn:
        for r in conn.execute('SELECT * FROM request_attempts ORDER BY started_at'):
            result=json.loads(r['result_json'] or '{}')
            request_rows.append({**{k:r[k] for k in ('attempt_id','request_key','state','started_at','deadline_at','usage_state')},
                **{k:result.get(k) for k in ('model_id','response_status','error_code','error','input_tokens','output_tokens','outcome_unknown')}})
    diagnostics=Counter((r['model_id'],r['response_status'] or r['state']) for r in request_rows)
    payload={'created_at_beijing':datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'experiment_id':'dev30_v62_rerun_20261105','partition':'development','summary':{k:v for k,v in summary.items() if k!='cases'},
        'original_workbook_unchanged':True,'production_30_candidate_hashes_unchanged':True,
        'frozen_experiment_candidates_unchanged':True,'labels_used_during_execution':False,
        'interpretation':'Core relation support, complete expression, formal mapping and task scope are separate. Historical human evidence labels require new-packet adjudication; no accuracy is claimed.',
        'model_attempt_status_counts':[{'model':m,'status':s,'count':n} for (m,s),n in sorted(diagnostics.items(),key=lambda x:str(x[0]))],
        'cases':comparison,'request_attempts':request_rows}
    write_status_snapshot(OUT/'comparison.json',payload)
    write_status_snapshot(OUT/'review_packets.json',{'partition':'development','samples':packets,'manifest_hash':sha256_json(packets)})
    with (OUT/'comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['sample_id','candidate_id','revision','audit_status','independent_review_completed','historical_source_result',
            'historical_human_source','core_source_support_result','source_support_result','expression_status','mapping_status',
            'effective_mapping_status','knowledge_scope','workflow_state','stop_reason']
        w=csv.DictWriter(f,fields,extrasaction='ignore');w.writeheader();w.writerows(comparison)
    def cell(v): return str(v if v is not None else '—').replace('|','/').replace('\n',' ')
    lines=['# 30条开发样本新版重跑', '',f"更新时间：{payload['created_at_beijing']}", '',
        f"已开始 {summary['audits_started']}/30，处理结束 {summary['processing_closed']}/30，三席独立审核齐全 {summary['independent_review_completed']}/30。",'',
        '本次重跑原始冻结候选，不自动修改候选，不发布本体，不导入图谱。人工标注未提供给模型。',
        '核心来源支持、完整表达支持、正式映射和知识收录范围分开统计。证据包已升级，旧人工标签仅作定性对照，不直接计算准确率。','',
        '|样本|三席齐全|核心来源|完整表达支持|表达|有效映射|知识范围|流程|',
        '|---|---|---|---|---|---|---|---|']
    for r in comparison:
        lines.append('|'+ '|'.join(map(cell,[r['sample_id'],'是' if r['independent_review_completed'] else '否',
            r['core_source_support_result'],r['source_support_result'],r['expression_status'],r['effective_mapping_status'],r['knowledge_scope'],r['workflow_state']]))+'|')
    lines+=['','原工作簿和生产库中这30条候选的哈希已核对，均未改动。',
        '完整逐项意见见 comparison.json，冻结审核证据包见 review_packets.json。',
        '旧V5/V5.1实验继续保留；本次采用V6.2新契约和独立数据库，旧票不计入新三席，未改写历史未知请求。','',
        '请求状态及已知token用量：', '```json',json.dumps({k:summary[k] for k in ('request_attempts','attempt_state_counts','usage','planner_usage','field_corrections')},ensure_ascii=False,indent=2),'```']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(payload['summary'],ensure_ascii=False))


if __name__=='__main__':main()
