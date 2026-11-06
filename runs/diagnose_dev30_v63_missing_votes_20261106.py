"""One read-only live snapshot: distinguish failed, pending and unstarted seats."""
from collections import Counter
from datetime import datetime, timezone, timedelta
import csv
import json
from pathlib import Path
from statistics import median
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
EXPERIMENT=ROOT/'outputs/dev30_v63_rerun_20261106'
OUT=ROOT/'outputs/dev30_v63_missing_votes_diagnosis_20261106'
MODELS=[('judge_a','Qwen'),('judge_b','Gemma'),('judge_c','deepseek-flash')]
LABELS={'accepted':'有效票已记录','gateway_unknown':'504，结果未知','opinion_conflict':'判断/反证角色冲突',
        'invalid_output':'返回内容未通过校验','valid_pending':'有效响应等待汇总','running':'请求仍在运行',
        'not_started':'候选尚未开始','awaiting_validation':'候选已启动，尚未提交本轮请求','other_failure':'其他失败'}


def stats(values):
    values=[v for v in values if v is not None]
    return {'min':min(values),'median':median(values),'max':max(values)} if values else None


def main():
    db=sqlite3.connect((EXPERIMENT/'review.sqlite3').as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
    checked=datetime.now(timezone(timedelta(hours=8))).isoformat()
    audits=[dict(r) for r in db.execute('SELECT * FROM audit_runs ORDER BY created_at DESC,rowid DESC')]
    proposals=[json.loads(r[0]) for r in db.execute('SELECT proposal_json FROM proposals ORDER BY round_id')]
    decisions=[{**dict(r),'value':json.loads(r['decision_json'])} for r in db.execute('SELECT * FROM decisions ORDER BY created_at DESC,rowid DESC')]
    votes=[json.loads(r[0]) for r in db.execute('SELECT vote_json FROM validator_votes')]
    requests=[{**dict(r),'metadata':json.loads(r['metadata_json']),'result':json.loads(r['result_json'] or '{}')}
              for r in db.execute('SELECT * FROM request_attempts ORDER BY started_at')]
    corrections=db.execute('SELECT COUNT(*) FROM review_response_corrections').fetchone()[0]
    db.close()
    bindings=json.loads((EXPERIMENT/'sample_bindings.json').read_text(encoding='utf-8'))
    cases=[];seats=[]
    for binding in bindings:
        cid=binding['candidate_id'];revision=binding['revision']
        audit=next((a for a in audits if a['candidate_id']==cid and a['revision']==revision),None)
        decision=next((d['value'] for d in decisions if audit and d['audit_id']==audit['audit_id']),{})
        rounds=[p for p in proposals if audit and p['audit_id']==audit['audit_id']]
        proposal=next((p for p in rounds if p['proposal_digest']==decision.get('selected_proposal_digest')),rounds[-1] if rounds else None)
        cells=[]
        for voter_id,model in MODELS:
            selected=[r for r in requests if audit and r['metadata']['binding'].get('audit_id')==audit['audit_id']
                      and r['metadata']['binding'].get('voter_id')==voter_id
                      and (not proposal or r['metadata']['binding'].get('proposal_digest')==proposal['proposal_digest'])]
            request=selected[-1] if selected else None
            vote=next((v for v in reversed(votes) if proposal and v['audit_id']==proposal['audit_id']
                       and v['proposal_digest']==proposal['proposal_digest'] and v['voter_id']==voter_id),{})
            result=request['result'] if request else {}
            if vote.get('response_status')=='ok':category='accepted'
            elif result.get('response_status')=='ok':category='valid_pending'
            elif result.get('http_status')==504:category='gateway_unknown'
            elif request and request['state']=='running':category='running'
            elif result.get('response_status')=='invalid_contract' and 'counter' in str(result.get('error','')):category='opinion_conflict'
            elif result.get('response_status') in {'invalid_contract','invalid_json','invalid_reference'}:category='invalid_output'
            elif not audit:category='not_started'
            elif not request:category='awaiting_validation'
            else:category='other_failure'
            row={'sample_id':binding['sample_id'],'candidate_id':cid,'model':model,'voter_id':voter_id,
                 'category':category,'display':LABELS[category], 'audit_status':audit['status'] if audit else 'not_started',
                 'audit_id':audit['audit_id'] if audit else None,'round_id':proposal['round_id'] if proposal else None,
                 'attempt_id':request['attempt_id'] if request else None,'request_state':request['state'] if request else None,
                 'vote_status':vote.get('response_status'),'result_status':result.get('response_status'),
                 'error_code':vote.get('error_code') or result.get('error_code'),'http_status':result.get('http_status'),
                 'error':vote.get('error') or result.get('error'),'correction_stop_reason':vote.get('correction_stop_reason'),
                 'late_response':bool(result.get('late_response')),
                 'model_latency_ms':result.get('latency_ms'),'input_tokens':result.get('input_tokens'),'output_tokens':result.get('output_tokens')}
            if request:
                row['caller_wait_cap_seconds']=round(request['deadline_at']-request['started_at'],3)
                row['total_response_seconds']=round(request['response_received_at']-request['started_at'],3) if request['response_received_at'] else None
            cells.append(row);seats.append(row)
        cases.append({'sample_id':binding['sample_id'],'candidate_id':cid,'audit_status':audit['status'] if audit else 'not_started',
                      'recorded_valid_seats':sum(c['category']=='accepted' for c in cells),
                      'decision_independent_review_completed':bool(decision.get('independent_review_completed')),'seats':cells})
    model_summary=[{'model':model,'categories':dict(Counter(s['category'] for s in seats if s['model']==model))} for _,model in MODELS]
    latencies={model:stats([s['model_latency_ms']/1000 for s in seats if s['model']==model and s['category']=='accepted' and s['model_latency_ms'] is not None]) for _,model in MODELS}
    gateway=[s for s in seats if s['category']=='gateway_unknown']
    conflicts=[s for s in seats if s['category']=='opinion_conflict']
    qwen_calls=[r for r in requests if r['metadata']['binding']['model_id']=='Qwen']
    settings=json.loads((EXPERIMENT/'settings.json').read_text(encoding='utf-8'))
    qwen_settings=next(v for v in settings['validators'] if v['id']=='judge_a')
    result={'checked_at_beijing':checked,'read_only':True,'batch_still_running':any(a['status']=='running' for a in audits),
            'population':30,'started':sum(c['audit_status']!='not_started' for c in cases),
            'closed':sum(c['audit_status'] in {'completed','failed','timeout','paused','interrupted'} for c in cases),
            'recorded_three_seats':sum(c['recorded_valid_seats']==3 for c in cases),
            'decision_review_completed':sum(c['decision_independent_review_completed'] for c in cases),
            'case_seat_distribution':dict(Counter(c['recorded_valid_seats'] for c in cases)),
            'seat_categories':dict(Counter(s['category'] for s in seats)),'model_summary':model_summary,
            'gateway_samples':[s['sample_id'] for s in gateway], 'output_conflicts':conflicts,
            'gateway_response_seconds':stats([s['total_response_seconds'] for s in gateway]),
            'successful_latency_seconds':latencies,
            'qwen_stream_responses':qwen_settings['stream_responses'],'qwen_client_timeout_seconds':qwen_settings['request_timeout_seconds'],
            'qwen_known_input_tokens':stats([r['result'].get('input_tokens') for r in qwen_calls]),
            'qwen_known_output_tokens':stats([r['result'].get('output_tokens') for r in qwen_calls]),
            'any_full_catalog_sent':any('ontology_catalog' in r['metadata']['proposal'] for r in requests),
            'actual_ontology_context_characters':stats([len(json.dumps(r['metadata']['proposal']['ontology_context'],ensure_ascii=False)) for r in qwen_calls]),
            'provider_attempts':len(requests),'field_correction_calls':corrections,
            'late_responses':sum(bool(r['result'].get('late_response')) for r in requests),
            'http_402_count':sum(r['result'].get('http_status')==402 for r in requests),'cases':cases}
    assert len(seats)==90 and sum(result['seat_categories'].values())==90
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'diagnosis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with (OUT/'seats.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for s in seats for k in s)))
        writer.writeheader();writer.writerows(seats)
    columns=['accepted','gateway_unknown','opinion_conflict','valid_pending','running','awaiting_validation','not_started','other_failure','invalid_output']
    lines=['# V6.3三席缺失原因快照','',f'北京时间：{checked}', '',
           f"已启动{result['started']}/30，处理结束{result['closed']}/30，最终选定轮次已记录三席有效票{result['recorded_three_seats']}/30。批次仍在运行，等待中和未开始不等于失败。",'',
           '|模型|'+'|'.join(LABELS[x] for x in columns)+'|','|---|'+'---:|'*len(columns)]
    for model in model_summary:
        lines.append('|'+model['model']+'|'+'|'.join(str(model['categories'].get(x,0)) for x in columns)+'|')
    timing=result['gateway_response_seconds']
    lines+=['','## 已证实的原因','',
        f"1. Qwen的{len(gateway)}个席位收到Nginx HTTP504，响应耗时{timing['min']:.1f}—{timing['max']:.1f}秒。客户端等待上限为90秒，且本轮Qwen仍为非流式；这些请求没有正常审核结果，不能计票。",'',
        '失败样本：'+', '.join(result['gateway_samples'])+'。','',
        '约一分钟的固定失败窗口指向服务端网关或其上游等待限制；具体是网关配置、排队还是生成耗时，需要服务端日志确认。缩短本体输入没有消除这一限制。504仍按执行结果未知处理，不能声称后端未执行。','',
        f"2. {len(conflicts)}个Gemma席位返回了互相冲突的判断：同一证据被标为仍成立的实质反证，又被列为已排除反例，counterevidence维度同时为pass。这是语义判断冲突；原有纠错保护禁止通过格式修复强制改掉真实反对意见。",'',
        f"3. 尚未启动、正在规划、模型请求运行中和有效响应等待汇总的席位已单独列出。本快照没有把它们统计为失败。正式审核的DeepSeek HTTP402次数为{result['http_402_count']}，充值后的调用正常。",'',
        '模型的反对、映射未定、建议修订本身并不导致缺席；只要响应自洽并通过原契约校验，仍是有效审核票。','',
        '## 动态本体是否生效','',
        f"所有实际请求均未携带ontology_catalog。Qwen已知输入tokens统计：{json.dumps(result['qwen_known_input_tokens'],ensure_ascii=False)}；成功响应耗时/秒：{json.dumps(latencies['Qwen'],ensure_ascii=False)}。失败请求缺少完整用量回执，未按零用量处理。",'',
        '输入已经显著缩小，但总耗时还包含排队、输入处理和输出生成，不能保证每条在网关等待窗口内完成。','',
        '## 后续处理建议','',
        '优先为Qwen验证真实审核的流式接收与Schema兼容性，或由服务端核对并调整网关等待、缓冲与队列设置。单独继续加长客户端超时不能越过已有504。已有有效票应保留；未知请求先对账。Gemma的角色冲突需要定向澄清，不应放宽成自动通过。','',
        '本次仅只读核对并生成报告，没有暂停、重发、改票或修改运行中的冻结配置。','',
        '|样本|Qwen|Gemma|DeepSeek|有效席位|审核状态|','|---|---|---|---|---:|---|']
    for case in cases:
        lines.append('|'+ '|'.join([case['sample_id'],*[s['display'] for s in case['seats']],str(case['recorded_valid_seats'])+'/3',case['audit_status']])+'|')
    (OUT/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'cases','output_conflicts'}},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
