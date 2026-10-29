"""Freeze the corrected review version for a live canary and the resumed batch."""
from datetime import datetime,timezone
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'runs/review_jobs/chapter1_20261028_review_retry2'
CHECK=ROOT/'runs/review_jobs/chapter1_20261028_contract_fix'
CANDIDATES=['cand_7619a3990068828ab44a','cand_261ddb15812406cc8f99','cand_0327302655245f377c35']


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--canary-name',default='chapter1_20261028_planner_v3_canary')
    parser.add_argument('--refresh-batch',action='store_true')
    args=parser.parse_args()
    from review_agent.batch import versions_for
    from review_agent.service import load_config
    files=['review_agent/planner_contracts.py','prompts/triple_planner_v3.txt','review_agent/action_policy.py',
        'review_agent/planner.py','review_agent/planner_view.py','review_agent/agent_state.py','review_agent/runtime.py',
        'review_agent/tools.py','review_agent/orchestrator.py','review_agent/repository.py','review_agent/batch.py']
    hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
    config=load_config(SOURCE/'review_config.json')
    for name,selected in [(args.canary_name,CANDIDATES),('chapter1_20261028_planner_v3',None)]:
        job=SOURCE.parent/name
        if job.parent!=SOURCE.parent: raise ValueError('job must stay inside review_jobs')
        if job.exists():
            if not args.refresh_batch or selected is not None or (job/'status.json').exists():
                raise FileExistsError('job already exists or was started')
            shutil.copy2(job/'execution_plan.json',job/'execution_plan_before_canary_refinement.json')
        else: job.mkdir(exist_ok=False)
        for artifact in ('model_probe.json','destination_authorization.json','preflight.json'):
            shutil.copy2(SOURCE/artifact,job/artifact)
        (job/'review_config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
        plan={'run_id':'run_chapter1_20261028_resume1','job_id':name,'review_versions':versions_for(config),
            'candidate_ids':selected,'source_files_sha256':hashes,'previous_job':str(SOURCE),
            'created_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'existing_audits_preserved':True,'case_budgets_reset':False,
            'old_no_vote_completions_are_not_reusable':True,'candidate_parallelism':config['budget']['max_parallel_candidates']}
        (job/'execution_plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
        (job/'README.md').write_text('# 第一章审核：规划契约 v3\n\n使用严格动作契约、真实证据 ID、受控前置条件及精简规划视图。\n\n'
            '- [实时状态](status.json)\n- [执行计划与代码摘要](execution_plan.json)\n- [标准输出](stdout.log)\n- [错误日志](stderr.log)\n\n'
            'completed 为执行结束数量，independent_review_completed 才表示同一当前提案已完成全部委员的有效独立复核。review_incomplete 单独记录降级、无证据或未完成委员审核的结果。\n\n'
            '旧记录和案例预算保留；契约版本变化后，旧的无投票完成记录不会被跳过。源文档、本体及三模型配置保持不变。\n',encoding='utf-8')
        print(json.dumps({'job':str(job),'candidate_ids':selected,'planner_contract_version':plan['review_versions']['planner_contract_version']},ensure_ascii=False))


if __name__=='__main__': main()
