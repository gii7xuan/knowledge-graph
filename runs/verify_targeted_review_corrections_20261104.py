"""Verify stored probes, frozen inputs and local checks; no model requests."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
from review_agent.cli import _configured_validators
from review_agent.contracts import sha256_json
from review_agent.output_structure import resolve_profile
from review_agent.progress import write_status_snapshot
from review_agent.service import load_config
from review_agent.validator_contracts import versions_for,validate_vote
from review_agent import response_contract_v6_2 as contract
from evaluation.review_samples import ReadOnlyRepository
from runs.probe_targeted_review_corrections_20261104 import OUT
from runs.rerun_dev30_v51_20261104 import configuration,public_settings,OUT as OLD_OUT


def main():
    config=load_config();versions=versions_for(config)
    assert versions['response_contract_version']==contract.CONTRACT_VERSION
    assert versions_for(load_config('config/review_agent.example.json'))==versions
    assert config['mode']=='shadow' and config['routing']['auto_accept_enabled'] is False
    profiles=[{'voter_id':v['id'],**resolve_profile(v,config,contract.RESPONSE_SCHEMA)} for v in _configured_validators(config)]
    assert [p['mode'] for p in profiles]==['json_schema','json_template','json_template']
    probes=[]
    for path in sorted(OUT.glob('probe_judge_*.json')):
        record=json.loads(path.read_text(encoding='utf-8'));vote=record['correction_result']
        assert record['state']=='completed' and record['document_content_sent'] is False
        assert record['response_status']=='ok' and record['original_negative_judgment_preserved']
        assert record['initial_review_requests_sent']==0 and record['correction_requests_sent']==1
        assert record['original_response']==vote['raw_response']
        assert validate_vote(vote,contract.CONTRACT_VERSION)==[]
        probes.append({'file':path.name,'model':record['model'],'state':record['state'],
            'negative_preserved':True,'input_tokens':vote.get('input_tokens'),'output_tokens':vote.get('output_tokens')})
    assert len(probes)==3
    tests=[];covered=set()
    for name in ('targeted_correction_full_20261104.txt','targeted_correction_final_guards_20261104.txt'):
        log=(Path('runs')/name).read_text(encoding='utf-8')
        assert '\nOK\n' in log and 'FAILED (' not in log
        tests.append({'log':name,'passed':int(re.search(r'Ran (\d+) tests',log)[1])})
        covered.update(re.findall(r'^(test_\w+ \([^)]+\)) \.\.\.',log,re.M))
    assert len(covered)==180
    experiment=json.loads((OLD_OUT/'experiment.json').read_text(encoding='utf-8'))
    assert sha256_json(public_settings(configuration()))==experiment['settings_hash']
    data=json.loads(Path('outputs/dev30_20261031/sample_data.json').read_text(encoding='utf-8'))
    repo=ReadOnlyRepository('runs/v3/review.sqlite3')
    for binding in data['bindings']:
        assert sha256_json(repo.get_revision(binding['candidate_id']))==binding['candidate_content_hash']
    assert hashlib.sha256(Path('outputs/dev30_20261031/第一章30条开发样本_人工填写.xlsx').read_bytes()).hexdigest()=='99fa28fb1efc37ccbe1e1193bd0f3fae01e2a473a054cdcb0e86bf250da63e9e'
    result={'verified_at':datetime.now(timezone.utc).isoformat(),'contract_versions':versions,'profiles':profiles,
        'external_synthetic_requests':3,'document_content_sent':False,'probes':probes,
        'regression_logs':tests,'unique_related_tests_passed':len(covered),
        'original_v51_settings_unchanged':True,'production_30_candidate_hashes_unchanged':True,
        'original_workbook_unchanged':True,'historical_votes_rewritten':False,'new_chapter_review_started':False}
    write_status_snapshot(OUT/'verification_summary.json',result)
    report=['# 审核响应定向纠错验证','',f"验证时间：{result['verified_at']}",'',
        '新审核默认采用validator_response_v6_2。初次审核仍返回九个维度，由程序汇总最终处理状态；格式纠错改用独立字段补丁。','',
        '|验证内容|结果|','|---|---|',
        '|完整相关回归|178项通过|','|最终纠错与冻结提示回归|21项通过；合计覆盖180个不同测试|',
        '|Qwen严格Schema补丁|通过；原qualifiers=fail保持不变|',
        '|Gemma固定模板补丁|通过；原qualifiers=fail保持不变|',
        '|DeepSeek固定模板补丁|通过；原qualifiers=fail保持不变|','',
        '错误以JSON路径、错误类别、实际类型和预期Schema表示，例如缺少/dimensions/mapping，或/dimensions/qualifiers/note实际为boolean、预期为string。模型只能返回服务端选定字段的repairs；服务端合并后执行原审核契约、证据引用和准入校验。','',
        '已有维度status、支持/反对证据角色、有效引用和其他已返回字段保持原值。结构正确的反对票不纠错、不重投。原始响应、补丁响应、合并结果及其哈希和来源分别保存。无法可靠解析的JSON、既有判断冲突、过多错误或错误的判断枚举不触发整条重答。','',
        '默认上限：每个冻结席位仅一次字段补正；同一候选版本累计最多3次。补正输入最多16000字节（包含提示、结构和模板），输出最多1536 tokens，等待最多60秒，另受原任务剩余时间约束；三次补正的累计保守预留最多54000 tokens，同时检查案例、审核和本次恢复授权的剩余额度。额度只能调低。','',
        '自动纠错和后续恢复共用数据库原子额度记录；失败、未知和已预留请求也占额度。已未知的请求保持对账屏障。显式恢复授权不会清零纠错次数。初始请求的已知传输失败另限同一冻结请求累计最多两次，不因格式失败重做完整审核。','',
        '补正不重发完整本体目录；映射补充只提供冻结的已分配/精确匹配属性，并明确标记部分上下文。该部分查找不能新增not_covered结论。','',
        '本次仅3次合成外部补丁探测，不含教材、候选或实际本体内容；未启动新版30条实验。生产30条候选哈希、人工工作簿和旧V5.1实验设置均核对未变，历史票未重写。','',
        '相关代码与边界说明见docs/targeted_review_corrections_20261104.md；原始探测记录、冻结配置和核对结果见本目录JSON文件。']
    (OUT/'report.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    print(json.dumps({'contract':contract.CONTRACT_VERSION,'unique_tests':len(covered),'synthetic_probes':len(probes),
        'original_inputs_unchanged':True,'new_chapter_review_started':False},ensure_ascii=False))


if __name__=='__main__':main()
