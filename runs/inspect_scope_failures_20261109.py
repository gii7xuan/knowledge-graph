"""Read-only development inspection; sample IDs select reports, never decisions."""
from pathlib import Path
import hashlib
import json
from evaluation.review_samples import ReadOnlyRepository
from review_agent.semantic_clarification import pending
from review_agent.scope_policy import task_policy

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'outputs/dev30_v63_stream_rerun_20261106'
OUT=ROOT/'outputs/scope_failure_policy_20261109'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    database=SOURCE/'review.sqlite3';before=hashlib.sha256(database.read_bytes()).hexdigest()
    repo=ReadOnlyRepository(database)
    cases=json.loads((SOURCE/'comparison.json').read_text(encoding='utf-8'))['cases'];inspected=[]
    for case in cases:
        if case['sample_id'] not in {'DEV-028','DEV-029','DEV-030'}:continue
        audit=repo.get_audit(case['candidate_id'],case['audit_id'])
        votes=[]
        for vote in audit['votes']:
            raw=vote.get('raw_response') or {};raw=json.loads(raw) if isinstance(raw,str) else raw
            votes.append({'voter_id':vote['voter_id'],'response_status':vote['response_status'],
                'knowledge_scope':raw.get('dimensions',{}).get('knowledge_scope'),
                'clarification_required':pending(vote),'error':vote.get('error')})
        inspected.append({'sample_id':case['sample_id'],'candidate_id':case['candidate_id'],'audit_id':case['audit_id'],
            'historical_workflow':case['workflow_state'],
            'frozen_policy':audit['context'].get('admission_policy',{}).get('scope_policy'),
            'independent_review_completed':audit['context'].get('independent_review_completed'),
            'votes':votes,'planner_steps':[{'step_no':step['step_no'],'status':step.get('status'),
                'attempts':[{k:a.get(k) for k in ('response_status','finish_reason','error')} for a in step.get('attempts',[])]}
                for step in audit['planner_steps']]})
    after=hashlib.sha256(database.read_bytes()).hexdigest();assert before==after
    result={'source_database_hash':before,'historical_database_unchanged':True,'model_requests':0,
        'current_task_policy':task_policy(),'cases':inspected,'new_policy_reaudit_executed':False}
    (OUT/'inspection.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'report.md').write_text('''# 收录范围与故障处理排查

本次仅只读核对开发样本，未调用真实模型、未修改历史票或候选、未重新准入。

- DEV-029：三席均将其分类为 local_definition，旧配置排除此类别，按范围排除有一致的类别依据。
- DEV-028：三席原始输出均分类为 general，并非与 DEV-029 同类别。该条还有映射约束冲突及一席反证角色与 counterevidence=pass 的内部矛盾；不能把人工队列直接归因为范围规则不一致。
- DEV-030：规划器先完成一次动作，随后两个规划步骤各返回两次无效动作（issue/rationale 超长，finish_reason=stop）。未产生独立审核票；不是已经完成事实审核或模型拒绝候选。

新任务使用 knowledge_scope_v2：明确一般知识、条件性知识、局部定义、推导、示例、待确认六类边界。模型分类，程序依据冻结配置决定收录；无候选ID特判。

新任务中，规划器格式失败最多再试一次，随后执行固定最小审核，沿用原预算。内部语义矛盾只允许一次独立解释；原始票仍无效、保留原判断并交人工，不会改成通过票。

新逻辑已经用离线模型验证，以上历史样本尚未按新策略真实复跑，不能声称故障或争议已被真实模型消除。
''',encoding='utf-8')
    print(json.dumps({'cases':len(inspected),'model_requests':0,'historical_database_unchanged':before==after},ensure_ascii=False))


if __name__=='__main__':main()
