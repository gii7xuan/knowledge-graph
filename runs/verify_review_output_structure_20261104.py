"""Summarize existing probes and offline checks; performs no provider requests."""
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
from review_agent.validator_contracts import versions_for
from review_agent.validators import _parse_content
from review_agent import response_contract_v6_1 as contract
from evaluation.review_samples import ReadOnlyRepository
from runs.probe_review_output_structure_20261104 import OUT
from runs.rerun_dev30_v51_20261104 import configuration,public_settings,OUT as OLD_OUT


def main():
    config=load_config();assert config['validator_contract_version']==contract.CONTRACT_VERSION
    assert config['mode']=='shadow' and config['routing']['auto_accept_enabled'] is False
    assert versions_for(load_config('config/review_agent.example.json'))==versions_for(config)
    registry=json.loads((OUT/'capabilities.json').read_text(encoding='utf-8'))
    assert registry['schema_hash']==sha256_json(contract.RESPONSE_SCHEMA)
    profiles=[{'voter_id':v['id'],**resolve_profile(v,config,contract.RESPONSE_SCHEMA)} for v in _configured_validators(config)]
    assert [p['mode'] for p in profiles]==['json_schema','json_template','json_template']
    requests=[]
    for pattern in ('smoke_judge_*.json','nonce_confirmation_judge_*.json','actual_review_schema_judge_*.json','fixed_template_fallback_judge_*.json'):
        for path in sorted(OUT.glob(pattern)):
            r=json.loads(path.read_text(encoding='utf-8'));assert r['document_content_sent'] is False
            if r['stage'] in {'actual_review_schema','fixed_template_fallback'}:
                assert contract.validate_response(_parse_content(r['raw_response']))==[]
            requests.append({'file':path.name,'state':r['state'],'model':r['model'],
                **{k:r.get(k) for k in ('http_status','input_tokens','output_tokens','latency_ms','response_id')}})
    assert len(requests)==7 and not any(r['state'] in {'unknown','dispatching'} for r in requests)
    log=Path('runs/review_output_structure_full_20261104.txt').read_text(encoding='utf-8')
    assert '\nOK\n' in log and 'FAILED (' not in log
    count=int(re.search(r'Ran (\d+) tests',log)[1])
    original=json.loads((OLD_OUT/'experiment.json').read_text(encoding='utf-8'))
    assert sha256_json(public_settings(configuration()))==original['settings_hash']
    data=json.loads(Path('outputs/dev30_20261031/sample_data.json').read_text(encoding='utf-8'))
    repo=ReadOnlyRepository('runs/v3/review.sqlite3')
    for b in data['bindings']:assert sha256_json(repo.get_revision(b['candidate_id']))==b['candidate_content_hash']
    assert hashlib.sha256(Path('outputs/dev30_20261031/第一章30条开发样本_人工填写.xlsx').read_bytes()).hexdigest()=='99fa28fb1efc37ccbe1e1193bd0f3fae01e2a473a054cdcb0e86bf250da63e9e'
    result={'verified_at':datetime.now(timezone.utc).isoformat(),'contract_versions':versions_for(config),
        'selected_profiles':profiles,'external_probe_requests':len(requests),'document_content_sent':False,
        'requests':requests,'known_probe_usage':{k:sum(r.get(k) or 0 for r in requests) for k in ('input_tokens','output_tokens')},
        'requests_without_usage':sum(r.get('input_tokens') is None for r in requests),
        'offline_regression_tests':count,'offline_regression_passed':True,'unknown_probes_retried':False,
        'original_v51_settings_unchanged':True,'production_30_candidate_hashes_unchanged':True,
        'original_workbook_unchanged':True,'historical_votes_rewritten':False,'new_chapter_review_started':False}
    write_status_snapshot(OUT/'verification_summary.json',result)
    report=['# 审核输出结构约束探测与接入','',f"验证时间：{result['verified_at']}",'',
        '|当前入口/模型|实测结果|新审核模式|','|---|---|---|',
        '|Qwen（43.143.151.41）|两次唯一值约束测试及完整九维度Schema通过|严格JSON Schema|',
        '|Gemma（同入口）|接受参数但响应未遵守Schema；固定模板通过本地审核契约|固定模板＋JSON模式＋本地校验|',
        '|deepseek-flash（DeepSeek官方Chat入口）|json_schema返回HTTP 400；固定模板通过本地审核契约|固定模板＋JSON模式＋本地校验|','',
        f"新契约：{contract.CONTRACT_VERSION}。相关离线回归 {count} 项全部通过。",'',
        '共7次外部能力探测，仅发送合成任务和输出结构，没有发送教材、候选或本体内容。没有重发未知请求。',
        'Qwen能力结论绑定本次非流式配置，Gemma模板探测使用流式，DeepSeek使用当前thinking=disabled配置。',
        '模式选择按入口、模型、流式配置、thinking配置与Schema哈希匹配，未知能力使用模板。',
        '请求冻结后不再读取能力表重选模式；HTTP 400、504或格式错误不会静默切换模式另发请求。',
        '所有模式均检查结构、未替换的模板占位符、证据引用和审核语义。Schema合规不代表事实审核正确。',
        '未对30条样本发起新版重跑，未改写旧票、生产候选或人工工作簿。旧V5.1实验保留原配置与结果。','',
        '当前Chat入口的结论不代表供应商其他API协议。参考：[DeepSeek Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)；[vLLM结构化输出说明](https://docs.vllm.ai/en/latest/features/structured_outputs/)。',
        '完整探测响应见本目录各probe JSON文件；冻结模式和测试摘要见verification_summary.json。']
    (OUT/'report.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('offline_regression_tests','external_probe_requests','document_content_sent','new_chapter_review_started')},ensure_ascii=False))


if __name__=='__main__':main()
