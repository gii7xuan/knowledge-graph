"""Local analysis of the completed development comparison; never calls a model."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sqlite3
from contextlib import closing

P=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text(encoding='utf-8'))


def paired(rows_a,rows_b,refs,axis):
    a={r['candidate_id']:r for r in rows_a};b={r['candidate_id']:r for r in rows_b}
    differences=[];both=[];counts=Counter();not_run=0
    for ref in refs:
        target=ref['labels'][axis]
        if target=='unresolved':continue
        key=ref['binding']['candidate_id']
        if a[key]['status']=='not_started' or b[key]['status']=='not_started':
            not_run+=1;continue
        left=a[key]['axis_labels'].get(axis,'unresolved');right=b[key]['axis_labels'].get(axis,'unresolved')
        differences.append(int(right==target)-int(left==target))
        if left!='unresolved' and right!='unresolved':both.append(int(right==target)-int(left==target))
        counts['agent_match_only']+=right==target and left!=target
        counts['b3_match_only']+=left==target and right!=target
        counts['both_match']+=left==right==target
        counts['both_resolved']+=left!='unresolved' and right!='unresolved'
        counts['both_unresolved']+=left==right=='unresolved'
    rng=random.Random(20261119)
    draws=sorted(sum(rng.choices(differences,k=len(differences)))/len(differences) for _ in range(10000)) if differences else []
    return {'axis':axis,'known_reference_count_in_paired_executed_cases':len(differences),
        'known_reference_cases_not_run':not_run,**counts,
        'matching_coverage_difference_agent_minus_b3':sum(differences)/len(differences) if differences else None,
        'paired_bootstrap_95_percentile_interval':[draws[249],draws[9749]] if draws else None,
        'both_resolved_mean_match_difference':sum(both)/len(both) if both else None,
        'interpretation':'matching coverage against GPT reference, unresolved counts as no match; not human accuracy'}


def ledgers(paths):
    records={}
    for path in paths:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as connection:
            for attempt,state,meta,raw in connection.execute('SELECT attempt_id,state,metadata_json,result_json FROM request_attempts'):
                r=json.loads(raw or '{}');m=json.loads(meta)
                records[attempt]={'state':state,'task_type':m.get('task_type'),'model_id':m.get('binding',{}).get('model_id'),
                    **{k:r.get(k) for k in ('response_status','input_tokens','output_tokens','not_sent','error')}}
    return records


def main():
    if not (P/'finished.json').exists():raise RuntimeError('wait for the original run to finish')
    report=read(P/'comparison_report.json');protocol=read(P/'protocol.json');reference=read(P/'gpt_reference.json')
    groups={g:read(P/(g+'_predictions.json')) for g in report['groups']}
    refs=reference['annotations'];paired_results={axis:paired(groups['b3_fixed_workflow'],groups['agent'],refs,axis)
        for axis in ('source_support','expression_status')}
    original_packets=read(P.parents[1]/'outputs/development_pool_20261117/pilot/review_samples.json')['samples']
    source_groups=dict(Counter(s['source_group_id'] for s in original_packets))
    prior=protocol['continuation'];prior_paths=[Path(p) for p in prior['database_sha256']]
    current=ledgers(list(P.glob('case_*/*.sqlite3')));old=ledgers(prior_paths);all_records={**old,**current}
    physical=[r for r in all_records.values() if not r.get('not_sent')]
    assert len(physical)==report['usage']['unique_provider_attempts']
    input_tokens=sum(r.get('input_tokens') or 0 for r in physical);output_tokens=sum(r.get('output_tokens') or 0 for r in physical)
    assert input_tokens+output_tokens==report['usage']['known_tokens']
    overhead={k:r for k,r in old.items() if k not in current}
    known={axis:sum(r['labels'][axis]!='unresolved' for r in refs) for axis in ('source_support','expression_status')}
    replay=read(P/'weight_replay.json')
    replay_records=[r for r in replay if 'modes' in r]
    replay_summary={'records':len(replay_records),'errors':len(replay)-len(replay_records),
        'operation_changes_dynamic_vs_static':sum(r['dynamic_static_operation_differs'] for r in replay_records),
        'axis_status_changes_dynamic_vs_static':{a:sum(r['modes']['dynamic']['dimension_results'][a]['status']!=r['modes']['static']['dimension_results'][a]['status'] for r in replay_records) for a in ('source','expression','mapping','scope')},
        'weight_changes_dynamic_vs_static':{a:sum(any(abs(s['weight']-r['modes']['static']['weight_snapshot']['axes'][a]['seats'][v]['weight'])>1e-10 for v,s in r['modes']['dynamic']['weight_snapshot']['axes'][a]['seats'].items()) for r in replay_records) for a in ('source','expression','mapping','scope')},
        'extra_model_calls':0,'nonuniform_historical_reliability_available':False}
    code=read(P/'code_fingerprints.json');root=P.parents[1]
    verification={'population_preserved':all(len(rows)==40 for rows in groups.values()),
        'candidate_bindings_preserved':all(r['binding']==next(x['binding'] for x in refs if x['binding']['candidate_id']==r['candidate_id']) for rows in groups.values() for r in rows),
        'prior_databases_unchanged':all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in prior['database_sha256'].items()),
        'frozen_code_unchanged':all(hashlib.sha256((root/p).read_bytes()).hexdigest()==h for p,h in code.items()),
        'gpt_source_unchanged':hashlib.sha256(Path(reference['source_path']).read_bytes()).hexdigest()==reference['source_sha256'],
        'local_request_accounting_reconciled':True,'unknown_requests':sum(r['state'] in {'running','unknown'} for r in physical)}
    assert all(verification[k] for k in ('population_preserved','candidate_bindings_preserved','prior_databases_unchanged','frozen_code_unchanged','gpt_source_unchanged'))
    processed_ordinals={i+1 for i,(a,b) in enumerate(zip(groups['b3_fixed_workflow'],groups['agent'])) if a['status']!='not_started' and b['status']!='not_started'}
    processed_records=ledgers([p for p in P.glob('case_*/*.sqlite3') if int(p.parent.name.split('_')[1]) in processed_ordinals])
    usable=[r for r in processed_records.values() if not r.get('not_sent')]
    projection_300=None
    if processed_ordinals and not any(r['state'] in {'running','unknown'} or any(r.get(k) is None for k in ('input_tokens','output_tokens')) for r in usable):
        useful_tokens=sum((r.get('input_tokens') or 0)+(r.get('output_tokens') or 0) for r in usable)
        scale=300/len(processed_ordinals)
        projection_300={'candidate_count':300,'estimated_shared_requests':round(len(usable)*scale),
            'estimated_known_tokens':round(useful_tokens*scale),'observed_candidates':len(processed_ordinals),
            'observed_useful_requests':len(usable),'observed_useful_known_tokens':useful_tokens,
            'scope':'same review protocol and similar context lengths; excludes candidate extraction, annotation, engineering interruptions, the case with unknown requests, and monetary unit pricing'}
    analysis={'population':40,'label_source':'gpt_preannotation','human_gold_count':0,'known_reference_counts':known,
        'source_group_counts':source_groups,'source_group_count':len(source_groups),
        'development_budget_projection_300':projection_300,
        'physical_usage':{'requests':len(physical),'input_tokens':input_tokens,'output_tokens':output_tokens,
            'known_tokens':input_tokens+output_tokens,'engineering_only_requests':len(overhead),
            'engineering_only_known_tokens':sum((r.get('input_tokens') or 0)+(r.get('output_tokens') or 0) for r in overhead.values()),
            'request_states':dict(Counter(r['state'] for r in physical)),
            'response_statuses':dict(Counter(r['response_status'] for r in physical))},
        'paired_comparison':paired_results,'weight_replay':replay_summary,'verification':verification,
        'agent_vs_b3_known_deployment_token_difference':report['groups']['agent']['deployment_usage']['known_tokens']-report['groups']['b3_fixed_workflow']['deployment_usage']['known_tokens'],
        'agent_vs_b3_known_deployment_token_relative_difference':report['groups']['agent']['deployment_usage']['known_tokens']/report['groups']['b3_fixed_workflow']['deployment_usage']['known_tokens']-1,
        'provider_failures':[dict(attempt_id=k,**r) for k,r in all_records.items() if r['response_status']!='ok']}
    (P/'analysis.json').write_text(json.dumps(analysis,ensure_ascii=False,indent=2),encoding='utf-8')
    names={'b0_all_keep':'B0 全部保留','b1_single':'B1 DeepSeek','b2_majority':'B2 多数票','b3_fixed_workflow':'B3 固定流程','agent':'完整Agent'}
    lines=['# 五组开发集比较的部分结果','',
        f"计划40条，B3与Agent两分支均已运行{len(processed_ordinals)}条。第27条首审出现两个未知结果的请求，已按协议停止后续派发；13条尚未首审，两分支各14条未开始。总分母保留40，未宣称跑完40条。",'',
        f"使用上一版GPT参考标签，人工金标准为0。来源支持标签已知{known['source_support']}条、表达标签已知{known['expression_status']}条。40条来自{len(source_groups)}个书籍来源组，结果衡量与GPT的一致性，不是独立人工准确率。",'',
        f"实际提供方请求共{len(physical)}次，已知输入{input_tokens:,}、输出{output_tokens:,}，合计{input_tokens+output_tokens:,} token。包含{len(overhead)}次仅用于工程中断分支的请求。当前未知请求{verification['unknown_requests']}次。",'',
        '| 组别 | 来源一致/可比 | 表达一致/可比 | 技术未完成 | 未开始 | 完成后人工建议 | 保留/拒绝/待处理 | 部署归属请求 | 已知token |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for g,summary in report['groups'].items():
        m=summary['metrics'];d=summary['decision_counts'];u=summary['deployment_usage']
        val=lambda axis:f"{m[axis]['agreement_count']}/{m[axis]['comparable_count']}" if m[axis]['comparable_count'] else '—'
        lines.append(f"| {names[g]} | {val('source_support')} | {val('expression_status')} | {summary['technical_incomplete_count']} | {summary['not_started_count']} | {summary['completed_manual_recommendations']} | {d.get('accept',0)}/{d.get('reject',0)}/{d.get('defer',0)} | {u['provider_attempts']} | {u['known_tokens']:,} |")
    lines+=['', '可比数只计模型与GPT均有明确标签的样本；未决、未开始及GPT未知标签不进入可比数，总体分母始终为40。“待处理”包含技术未完成和未开始，不等同于已转人工。技术失败与语义未决分别报告。B0没有来源/表达判断。部署归属成本包括各组需要的共享首审及第27条的失败首审，不能跨组相加；两条未知请求的用量未计入已知token。','',
        '## B3与Agent的同样本比较','']
    for axis,label in (('source_support','来源支持'),('expression_status','表达判断')):
        x=paired_results[axis];interval=x['paired_bootstrap_95_percentile_interval'];delta=x['matching_coverage_difference_agent_minus_b3']
        lines.append(f"- {label}：在两分支已运行且GPT标签已知的{x['known_reference_count_in_paired_executed_cases']}条中，Agent独有匹配{x['agent_match_only']}条，B3独有匹配{x['b3_match_only']}条，均匹配{x['both_match']}条；差值{delta:.3f}，同样本bootstrap 95%区间[{interval[0]:.3f}, {interval[1]:.3f}]。")
    lines+=['', '这里的差值是参考标签已知样本上的“匹配覆盖率”之差，未决记为未匹配，不将未决解释为事实错误。区间使用候选级重采样，未校正同源相关性；本批仅有两个书籍来源组，区间不能代表跨文档泛化。', '',
        f"已运行部分未观察到Agent相对B3的来源/表达匹配增益。Agent部署归属的已知token较B3高{analysis['agent_vs_b3_known_deployment_token_relative_difference']:.1%}，两组处理建议有3条不同；当前不能据此确认新增拒绝正确或人工时间减少。",'',
        '## 权重回放','',
        f"对{len(replay_records)}条实际首审做零调用等权/静态/动态回放。动态相对静态改变最终操作{replay_summary['operation_changes_dynamic_vs_static']}条，改变来源状态{replay_summary['axis_status_changes_dynamic_vs_static']['source']}条、表达状态{replay_summary['axis_status_changes_dynamic_vs_static']['expression']}条。尚无非均匀历史可靠性，本次不能单独证明历史加权的收益。",'',
        '## 可用范围与复核记录','',
        '40条原参考均未映射，完整准入质量未被评估。没有人工计时数据，不能把转人工数量的变化直接解释为节省人工时间。GPT参考仅用于本地计分，不进入模型输入；不把GPT的准入偏好当作事实真值。旧抽取超时请求未重发，也不与本轮请求混计。','',
        '首轮工程中断的7条首审原始回执被复用，修复版两组共享首审与同一剩余运行时间。旧分支结果排除出质量比较，其真实成本保留。模型请求零自动重试。', '',
        f"完整性检查：原候选/证据绑定、原GPT标签、先前数据库和冻结代码均未改变；请求与token已按唯一attempt_id核对。运行停止原因：{report['usage']['stop_reason'] or '全部候选已处理'}。",'',
        '完整数据见 comparison_report.json、analysis.json、各组 *_predictions.json、weight_replay.json 及逐候选SQLite日志。']
    if projection_300:
        lines+=['','## 300条审核预算的开发集外推','',
            f"使用已运行的{projection_300['observed_candidates']}条、剔除工程中断分支及未知请求所在候选，按相似上下文长度与共享首审规则，300条约需{projection_300['estimated_shared_requests']:,}次提供方请求、{projection_300['estimated_known_tokens']:,}已知token。该数值是开发样本的线性外推，不是硬上限；不含新候选抽取、标注、工程中断及未知请求成本，金额需按两个服务的实际单价换算。"]
    (P/'results.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:analysis[k] for k in ('population','physical_usage','paired_comparison','weight_replay','verification')},ensure_ascii=False))


if __name__=='__main__':main()
