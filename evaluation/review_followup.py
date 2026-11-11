"""Offline follow-up of a sealed development run. No model calls or label migration.

python -m evaluation.review_followup --source outputs/dev30_v8_full_20261111 \
    --output outputs/dev30_next_20261111
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from evaluation.review_samples import ReadOnlyRepository
from evaluation.review_quality import annotation_template, quality_report
from review_agent.contracts import sha256_json
from review_agent.mapping_trace import process
from review_agent.review_observability import assess_vote
from review_agent.validators import _parse_content
from review_agent.repository import ValidationError
from review_agent import response_corrections as repairs, semantic_clarification as clarification


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def jsonl(path, rows):
    path.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selected(repo, case):
    audit = repo.get_audit(case['candidate_id'], case['audit_id'])
    decision = repo.latest_decision(case['candidate_id'])
    proposal = next(p for p in audit['rounds'] if p['proposal_digest']==decision['selected_proposal_digest'])
    votes = [v for v in audit['votes'] if v['proposal_digest']==proposal['proposal_digest']]
    return proposal, votes


def window_simulation(cases, proposals, votes_by_id, attempts, windows=(90,120,150,180)):
    """Only reconcile the same originally selected attempt; never borrow another round."""
    receipts=[]
    for case in cases:
        sid=case['sample_id'];p=proposals[sid]
        for v in votes_by_id[sid]:
            if v.get('response_status')!='timeout':continue
            a=attempts.get(v.get('attempt_id'))
            if not a:continue
            r=a['result']
            elapsed=(a['response_received_at']-a['started_at']) if a['response_received_at'] else None
            valid=assess_vote(r,p)['whole_vote_eligible']
            receipts.append({'sample_id':sid,'voter_id':v['voter_id'],'attempt_id':a['attempt_id'],
                'started_at_epoch':a['started_at'],'response_received_at_epoch':a['response_received_at'],
                'original_deadline_epoch':a['deadline_at'],'settlement_elapsed_seconds':elapsed,
                'worker_latency_ms':r.get('latency_ms'),'local_dispatch_queue_ms':r.get('queue_time_ms'),
                'transport_metrics':r.get('transport_metrics'),'actual_receipt_eligible':valid,
                'provider_queue_ms':None,'parse_and_validation_separate_ms':None})
    rows=[]
    for seconds in windows:
        complete=[];added=[]
        for case in cases:
            sid=case['sample_id'];p=proposals[sid]
            ids={v['voter_id'] for v in votes_by_id[sid] if assess_vote(v,p)['whole_vote_eligible']}
            for r in receipts:
                if r['sample_id']==sid and r['actual_receipt_eligible'] and r['settlement_elapsed_seconds'] is not None and r['settlement_elapsed_seconds']<=seconds:
                    ids.add(r['voter_id']);added.append({'sample_id':sid,'voter_id':r['voter_id']})
            if ids=={m['id'] for m in p['committee_members']}:complete.append(sid)
        rows.append({'window_seconds':seconds,'complete_proposals':len(complete),'sample_ids':complete,'reconciled_seats':added})
    return {'kind':'counterfactual_saved_receipt_reconciliation','new_model_calls':0,
        'historical_decisions_changed':False,'timing_origin':'durable request started_at to response_received_at (persisted after worker result)',
        'limitation':'Fixed selected attempts and vote rules. Larger windows can change scheduling in a real run; no throughput prediction.',
        'receipts':receipts,'windows':rows}


def costs(repo, attempts):
    groups=defaultdict(list)
    for a in attempts.values():
        phase=(a['metadata'].get('feedback') or {}).get('kind') or 'initial'
        groups['committee:'+phase].append(a['result'])
    with repo.connect() as c:
        for row in c.execute('SELECT record_json FROM planner_steps'):
            groups['planner'].extend(read_json['attempts'] if (read_json:=json.loads(row[0])).get('attempts') else [])
        tools=Counter(r[0] for r in c.execute('SELECT tool_name FROM agent_tool_calls'))
    summary={}
    for phase, values in groups.items():
        summary[phase]={'requests':len(values),'input_tokens':sum(v.get('input_tokens') or 0 for v in values),
            'output_tokens':sum(v.get('output_tokens') or 0 for v in values),
            'usage_missing':sum(v.get('input_tokens') is None or v.get('output_tokens') is None for v in values),
            'latency_ms_sum':sum(v.get('latency_ms') or 0 for v in values),
            'receipt_statuses':dict(Counter(v.get('response_status') for v in values))}
    return {'phases':summary,'tool_calls':dict(tools),'currency_cost':None,
        'total_reported_tokens':sum(v['input_tokens']+v['output_tokens'] for v in summary.values()),
        'semantic_quality_not_inferred_from_receipt_status':True,'latency_sums_are_not_wall_time':True}


def contract_diagnosis(proposals, votes_by_id):
    rows=[]
    for sid,p in proposals.items():
        for v in votes_by_id[sid]:
            if v.get('response_status') not in {'invalid_contract','invalid_reference'}:continue
            try:raw=_parse_content(v.get('corrected_response') or v['raw_response'])
            except (ValueError,TypeError):continue
            policy=repairs.policy_for({'semantic_clarification':{'enabled':True}})
            plan,errors=repairs.make_plan(raw,p,policy)
            conflicts=repairs._conflicts(raw)
            if conflicts:
                new=clarification.payload(raw,clarification.make_plan(raw,p,policy,conflicts),p)
                prompt=clarification.PROMPT;kind='semantic_clarification'
            elif plan:
                new=repairs.repair_payload(raw,plan,p);prompt=repairs.PROMPT;kind='field_patch'
            else:new=None;prompt='';kind='manual_review'
            wire={'type':'json_object'}
            if new and kind=='field_patch':
                from review_agent.output_structure import response_format
                profile=next(g['output_transport'] for g in p['validator_generation'] if g['voter_id']==v['voter_id'])
                wire=response_format(profile,repairs.repair_schema(plan))
                if profile['mode']=='json_schema':new.pop('output_template',None)
            size=len(json.dumps({'system':prompt,'payload':new,'response_format':wire},ensure_ascii=False,separators=(',',':')).encode('utf-8')) if new else None
            rows.append({'sample_id':sid,'voter_id':v['voter_id'],'original_status':v['response_status'],
                'original_error':v.get('error'),'original_stop_reason':v.get('correction_stop_reason'),
                'conflicts':conflicts,'blocked_field_repairs':errors,'next_attempt_kind':kind,
                'new_payload_input_bytes':size,'new_payload_fits_input_limit':size<=policy['limits']['max_input_bytes'] if size else None,
                'source_bound_plan_ids':[x['plan_id'] for x in p.get('field_standardization',{}).get('plans',[])],
                'new_payload_is_offline_only':True,'new_semantic_judgment':None})
    return rows


def revision_case(packet, proposal, votes):
    """Prepare source-bound previews; approval and new-version review remain explicit."""
    from review_agent.revision_loop import analyze
    from review_agent.revision_patch import merge_triple_patch
    before=packet['candidate'];std=packet.get('field_standardization') or {}
    suggestions=[]
    if std.get('plans'):
        after=deepcopy(before);after['triple']=deepcopy(std['normalized_expression'])
        suggestions.append({'kind':'source_bound_normalization','after':after,'plans':std['plans']})
    for vote in votes:
        try:raw=_parse_content(vote.get('corrected_response') or vote.get('raw_response'))
        except (ValueError,TypeError):continue
        if raw.get('suggested_revision'):
            changes=raw['suggested_revision'];after=deepcopy(before)
            # A single explicit triple wrapper is losslessly unwrapped; no ID or meaning is guessed.
            if set(changes)=={'triple'} and isinstance(changes['triple'],dict):changes=changes['triple']
            try:after['triple']=merge_triple_patch(before['triple'],changes,strict=before.get('contract_version')=='extraction_candidate_v4')
            except (ValueError,ValidationError) as exc:
                suggestions.append({'voter_id':vote['voter_id'],'changes':changes,'error':str(exc)});continue
            suggestions.append({'kind':'model_suggestion','voter_id':vote['voter_id'],'after':after,'changes':changes})
    for s in suggestions:
        if 'after' not in s:continue
        s['after']['revision']=before['revision']+1
        s['diff']=[{'field':k,'before':before['triple'].get(k),'after':s['after']['triple'].get(k)}
            for k in sorted(set(before['triple'])|set(s['after']['triple'])) if before['triple'].get(k)!=s['after']['triple'].get(k)]
        s['change_analysis']=analyze(before,s['after'],packet['evidence_bundle'])
        s['preview_hash']=sha256_json(s['after']);s['old_votes_reusable']=False
    return {'sample_id':packet['sample_id'],'packet_id':packet['packet_id'],'before':before,
        'suggestions':suggestions,'verification_status':'verification_incomplete',
        'reason':'No independently approved current-version draft or new-version evidence/mapping review; preview is not an applied revision.',
        'human_necessary':None,'human_correct':None,'production_written':False}


def run(source, output, old_annotations=None, comparison=None):
    source=source.resolve();output=output.resolve()
    if output.exists() or output.is_relative_to(source):raise ValueError('Use a new separate output directory; sealed results are never overwritten')
    protected=[p for p in source.iterdir() if p.is_file() and not p.name.endswith(('-shm','-wal'))]
    if old_annotations:protected.append(old_annotations.resolve())
    if comparison:protected.append(comparison.resolve())
    before={str(p):digest(p) for p in protected}
    repo=ReadOnlyRepository(source/'review.sqlite3')
    cases=read(source/'smoke_report.json')['cases'];packets=read(source/'review_packets.json')['samples']
    proposals={};votes_by_id={}
    for case in cases:proposals[case['sample_id']],votes_by_id[case['sample_id']]=selected(repo,case)
    with repo.connect() as c:
        attempts={r['attempt_id']:{**dict(r),'metadata':json.loads(r['metadata_json']),
            'result':json.loads(r['result_json'] or '{}')} for r in c.execute('SELECT * FROM request_attempts')}
    output.mkdir(parents=True)
    blockers=[];traces=[];annotations=[]
    old=read(old_annotations)['annotations'] if old_annotations else []
    for case in cases:
        sid=case['sample_id'];p=proposals[sid];votes=votes_by_id[sid];o=case['outcome']
        packet=next(p for p in packets if p['sample_id']==sid)
        trace=process(p,votes);trace.update(sample_id=sid,packet_id=packet['packet_id'])
        if o['recommendation']=='wait_mapping':traces.append(trace)
        row={'sample_id':sid,'packet_id':packet['packet_id'],'binding':packet['binding'],
            'recommendation':o['recommendation'],'axes':o['dimension_results'],
            'blocking_reasons':o['blocking_reasons'],'mapping_blockers':trace['blockers'],
            'mapping_milestones':trace['milestones'],'eligible_voter_ids':o['whole_vote_eligible_voter_ids'],
            'vote_validation':o['vote_validation'],'evidence_ids':[e['evidence_id'] for e in packet['evidence_bundle']['items']],
            'raw_database':str(source/'review.sqlite3'),'proposal_digest':p['proposal_digest'],
            'next_actions':[{'reason':reason,'action':{
                'definition_missing':'核对已检索关系的正式发布版定义；缺定义保留阻塞，独立审核定义后再开新轮',
                'identity_ambiguous':'按 missing_information 补齐两端身份、作用域和类型，不以类型ID替代实例身份',
                'retrieval_failed':'先排除检索失败或上下文预算阻塞，不将其解释为本体缺口',
                'constraint_conflict':'人工核对方向、domain/range与限定语义的真实冲突，禁止换相似关系强行通过',
                'mapping_material_incomplete':'核对列出的映射材料缺项后重审',
                'mapping_not_executed':'检查映射入口和前置条件'}.get(reason,'核对当前维度、证据及对应票据')}
                for reason in sorted({b['reason'] for b in trace['blockers']})]}
        row['next_actions'].extend({'reason':reason,'action':'核对该维度的全部原始票、有效引用及冲突，保留并存阻塞'}
            for reason in o['blocking_reasons'] if not reason.startswith(('mapping:','ontology_','formal_mapping_')))
        blockers.append(row)
        a=annotation_template(packet);a['sample_id']=sid
        a['axis_review']={axis:{'determinable':None,'missing_material':[], 'blocker_type':None,'note':None,
            'reuse_confirmed_by_human':False,'reuse_basis':None} for axis in ('source','expression','mapping','scope')}
        a['axis_review']['mapping']['missing_material']=[deepcopy(b.get('detail',b)) for b in trace['blockers']]
        a['correct_disposition']=None;a['avoidable_manual_review']=None
        a['prior_label_ids_for_later_comparison']=[x['packet_id'] for x in old if x.get('sample_id')==sid]
        annotations.append(a)
        blind={k:deepcopy(v) for k,v in packet.items() if k!='system'}
        write(output/'annotation_packets'/f'{sid}.json',blind)
    jsonl(output/'blocker_breakdown.jsonl',blockers);jsonl(output/'mapping_trace.jsonl',traces)
    # Old answers and model suggestions are deliberately kept outside the blind material.
    jsonl(output/'annotation_current.jsonl',annotations)
    write(output/'prior_labels_after_annotation.json',{'annotations':old,'automatically_rebound':False,
        'material_differences':read(comparison).get('previous_human_labels',[]) if comparison else [],
        'human_per_axis_confirmation_required':True})
    write(output/'model_suggestions_after_annotation.json',{'cases':cases,'show_after_independent_annotation':True})
    window=window_simulation(cases,proposals,votes_by_id,attempts)
    write(output/'window_sensitivity.json',window)
    cost=costs(repo,attempts);write(output/'request_cost_breakdown.json',cost)
    diagnoses=contract_diagnosis(proposals,votes_by_id);write(output/'contract_fix_diagnosis.json',diagnoses)
    (output/'contract_fix_diagnosis.md').write_text('# 契约诊断（仅离线）\n\n'+ '\n'.join(
        f"- {r['sample_id']} / {r['voter_id']}：{r['original_error']}；下一步 {r['next_attempt_kind']}，压缩后输入 {r['new_payload_input_bytes']} bytes。" for r in diagnoses)+
        '\n\nDEV-019 的 plans 为空，FREQ-001 是规则而非合法计划；拒绝正确，禁止猜补。DEV-022 保留反证冲突；澄清单独记为新尝试且不计票。DEV-024 matched 缺ID需模型补齐，不从注释猜ID；unassigned 允许 null/[]。\n',encoding='utf-8')
    revisions=[revision_case(p,proposals[p['sample_id']],votes_by_id[p['sample_id']]) for p in packets if p['system']['recommendation']=='revise']
    jsonl(output/'revision_validation.jsonl',revisions)
    write(output/'quality_report.json',quality_report(packets))
    report=['# 下一步改进：离线盘点与定向修复','',
        f"已导出 {len(blockers)} 条四维阻塞、{len(traces)} 条等待映射轨迹、{len(annotations)} 份当前绑定人工模板及 {len(revisions)} 条修订预览。",
        f"历史建议：{dict(Counter(r['recommendation'] for r in blockers))}。",
        '正式有效映射指标来自选定审核的 mapping.status=valid，而非发布/登记数；本轮全部 context_incomplete。',
        f"等待映射原因（按候选计数，可重叠）：{dict(Counter(reason for t in traces for reason in {b['reason'] for b in t['blockers']}))}。",
        f"映射里程碑：{dict((k,dict(Counter('unknown' if t['milestones'][k] is None else 'yes' if t['milestones'][k] else 'no' for t in traces))) for k in traces[0]['milestones']) if traces else {}}。",
        '已执行局部和全绑定本体检索，主要缺口须核对正式发布版定义和实体身份；缺定义不代表不存在等价关系，不补造定义或强行替换。',
        '','|反事实窗口/秒|完整提案|','|---|---|',
        *[f"|{r['window_seconds']}|{r['complete_proposals']}/{len(cases)}|" for r in window['windows']],
        '', '计时按持久请求开始至结果入账；同时保留worker、首内容和队列时点。模拟不改写90秒历史结果，不预测真实吞吐。',
        f"用量分组：{cost['phases']}。已报告token合计 {cost['total_reported_tokens']}，含缺失用量，不能作为完整总量；无价格表不估金额。",'',
        '标注顺序：先阅读 annotation_packets/ 中候选、原文位置、完整证据、本体、范围及字段规则并独立判断；随后再看 model_suggestions_after_annotation.json 和旧标签。',
        'annotation_current.jsonl 每维可判定性、缺材料、阻塞类型及沿用依据均待人工填写。旧5条未改绑；当前有效人工标签0，事实/处置质量不可评估。',
        '两条修订保存修改前后、字段差异、语义风险和预览哈希；未独立批准及新版本复核，状态为执行未完成，不计修订成功。',
        '','代码：新轮提示v4明确规则ID/计划ID、反证pass和nullable映射字段；补丁v2对大本体按无损列式编码，语义澄清保留完整证据并去掉候选重复包装；预算上限不变。历史v3提示及v1补丁仍按原哈希读取。',
        '新提示和补丁尚未经过真实模型复验；本次新模型调用0、生产写入0。先完成人工核验和有界真实定向验证，再冻结配置全量复跑；这30条仍为开发集。','']
    (output/'report.md').write_text('\n'.join(report),encoding='utf-8')
    after={str(p):digest(p) for p in protected}
    verification={'source_files_unchanged':before==after,'protected_sha256':before,
        'new_model_calls':0,'production_written':False,'old_labels_rebound':False}
    write(output/'verification.json',verification)
    if before!=after:raise RuntimeError('Source files changed during read-only analysis')
    return {'output':str(output),'cases':len(cases),'window_complete':[r['complete_proposals'] for r in window['windows']],
        'costs':cost['phases'],'diagnoses':diagnoses,'source_unchanged':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--old-annotations',type=Path)
    parser.add_argument('--comparison',type=Path)
    args=parser.parse_args();print(json.dumps(run(args.source,args.output,args.old_annotations,args.comparison),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
