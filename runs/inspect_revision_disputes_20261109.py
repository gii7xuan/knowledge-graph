"""Read-only inspection of the 12 revision-queue cases and reported disputes."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import json
import sqlite3
from evaluation.review_samples import ReadOnlyRepository
from review_agent.revision_loop import history
from review_agent.disputes import build_report

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'outputs/dev30_v63_stream_rerun_20261106'
OUT=ROOT/'outputs/revision_disputes_20261109'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    repo=ReadOnlyRepository(SOURCE/'review.sqlite3')
    old=json.loads((SOURCE/'comparison.json').read_text(encoding='utf-8'))
    revision_cases=[];dispute_cases=[]
    with sqlite3.connect((SOURCE/'review.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row
        for case in old['cases']:
            if case['workflow_state']!='revision_required' and case['sample_id'] not in {'DEV-005','DEV-022','DEV-028'}:continue
            audit=repo.get_audit(case['candidate_id'],case['audit_id'])
            digest=repo.latest_decision(case['candidate_id'])['selected_proposal_digest']
            proposal=next(p for p in audit['rounds'] if p['proposal_digest']==digest)
            votes=[v for v in audit['votes'] if v['proposal_digest']==digest]
            if case['workflow_state']=='revision_required':
                chain=history(c,case['candidate_id'])
                revision_cases.append({'sample_id':case['sample_id'],'candidate_id':case['candidate_id'],
                    'audit_id':case['audit_id'],'revision':case['revision'],'revision_loop':chain,
                    'new_revision_created_this_task':False,'automatic_admission_granted':False})
            if case['sample_id'] in {'DEV-005','DEV-022','DEV-028'}:
                report=build_report(proposal,votes,(proposal.get('consensus') or {}).get('eligible_voter_ids'))
                dispute_cases.append({'sample_id':case['sample_id'],'candidate_id':case['candidate_id'],'audit_id':case['audit_id'],
                    'report':report,'new_focused_review_executed':False})
    result={'created_at_beijing':datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'revision_population':len(revision_cases),'model_requests':0,'historical_votes_changed':False,
        'revision_cases':revision_cases,'dispute_cases':dispute_cases,
        'split_example':{'sample_id':'DEV-016','children_created':False,'parent_ballot_reusable':False,
            'requirements':['每条子表达选择来源证据','逐条核验条件和集合含义','每个子草案独立审核','应用后逐条新版本证据核验与本体映射']}}
    assert len(revision_cases)==12
    (OUT/'inspection.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 修订闭环与具体争议排查','','本轮为本地只读排查，未新增真实模型审核票、未自动改写或拆分教材候选。',
        '','12条进入旧版修订队列不代表修订后通过，仍按各自候选版本的证据、本体映射和最终决定核验。',
        '','|样本|当前版本|已保存版本数|本轮自动准入|','|---|---|---|---|']
    for case in revision_cases:lines.append(f"|{case['sample_id']}|{case['revision']}|{len(case['revision_loop']['revisions'])}|无|")
    lines += ['','DEV-016没有被自动拆分。若提出拆分，每个子候选需来源、独立草案审核及新版本映射；父候选票不能作为子候选通过票。',
        '','## 争议问题','','DEV-005围绕“包含”是否被原文明确支持定向补证，不用2:1替代语义核验。']
    for case in dispute_cases:
        report=case['report'];lines.append(f"- {case['sample_id']}：{len(report['disputes'])}项具体分歧，{len(report['technical_seats'])}个无效或缺失席位；历史票未修改。")
        for dispute in report['disputes']:lines.append('  - '+dispute['question'])
    lines += ['','未发现反证与找到正面支持分别展示。无效席位属于技术或契约问题，不计作反对或争议解决。',
        '','本次没有真实复跑，不能据此声称语义分歧已消除或12条已入库。']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'revision_cases','dispute_cases'}},ensure_ascii=False))


if __name__=='__main__':main()
