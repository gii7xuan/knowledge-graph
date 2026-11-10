"""Summarize paid frozen trials and an explicitly separate offline code revalidation."""
from collections import Counter
from datetime import datetime,timezone,timedelta
from pathlib import Path
import hashlib
import json
from evaluation.review_samples import ReadOnlyRepository
from evaluation.review_quality import repeat_consistency
from review_agent.contracts import sha256_json
from review_agent.consensus import Committee,aggregate_votes
from review_agent.validator_contracts import validate_vote
from review_agent.progress import write_status_snapshot
from runs.report_dev30_v8_20261109 import main as report

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/dev30_v8_rerun_20261109'


def main():
    repo=ReadOnlyRepository(OUT/'review.sqlite3')
    summary=json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
    assert summary['processing_closed']==30
    original=json.loads((OUT/'frozen_trace_diagnosis.json').read_text(encoding='utf-8'))
    bindings=json.loads((OUT/'sample_bindings.json').read_text(encoding='utf-8'))
    by_sample={b['sample_id']:b for b in bindings};rows=[]
    for old in original['cases']:
        binding=by_sample[old['sample_id']];audit=repo.get_audit(binding['candidate_id'],old['audit_id'])
        proposal=next(p for p in audit['rounds'] if p['proposal_digest']==old['proposal_digest'])
        votes=[v for v in audit['votes'] if v['proposal_digest']==proposal['proposal_digest']]
        policy=proposal['committee_policy'];committee=Committee([m['id'] for m in proposal['committee_members']],
            policy['required_votes'],policy['minimum_distinct_families'],proposal['committee_version'])
        consensus=aggregate_votes(votes,committee,proposal_digest=proposal['proposal_digest'],proposal=proposal)
        rows.append({'sample_id':old['sample_id'],'audit_id':old['audit_id'],'proposal_digest':old['proposal_digest'],
            'previously_affected_voters':old['affected_voter_ids'],'original_eligible_voters':old['original_eligible_voter_ids'],
            'revalidated_eligible_voters':consensus['eligible_voter_ids'],
            'revalidated_review_axes':consensus.get('review_axes'),
            'remaining_contract_errors':{v['voter_id']:validate_vote(v) for v in votes if v['response_status']=='ok'}})
    validation={'stage':'offline_revalidation_of_unchanged_original_votes','model_calls':0,
        'stored_votes_changed':False,'stored_decisions_changed':False,'admission_authorized':False,
        'affected_candidates':original['affected_candidates'],'affected_votes':original['affected_votes'],
        'preserved_final_packets':len(rows),'full_roster_after_revalidation':sum(len(r['revalidated_eligible_voters'])==3 for r in rows),
        'source_before_sha256':hashlib.sha256((OUT/'frozen_sources/review_agent/response_contract_v6_2.py').read_bytes()).hexdigest(),
        'source_after_sha256':hashlib.sha256((ROOT/'review_agent/response_contract_v6_2.py').read_bytes()).hexdigest(),'cases':rows}
    write_status_snapshot(OUT/'trace_revalidation.json',validation)
    frozen_repeat=json.loads((OUT/'frozen_repeat_report.json').read_text(encoding='utf-8'))
    repeat={**frozen_repeat,**repeat_consistency(frozen_repeat['trials_detail']),
        'comparison_stage':'post_run_contract_validation_of_original_responses','raw_trials_unchanged':True,'new_model_calls_after_fix':0}
    write_status_snapshot(OUT/'repeat_report.json',repeat)
    report()
    quality=json.loads((OUT/'quality_report.json').read_text(encoding='utf-8'))
    note=(f"本轮旧汇总受字段修复校验缺陷影响：{original['affected_candidates']}条中的{original['affected_votes']}张票被误判。"
        f"缺陷已修复；离线重验有{validation['full_roster_after_revalidation']}份提案保留三席有效票。原决定未改写，这不是修复后新的整批复跑，也不代表准入或事实正确率。")
    quality['technical_note']=note
    quality['offline_revalidation']={k:v for k,v in validation.items() if k!='cases'}
    write_status_snapshot(OUT/'quality_report.json',quality)
    comparison=json.loads((OUT/'comparison.json').read_text(encoding='utf-8'));comparison['quality']=quality
    write_status_snapshot(OUT/'comparison.json',comparison)
    preserved=json.loads((OUT/'preserved_inputs.json').read_text(encoding='utf-8'))['sha256']
    assert all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==old for name,old in preserved.items())
    production=ReadOnlyRepository(ROOT/'runs/v3/review.sqlite3')
    for binding in bindings:
        assert sha256_json(production.get_revision(binding['candidate_id']))==binding['candidate_content_hash']
        assert sha256_json(repo.get_revision(binding['candidate_id']))==binding['candidate_content_hash']
    with repo.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM import_outbox').fetchone()[0]==0
    progress=json.loads((OUT/'progress.json').read_text(encoding='utf-8'))
    status=json.loads((OUT/'launch_status.json').read_text(encoding='utf-8'))
    status.update(status='completed',sample_audits_started=30,processing_closed=30,
        independent_review_completed_in_original_run=summary['independent_review_completed'],
        main_committee_request_attempts=summary['request_attempts'],planner_calls=summary['planner_calls'],
        repeat_new_model_calls=repeat['new_model_calls'],synthetic_service_probes=6,
        elapsed_minutes=(progress['finished_at_epoch']-progress['started_at_epoch'])/60,
        completed_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
        production_candidates_unchanged=True,original_workbook_unchanged=True,previous_experiment_unchanged=True,
        post_fix_offline_revalidation_only=True,post_fix_real_model_calls=0)
    write_status_snapshot(OUT/'launch_status.json',status)
    lines=['','## 本轮结论与技术复核','',
        f"首轮30条全部运行结束：{summary['audits_finished']}条流程关闭完成，1条超时；旧汇总仅{summary['independent_review_completed']}条记为独立审核完成。",'',note,'',
        '修复按票的实际响应版本读取完整模型字段，继续校验原始判断、来源、规则哈希、字段补丁和缓存出处。没有覆盖原票、旧决定或本体数据。',
        '',f"DEV-016固定输入共{repeat['trials']}轮，其中新增两轮、{repeat['new_model_calls']}次真实请求。"]
    for voter in repeat['voters']:
        changed='不可评估' if voter['changed'] is None else '有变化' if voter['changed'] else '未发现变化'
        lines.append(f"- {voter['voter_id']}：有效{voter['valid_trials']}轮，{changed}；技术或契约失败{voter['technical_failures']}轮。检查九维状态、四维结论、规则问题类型与正式关系ID，不因说明文字变化判为漂移。")
    lines += ['','该重复检查仅有3轮，且服务只返回模型别名，不能证明后端权重版本固定；一致性不等于正确率。',
        '','当前四维人工重标仍为0/30；六项质量指标保留“暂不可评估”。本轮未自动准入、应用修订、发布本体或导入图谱。',
        '','原代码和原汇总已保存为 frozen_sources、frozen_report.md、frozen_quality_report.json、frozen_trace_diagnosis.json、frozen_repeat_report.json。',
        '','新鲜模型调用在代码修复前已结束；修复后仅离线重验原始返回。下一次完整流程对照需使用新的实验目录，不能覆盖本轮冻结记录。',
        '','生产30条候选、原人工表和旧实验库哈希/内容均保持不变。']
    with (OUT/'report.md').open('a',encoding='utf-8') as f:f.write('\n'.join(lines)+'\n')
    write_status_snapshot(OUT/'completion.json',{'status':status,'offline_revalidation':{k:v for k,v in validation.items() if k!='cases'},
        'repeat':{k:v for k,v in repeat.items() if k!='trials_detail'},'human_gold_count':0,'quality_claim':False})
    print(json.dumps({'closed':30,'original_independent_complete':summary['independent_review_completed'],
        'affected_candidates':original['affected_candidates'],'affected_votes':original['affected_votes'],
        'full_roster_in_offline_revalidation':validation['full_roster_after_revalidation'],
        'repeat':[{k:v.get(k) for k in ['voter_id','valid_trials','changed','changed_checks','changed_axes','findings_changed']} for v in repeat['voters']]},ensure_ascii=False))


if __name__=='__main__':main()
