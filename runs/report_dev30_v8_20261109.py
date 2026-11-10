"""Current-packet development quality report; historical workbook labels stay outside gold."""
from collections import Counter
from datetime import datetime,timezone,timedelta
from pathlib import Path
import json
from evaluation.review_samples import ReadOnlyRepository
from evaluation.review_quality import bind_packet,annotation_template,quality_report
from review_agent.contracts import sha256_json
from review_agent.progress import write_status_snapshot
from runs.rerun_dev30_v8_20261109 import OUT,DATA,DB,summarize,configuration


def main():
    data=json.loads(DATA.read_text(encoding='utf-8'));repo=ReadOnlyRepository(DB)
    summary=summarize(data,repo);config=configuration();packets=[];rows=[];audit_bindings=[]
    old=json.loads(Path('outputs/dev30_v63_stream_rerun_20261106/comparison.json').read_text(encoding='utf-8'))
    previous={r['sample_id']:r for r in old['cases']}
    for binding,source in zip(data['bindings'],data['selected_raw_candidates']):
        cid=binding['candidate_id'];candidate=repo.get_revision(cid)
        assert sha256_json(candidate)==binding['candidate_content_hash'],'review-only run must preserve the frozen input'
        decision=repo.latest_decision(cid) or {}
        try:audit=repo.get_audit(cid,decision.get('audit_id'))
        except Exception:audit={}
        context=audit.get('context') or {};rounds=audit.get('rounds') or []
        selected=next((p for p in rounds if p['proposal_digest']==decision.get('selected_proposal_digest')),None)
        proposal=selected or (rounds[-1] if rounds else {})
        checkpoint=audit.get('agent_state') or {};state=checkpoint.get('state') or {}
        bundle=proposal.get('evidence_bundle') or state.get('bundle') or {'items':[],'evidence_hash':None}
        actions=decision.get('pending_actions') or []
        axes=decision.get('review_axes') or {}
        formal=(axes.get('mapping') or {}).get('status')=='valid' and bool(candidate['triple'].get('relation_id'))
        system={'auto_admitted':bool(decision.get('decision')=='auto_accept' and
            (decision.get('admission_policy') or {}).get('mode')=='active' and (decision.get('admission') or {}).get('allowed')),
            'prediction_observed':bool(selected),'revision_requested':bool(set(actions)&{'semantic_revision_required','normalization_required'}) or decision.get('workflow_state')=='revision_required' or
                any(d.get('status') in {'proposed','approved','review_incomplete'} for d in context.get('revision_drafts',[])),
            'mapping_completed':formal,'workflow_state':decision.get('workflow_state'),'pending_actions':actions,
            'review_axes':axes,'audit_id':audit.get('audit_id'),'audit_status':audit.get('status','not_started'),
            'independent_review_completed':bool(decision.get('independent_review_completed')),
            'failure_handling':context.get('failure_handling'),'revision_drafts':context.get('revision_drafts',[])}
        packet=bind_packet(candidate,proposal,bundle,proposal.get('review_scope_policy') or config['scope_policy'],system=system)
        packet['sample_id']=binding['sample_id'];packet['human_and_model_same_packet']=bool(selected)
        packets.append(packet)
        audit_bindings.append({'candidate_id':cid,'revision':candidate['revision'],'audit_id':audit.get('audit_id')})
        rows.append({'sample_id':binding['sample_id'],'candidate_id':cid,'revision':candidate['revision'],
            'audit_id':audit.get('audit_id'),'packet_id':packet['packet_id'],**system,
            'old_workflow':previous[binding['sample_id']]['workflow_state'],
            'old_independent_review_completed':previous[binding['sample_id']]['independent_review_completed']})
    labels_file=OUT/'annotations.json'
    annotations=json.loads(labels_file.read_text(encoding='utf-8')).get('annotations',[]) if labels_file.exists() else []
    quality=quality_report(packets,annotations,partition='development')
    quality.update(experiment_id=OUT.name,generated_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
        audit_bindings=audit_bindings,auto_admission_policy_enabled=False,auto_apply_revisions_enabled=False,
        population_manifest_hash=json.loads((OUT/'candidate_manifest.json').read_text(encoding='utf-8'))['manifest_hash'])
    write_status_snapshot(OUT/'quality_report.json',quality)
    write_status_snapshot(OUT/'review_packets.json',{'partition':'development','samples':packets,'manifest_hash':sha256_json(packets)})
    write_status_snapshot(OUT/'annotation_template.json',{'version':quality['version'],'partition':'development',
        'instruction':'基于本轮完整候选、证据与本体上下文重标四维；填完另存 annotations.json。旧版建议修改后结论不能迁入。',
        'annotations':[annotation_template(p) for p in packets]})
    comparison={'experiment_id':OUT.name,'partition':'development','summary':{k:v for k,v in summary.items() if k!='cases'},
        'quality':quality,'cases':rows,'labels_used_during_execution':False,'production_written':False}
    write_status_snapshot(OUT/'comparison.json',comparison)
    def value(m):return '暂不可评估' if m['value'] is None else f"{m['value']:.1%}"
    lines=['# 30条开发样本 V8 复跑与质量评估','',f"更新时间：{quality['generated_at']}",
        '',f"已开始 {summary['audits_started']}/30；处理结束 {summary['processing_closed']}/30；独立票完整 {summary['independent_review_completed']}/30。",
        '','本轮为开发回归。旧人工表的修改后事实判断、概念可表达性未迁为当前版本真值。',
        '','自动准入、自动应用修订、发布本体和图谱导入均关闭；全部转人工不能视为审核质量提高。',
        '','|质量指标|结果|分子/分母|缺失标注或结果|','|---|---|---|---|']
    for m in quality['metrics']:lines.append(f"|{m['title']}|{value(m)}|{m['numerator']}/{m['denominator']}|{m['unknown_count']}|")
    lines += ['','没有自动准入候选时，自动准入精确率为空，不写成0%或100%。已收齐票、运行结束与正确准入分别统计。',
        '',f"当前有效四维人工重标：{quality['human_resolved']}/30。待重标清单：annotation_template.json；完整材料：review_packets.json。",
        '','|样本|旧流程|新流程|独立票完整|原文|表达|映射|范围|','|---|---|---|---|---|---|---|---|']
    for row in rows:
        axes=row['review_axes'];lines.append('|'+ '|'.join(str(x or '—') for x in [row['sample_id'],row['old_workflow'],row['workflow_state'],
            '是' if row['independent_review_completed'] else '否',*[axes.get(k,{}).get('status') for k in ('source','expression','mapping','scope')]])+'|')
    repeat_file=OUT/'repeat_report.json'
    if repeat_file.exists():
        repeat=json.loads(repeat_file.read_text(encoding='utf-8'))
        lines += ['',f"DEV-016固定输入重复审核：{repeat.get('status')}，已记录 {repeat.get('trials',0) if isinstance(repeat.get('trials'),int) else len(repeat.get('trials',[]))} 轮。"]
        for voter in repeat.get('voters',[]):lines.append(f"- {voter['voter_id']}：有效 {voter['valid_trials']} 轮；判断变化 {voter['changed']}；技术失败 {voter['technical_failures']} 轮。")
    else:lines += ['','DEV-016将在首轮结束后选用同一份冻结提案做两次额外独立审核，比较九项判断与正式关系ID；不复用缓存票。']
    lines += [
        '','配置固定 temperature=0；top_p 和 seed 不发送。保存请求/返回模型标识；服务别名不能证明后端权重版本未变化。',
        '','未参与调试的独立验证集另行登记，在明确来源与防泄漏分组后单独评估，不能从这30条中改名切出。']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'started':summary['audits_started'],'closed':summary['processing_closed'],
        'independent':summary['independent_review_completed'],'human_resolved':quality['human_resolved']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
