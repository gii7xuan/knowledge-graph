"""Read-only field diagnosis of the four reported examples; no new model votes."""
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from evaluation.review_samples import ReadOnlyRepository
from review_agent.contracts import sha256_json
from review_agent.field_spec import specification, prepare

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'outputs/dev30_v63_stream_rerun_20261106'
OUT=ROOT/'outputs/field_rules_20261107'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    repo=ReadOnlyRepository(SOURCE/'review.sqlite3')
    old=json.loads((SOURCE/'comparison.json').read_text(encoding='utf-8'))
    entries={};cases=[];spec=specification()
    for case in old['cases']:
        if case['sample_id'] not in {'DEV-002','DEV-019','DEV-020','DEV-021'}:continue
        candidate=repo.get_revision(case['candidate_id'],case['revision'])
        audit=repo.get_audit(candidate['candidate_id'],case['audit_id'])
        digest=repo.latest_decision(candidate['candidate_id'])['selected_proposal_digest']
        proposal=next(p for p in audit['rounds'] if p['proposal_digest']==digest)
        assert sha256_json(candidate)==sha256_json(proposal['candidate'])
        report=prepare(candidate,proposal['evidence_bundle'],spec)
        binding={'audit_id':audit['audit_id'],'proposal_digest':digest,'candidate_hash':sha256_json(candidate),
            'evidence_hash':proposal['evidence_bundle']['evidence_hash'],'ontology_context_hash':proposal['ontology_context_hash']}
        entries[audit['audit_id']]={'binding':binding,'report':report,'report_hash':sha256_json(report)}
        cases.append({'sample_id':case['sample_id'],'candidate_id':candidate['candidate_id'],'audit_id':audit['audit_id'],
            'plans':[{'rule_id':p['rule_id'],'operation':p['operation'],'value':p['value']} for p in report['plans']],
            'semantic_review':report['semantic_review'],'original_expression':report['original_expression'],
            'normalized_expression':report['normalized_expression'],'report_hash':sha256_json(report)})
    assert len(cases)==4
    summary={'created_at_beijing':datetime.now(timezone(timedelta(hours=8))).isoformat(),'spec_version':spec['version'],
        'spec_hash':sha256_json(spec),'model_requests':0,'historical_votes_changed':False,
        'original_candidates_changed':False,'admission_authorized':False,'cases':cases}
    (OUT/'diagnosis.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (SOURCE/'field_diagnostics.json').write_text(json.dumps({'version':'post_run_field_diagnostics_v1','entries':entries},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 四个字段口径问题的独立诊断','','使用原候选和原冻结证据；没有重发模型请求、修改历史票或覆盖候选。',
        '规范化结果为另存的派生表达，尚不等于语义核验通过或可入库。','',
        '|样本|确定性结果|仍需语义核验|','|---|---|---|']
    for c in cases:
        changes='；'.join(p['rule_id']+' '+p['operation']+'：'+p['value'] for p in c['plans']) or '未自动修改'
        reviews='；'.join(r['rule_id']+' '+r['reason'] for r in c['semantic_review']) or '完整原意、限定附着和条件保留仍需审核'
        lines.append('|'+c['sample_id']+'|'+changes+'|'+reviews+'|')
    lines += ['','DEV-002 不根据“分别”猜测对象配对；DEV-019 将独立“常常”另存为频率；DEV-020 原样保留“总是”并清除关系中的精确重复，条件不动；DEV-021 的 can 保持原样，需结合定义公式和上下文核验。',
        '','这些诊断不重新分类旧模型的意见；新规则下的真实效果需另行复跑验证。']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'population':len(cases),'plans':sum(len(c['plans']) for c in cases),'model_requests':0,
        'samples':[{k:c[k] for k in ('sample_id','plans','semantic_review')} for c in cases]},ensure_ascii=False))


if __name__=='__main__':main()
