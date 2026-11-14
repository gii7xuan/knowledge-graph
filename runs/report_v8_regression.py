"""Seal a completed isolated regression report without changing its database or old experiments."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from evaluation.review_samples import ReadOnlyRepository
from evaluation.review_quality import bind_packet,annotation_template,quality_report
from review_agent.mapping_trace import statistics
from runs.revalidate_v8 import save,read,digest


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True);args=parser.parse_args()
    directory=args.directory.resolve();smoke=read(directory/'smoke_report.json');repo=ReadOnlyRepository(directory/'review.sqlite3')
    progress=read(directory/'progress.json')
    if progress.get('active') or progress.get('status')=='running':raise ValueError('wait for the regression to reach terminal state')
    before=digest(directory/'review.sqlite3');packets=[];audits=[];drafts=[]
    for row in smoke['cases']:
        audit=repo.get_audit(row['candidate_id'],row['audit_id']) if row['audit_id'] else {'rounds':[]}
        decision=(repo.latest_decision(row['candidate_id']) or {}) if row['audit_id'] else {}
        p=next((p for p in audit['rounds'] if p['proposal_digest']==decision.get('selected_proposal_digest')),
            audit['rounds'][-1] if audit['rounds'] else {'candidate':repo.get_revision(row['candidate_id']),'evidence_bundle':{'items':[]}})
        o=row['outcome'];system={'auto_admitted':False,'recommendation':o['recommendation'],
            'source_support_result':o['dimension_results']['source']['status'],
            'prediction_observed':bool(audit['rounds']),'mapping_completed':o['dimension_results']['mapping']['status']=='valid',
            'revision_requested':o['recommendation']=='revise'}
        packet=bind_packet(p['candidate'],p,p['evidence_bundle'],p.get('review_scope_policy') or {},system=system)
        packet['sample_id']=row['sample_id'];packets.append(packet)
        audits.append({'candidate_id':row['candidate_id'],'revision':p['candidate']['revision'],'audit_id':row['audit_id']})
    quality=quality_report(packets);quality.update(audit_bindings=audits,auto_admission_policy_enabled=False,
        auto_apply_revisions_enabled=False,experiment_id=directory.name)
    with repo.connect() as c:
        for r in c.execute('SELECT draft_id,status,draft_json FROM revision_drafts'):
            d=json.loads(r['draft_json']);drafts.append({'draft_id':r['draft_id'],'stored_status':r['status'],
                'candidate_id':d['candidate_id'],'base_revision':d['base_revision'],
                'change_analysis':d.get('change_analysis'),'requires_new_version_validation':True})
        attempts=[json.loads(r[0] or '{}') for r in c.execute('SELECT result_json FROM request_attempts')]
    mapping=statistics(r['outcome']['mapping_process'] for r in smoke['cases'])
    failures=Counter(e['category'] for row in smoke['cases'] for r in row['outcome']['vote_validation'].values() for e in r['errors'])
    coverage={'unit':'selected current candidate packet','population':len(smoke['cases']),
        'audits_started':sum(bool(r['audit_id']) for r in smoke['cases']),
        'execution_counts':dict(Counter(r['outcome']['execution_status'] for r in smoke['cases'])),
        'full_committee_packets':sum(len(r['outcome']['whole_vote_eligible_voter_ids'])==r['outcome']['dimension_results']['scope']['required_seats'] for r in smoke['cases']),
        'dimension_complete':{a:sum(r['outcome']['dimension_results'][a]['judgment_complete'] for r in smoke['cases'])
        for a in ('source','expression','mapping','scope')},'failure_error_occurrences':dict(failures),
        'recommendations':dict(Counter(r['outcome']['recommendation'] for r in smoke['cases'])),
        'cached_result_count':sum(bool(r.get('cached')) for r in attempts),
        'clarification_count':sum(bool(r.get('semantic_clarification')) for r in attempts),
        'actual_actions':{'none':len(smoke['cases'])},'human_labels':0}
    for name,value in [('quality_report.json',quality),('review_packets.json',{'samples':packets}),
        ('annotation_template.json',{'instruction':'独立审核当前完整材料；允许不可判断，不使用旧版标签','annotations':[annotation_template(p) for p in packets]}),
        ('coverage.json',coverage),('mapping_process_statistics.json',mapping),('shadow_revision_inventory.json',{'drafts':drafts,
            'current_experiment_revisions_applied':smoke['isolated_revisions_applied'],'real_revision_quality':'not_evaluable',
            'entry':'python -m runs.shadow_revision_v8 --source <db> --output outputs/<new-shadow> --draft-id <independently-approved-draft> --run',
            'requires_exact_independent_approval':True,'max_semantic_revision_rounds':1})]:save(directory,name,value)
    lines=[f"# V8 {smoke['population']}条开发样本隔离复跑",'',f"工程门槛：{'通过' if smoke['engineering_gate_passed'] else '未通过，停止扩批'}。",
        f"已启动 {coverage['audits_started']}/{coverage['population']}；三席完整 {coverage['full_committee_packets']}/{coverage['population']}；执行状态 {coverage['execution_counts']}。",
        f"本阶段新委员请求尝试{smoke['new_committee_request_attempts']}次；规划请求{smoke['planner_calls']}次。缓存回放不计独立重复。",
        f"模式：{smoke['mode']}；来源实验的真实委员请求{smoke.get('recorded_committee_request_attempts',smoke['new_committee_request_attempts'])}次、规划请求{smoke.get('recorded_planner_calls',smoke['planner_calls'])}次。",'',
        '候选、原文、本体、规则、提示词、输出契约、代码及服务设置已冻结。生产准入、修订应用、发布与导入均关闭。',
        '执行终态不等于审核完成，三席齐全不等于事实正确；本报告是开发回归，不是独立验证。','',
        '|样本|执行|原文（有效席位）|表达（有效席位）|映射（有效席位）|范围（有效席位）|建议|',
        '|---|---|---|---|---|---|---|']
    for row in smoke['cases']:
        o=row['outcome'];lines.append('|'+ '|'.join([row['sample_id'],o['execution_status'],*[o['dimension_results'][a]['status']+
            f" ({o['dimension_results'][a]['valid_seats']}/3)" for a in ('source','expression','mapping','scope')],o['recommendation']])+'|')
    lines+=['','四维人工标签0条；实际自动准入指标与六项影子建议指标均暂不可评估。',
        '修订机制在隔离测试中验证了版本生成、三席独立审批、原子拆分、旧票不继承和新版本重审。实际修订有效性仍须执行影子案例并补充绑定全部子版本的人工评价。',
        '未形成有效正式映射时，应继续补齐绑定本体的定义与实体身份材料；不能将相似召回、约束通过或检索未命中作为语义等价/本体缺口证明。']
    target=directory/'report.md';content='\n'.join(lines)+'\n'
    if target.exists() and target.read_text(encoding='utf-8')!=content:raise ValueError('report changed; use a new analysis directory')
    if not target.exists():target.write_text(content,encoding='utf-8')
    if digest(directory/'review.sqlite3')!=before:raise ValueError('report must not change source database')
    seal=read(directory/'report_seal.json') if (directory/'report_seal.json').exists() else {}
    save(directory,'report_seal.json',{'database_sha256':before,'mode':smoke['mode'],
        'sealed_at':seal.get('sealed_at') or datetime.now().astimezone().isoformat(),
        'model_calls_during_reporting':0,'source_database_changed':False})
    print(json.dumps({'directory':str(directory),'gate':smoke['engineering_gate_passed'],'coverage':coverage},ensure_ascii=False))


if __name__=='__main__':main()
