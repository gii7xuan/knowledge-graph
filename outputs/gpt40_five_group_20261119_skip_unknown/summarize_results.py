"""Local final analysis of inherited results plus the remaining candidates."""
from collections import Counter
from contextlib import closing
from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
import random
import sqlite3

P=Path(__file__).resolve().parent
ROOT=P.parents[1]
read=lambda path:json.loads(path.read_text(encoding='utf-8'))


def ledgers(paths):
    result={}
    for path in paths:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as connection:
            for attempt,state,recovery,started,provider,metadata,raw in connection.execute('SELECT attempt_id,state,recovery_of,started_at,provider_request_id,metadata_json,result_json FROM request_attempts'):
                m=json.loads(metadata);p=m.get('proposal') or {};r=json.loads(raw or '{}')
                result[attempt]={'attempt_id':attempt,'state':state,'recovery_of':recovery,
                    'candidate_id':p.get('candidate_id') or p.get('candidate',{}).get('candidate_id'),
                    'task_type':m.get('task_type'),'voter_id':m.get('binding',{}).get('voter_id'),
                    'model_id':m.get('binding',{}).get('model_id'),'started_at':started,'provider_request_id':provider,
                    **{k:r.get(k) for k in ('response_status','input_tokens','output_tokens','not_sent','error','response_id','gateway_request_id','transport_metrics')}}
    return result


def paired(left,right,refs,axis,excluded):
    differences=[];count=Counter()
    for a,b,ref in zip(left,right,refs):
        if a['candidate_id'] in excluded or a['status']=='not_started' or b['status']=='not_started' or ref['labels'][axis]=='unresolved':continue
        expected=ref['labels'][axis];av=a['axis_labels'].get(axis,'unresolved');bv=b['axis_labels'].get(axis,'unresolved')
        count['agent_match_only']+=bv==expected and av!=expected
        count['b3_match_only']+=av==expected and bv!=expected
        count['both_match']+=av==bv==expected
        count['both_resolved']+=av!='unresolved' and bv!='unresolved'
        count['both_unresolved']+=av==bv=='unresolved'
        differences.append(int(bv==expected)-int(av==expected))
    rng=random.Random(20261119)
    draws=sorted(sum(rng.choices(differences,k=len(differences)))/len(differences) for _ in range(10000)) if differences else []
    return {'paired_reference_known':len(differences),**count,
        'matching_coverage_difference_agent_minus_b3':sum(differences)/len(differences) if differences else None,
        'candidate_bootstrap_95_interval':[draws[249],draws[9749]] if draws else None,
        'interpretation':'GPT matching coverage, not human accuracy; source clustering not corrected'}


def main():
    if not (P/'finished.json').exists():raise RuntimeError('wait for the original dispatcher to finish')
    protocol=read(P/'protocol.json');report=read(P/'comparison_report.json');resume=protocol['resume'];prior=Path(resume['prior_directory'])
    refs=read(P/'gpt_reference.json');inputs=read(P/'model_inputs.json')
    groups={g:read(P/(g+'_predictions.json')) for g in report['groups']}
    skipped=set(report['usage']['quarantined_candidate_ids']);cohort={r['candidate']['candidate_id'] for r in inputs}-skipped
    executed={a['candidate_id'] for a,b in zip(groups['b3_fixed_workflow'],groups['agent']) if a['candidate_id'] in cohort and a['status']!='not_started' and b['status']!='not_started'}
    prior_paths=[Path(name) for name in resume['database_sha256']]
    new_paths=list(P.glob('case_*/*.sqlite3'));quality_paths=list(prior.glob('case_*/*.sqlite3'))+new_paths
    all_paths=prior_paths+new_paths;records=ledgers(all_paths);quality_records=ledgers(quality_paths)
    physical={k:r for k,r in records.items() if not r['not_sent']}
    engineering={k:r for k,r in physical.items() if k not in quality_records}
    known_tokens=sum((r['input_tokens'] or 0)+(r['output_tokens'] or 0) for r in physical.values())
    assert len(physical)==report['usage']['unique_provider_attempts'] and known_tokens==report['usage']['known_tokens']
    inherited={g:read(prior/(g+'_predictions.json')) for g in groups}
    done=set(resume['completed_sample_ids'])
    unchanged=all(row==old for g,rows in groups.items() for row,old in zip(rows,inherited[g]) if row['sample_id'] in done)
    bound=all(r['binding']==ref['binding'] for rows in groups.values() for r,ref in zip(rows,refs['annotations']))
    code=read(P/'code_fingerprints.json')
    initial={}
    for key,r in physical.items():
        if r['task_type']=='triple_review':initial.setdefault((r['candidate_id'],r['voter_id']),set()).add(key)
    unknown=[r for r in physical.values() if r['state'] in {'running','unknown'}]
    verification={'planned_population':40,'comparison_cohort':len(cohort),'skipped_unknown_candidates':len(skipped),
        'paired_branches_processed':len(executed),'prior_unknown_requests_resent':0,
        'all_predictions_preserved_population':all(len(rows)==40 for rows in groups.values()),'candidate_reference_bindings_unchanged':bound,
        'prior_completed_predictions_unchanged':unchanged,
        'prior_databases_unchanged':all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in resume['database_sha256'].items()),
        'prior_artifacts_unchanged':all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in resume['artifact_sha256'].items()),
        'frozen_code_unchanged':all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in code.items()),
        'gpt_source_unchanged':hashlib.sha256(Path(refs['source_path']).read_bytes()).hexdigest()==refs['source_sha256'],
        'initial_request_pairs':len(initial),'initial_requests_repeated':sum(len(ids)-1 for ids in initial.values()),
        'retry_or_recovery_attempts':sum(bool(r['recovery_of']) for r in physical.values()),
        'unknown_requests':len(unknown),'unique_provider_requests':len(physical),'known_tokens':known_tokens,
        'new_provider_requests':len(physical)-resume['prior_unique_requests'],'local_request_accounting_reconciled':True}
    assert all(verification[k] for k in ('all_predictions_preserved_population','candidate_reference_bindings_unchanged','prior_completed_predictions_unchanged','prior_databases_unchanged','prior_artifacts_unchanged','frozen_code_unchanged','gpt_source_unchanged'))
    assert not verification['initial_requests_repeated'] and not verification['retry_or_recovery_attempts']
    outbox=0;drafts=0
    for path in all_paths:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as connection:
            outbox+=connection.execute('SELECT COUNT(*) FROM import_outbox').fetchone()[0]
            drafts+=connection.execute('SELECT COUNT(*) FROM revision_drafts').fetchone()[0]
    verification.update(isolated_import_outbox_entries=outbox,revision_drafts=drafts)
    assert outbox==0 and drafts==0
    paired_results={axis:paired(groups['b3_fixed_workflow'],groups['agent'],refs['annotations'],axis,skipped) for axis in ('source_support','expression_status')}
    replay=[r for r in read(P/'weight_replay.json') if r.get('candidate_id') in cohort and 'modes' in r]
    weight_summary={'cases':len(replay),'model_calls':0,'operation_changes':sum(r['dynamic_static_operation_differs'] for r in replay),
        'axis_changes':{a:sum(r['modes']['dynamic']['dimension_results'][a]['status']!=r['modes']['static']['dimension_results'][a]['status'] for r in replay) for a in ('source','expression','mapping','scope')}}
    observations=[r for r in quality_records.values() if r['candidate_id'] in executed and not r['not_sent']]
    measured_tokens=sum((r['input_tokens'] or 0)+(r['output_tokens'] or 0) for r in observations)
    budget={'observed_candidates':len(executed),'observed_requests':len(observations),'observed_known_tokens':measured_tokens,
        'linear_requests_for_300':round(len(observations)*300/len(executed)),
        'linear_known_tokens_for_300':round(measured_tokens*300/len(executed)),
        'exclusions':'candidate extraction, annotation, engineering prefix and all quarantined candidates; same context lengths assumed'}
    names={'b0_all_keep':'B0 全部保留','b1_single':'B1 DeepSeek','b2_majority':'B2 多数票','b3_fixed_workflow':'B3 固定流程','agent':'完整Agent'}
    analysis={'verification':verification,'label_source':'gpt_preannotation','human_gold_count':0,
        'physical_usage':{'requests':len(physical),'known_tokens':known_tokens,'states':dict(Counter(r['state'] for r in physical.values())),
            'response_statuses':dict(Counter(r['response_status'] for r in physical.values())),
            'engineering_only_requests':len(engineering),'engineering_only_known_tokens':sum((r['input_tokens'] or 0)+(r['output_tokens'] or 0) for r in engineering.values())},
        'paired_comparison':paired_results,'weight_replay':weight_summary,'budget_300':budget,
        'agent_vs_b3_known_token_relative_difference':report['groups']['agent']['deployment_usage']['known_tokens']/report['groups']['b3_fixed_workflow']['deployment_usage']['known_tokens']-1}
    skipped_samples=[s['sample_id'] for s in inputs if s['candidate']['candidate_id'] in skipped]
    pending=max(v['not_started_count'] for v in report['groups'].values())
    lines=['# 跳过未知候选后的五组开发集比较','',
        f"计划40条；未知结果候选已统一隔离{len(skipped)}条（{', '.join(skipped_samples)}），比较队列{len(cohort)}条，尚未开始{pending}条。前26条结果原样继承，未知请求未重发。",'',
        f"累计{len(physical)}次提供方请求，已知{known_tokens:,} token，未知结果请求{len(unknown)}次。包含工程中断成本；未知用量没有按零计费。",'',
        '使用上一版GPT参考标签，人工金标准为0。各组总体分母保留40，并单独报告隔离数；可比数只计GPT与模型均有明确标签的样本。','',
        '| 组别 | 来源一致/可比 | 表达一致/可比 | 技术未完成 | 隔离 | 未开始 | 完成后人工建议 | 保留/拒绝/待处理 | 部署归属请求 | 已知token |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for g,v in report['groups'].items():
        m=v['metrics'];d=v['decision_counts'];u=v['deployment_usage']
        cell=lambda a:f"{m[a]['agreement_count']}/{m[a]['comparable_count']}" if m[a]['comparable_count'] else '—'
        lines.append(f"| {names[g]} | {cell('source_support')} | {cell('expression_status')} | {v['technical_incomplete_count']} | {v['skipped_unknown_count']} | {v['not_started_count']} | {v['completed_manual_recommendations']} | {d.get('accept',0)}/{d.get('reject',0)}/{d.get('defer',0)} | {u['provider_attempts']} | {u['known_tokens']:,} |")
    lines+=['','待处理包含未决、技术未完成、隔离和未开始，不等于已经转人工。部署归属用量包含各组必要首审以及已发生的隔离/失败调用，不能跨组相加。B0未执行来源或表达判断。','',
        '## 同样本比较','']
    for axis,title in (('source_support','来源支持'),('expression_status','表达判断')):
        x=paired_results[axis]
        lines.append(f"- {title}：在参考标签已知的{x['paired_reference_known']}条已运行且未隔离候选中，Agent独有匹配{x['agent_match_only']}条、B3独有匹配{x['b3_match_only']}条、共同匹配{x['both_match']}条；匹配覆盖率差（Agent−B3）为{x['matching_coverage_difference_agent_minus_b3']:.3f}，候选级bootstrap 95%区间{x['candidate_bootstrap_95_interval']}。")
    lines+=['','区间未校正同源相关性；本批仅有两个书籍来源组，不能据此推断跨来源泛化。未知候选按运行状态隔离，不能把其缺失结果当作正确或错误。', '',
        f"Agent的部署归属已知token相对B3变化{analysis['agent_vs_b3_known_token_relative_difference']:+.1%}。当前未测人工分钟数，处理建议变化不等于已证实节省人时。",'',
        '## 权重回放与300条用量估算','',
        f"对未隔离的{len(replay)}条首审做零调用回放：动态相对静态改变最终操作{weight_summary['operation_changes']}条，改变来源状态{weight_summary['axis_changes']['source']}条、表达状态{weight_summary['axis_changes']['expression']}条。没有非均匀历史可靠性，不能单独确认历史加权收益。",'',
        f"按未隔离候选的实测已知用量线性外推，相同规则与相似上下文长度下，300条约需{budget['linear_requests_for_300']:,}次请求、{budget['linear_known_tokens_for_300']:,} token。不含候选抽取、标注、工程中断、隔离候选及未知请求用量；金额需按服务的实际单价换算。",'',
        '## 完整性与适用范围','',
        '前26条预测、原候选/证据绑定、GPT标注和先前数据库均未改变，冻结代码与请求账本已核对。全程零自动重试，原总520次/1500万已知token上限不变，未发生生产写入。', '',
        '40条参考均未映射，因此未评估完整准入准确率。结果属于GPT开发集参考比较，不是独立人工评测。原始超时凭据与被隔离的局部结果保留；本轮及旧抽取的未知请求均未重发。']
    stored_verification={**verification,'database_sha256':{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in all_paths}}
    for name,data in (('analysis.json',analysis),('final_verification.json',stored_verification),('unknown_requests.json',{'automatic_resend':False,'requests':unknown})):
        (P/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    (P/'results.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    rows=['# 保留的未知请求','', '| 模型 | 候选 | 北京时间 | 网关请求ID | 本地attempt_id |','|---|---|---|---|---|']
    for r in unknown:
        t=datetime.fromtimestamp(r['started_at'],timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
        rows.append(f"| {r['model_id']} | {r['candidate_id']} | {t} | {r['provider_request_id']} | {r['attempt_id']} |")
    rows+=['','未自动重发。HTTP成功响应头或部分流内容不能证明服务端已经完成或未计费，最终结果和未知用量仍需按请求ID核对。']
    (P/'unknown_requests.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    print(json.dumps({'verification':verification,'paired_comparison':paired_results,'weight_replay':weight_summary,'budget_300':budget},ensure_ascii=False))


if __name__=='__main__':main()
