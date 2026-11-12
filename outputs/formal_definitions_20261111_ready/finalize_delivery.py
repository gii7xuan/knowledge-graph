"""Read and reconcile saved receipts only; no new model calls or publication."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from evaluation.review_followup import read,write,digest
from review_agent.repository import ReviewRepository
from ontology_review.validators import response_errors,consensus
from ontology_review.contracts import RESPONSE_SCHEMA,vote_binding
from ontology_review.publisher import current_release_id,OntologyPublisher
from runs.formal_definitions_20261111 import now


def main():
    out=Path(__file__).resolve().parent;m=read(out/'prepared_manifest.json');p=read(out/'prepared_proposal.json')
    repo=ReviewRepository(out/'review.sqlite3')
    with repo.connect() as c:stored=c.execute('SELECT * FROM request_attempts').fetchall()
    rows=[]
    for r in stored:
        meta=json.loads(r['metadata_json']);z=json.loads(r['result_json'] or '{}')
        if (meta.get('proposal') or {}).get('task_id')==m['extension_id']:
            rows.append({'record':dict(r),'metadata':meta,'result':z})
    assert len(rows)==5
    qwen=next(r for r in reversed(rows) if r['metadata']['binding']['voter_id']=='judge_a')
    assert qwen['record']['state']=='completed' and qwen['result']['response_status']=='ok'
    new=qwen['result'];member=next(x for x in p['committee_members'] if x['id']=='judge_a')
    assert all(new[k]==v for k,v in vote_binding(member,p).items())
    assert not response_errors({k:new[k] for k in RESPONSE_SCHEMA['required']},p)
    audit=repo.ontology_audit(m['audit_id'])
    previous=next(v for v in audit['votes'] if v['voter_id']=='judge_a')
    if previous['response_status']!='ok':repo.ontology_recovered_vote(new)
    else:assert previous['response_id']==new['response_id']
    write(out/'qwen_field_recovery_late_reconciliation.json',{'at':now(),'old_caller_envelope':previous,
        'actual_response':new,'attempt_id':qwen['record']['attempt_id'],'new_model_calls':0,
        'judgment_changed_by_program':False})
    votes=repo.ontology_audit(m['audit_id'])['votes'];final=consensus(votes,p)
    task=repo.extension_status(m['extension_id'])
    repo.finish_ontology_audit(m['audit_id'],{'extension_id':m['extension_id'],'recommendation':'needs_human',
        'task_status':'needs_human','selected_round_id':p['round_id'],'selected_proposal_digest':p['proposal_digest'],
        'consensus':final,'stop_reason':'Two valid modeling approvals; Gemma gateway outcome unknown; no resend or publication.'},task['row_version'])
    ledger=[]
    for row in rows:
        r,meta,z=row['record'],row['metadata'],row['result']
        ledger.append({'attempt_id':r['attempt_id'],'request_key':r['request_key'],
            'voter_id':meta['binding']['voter_id'],'state':r['state'],'started_at':r['started_at'],
            'deadline_at':r['deadline_at'],'response_received_at':r['response_received_at'],
            'response_status':z.get('response_status'),'decision':z.get('decision'),
            'error_code':z.get('error_code'),'error':z.get('error'),'input_tokens':z.get('input_tokens'),
            'output_tokens':z.get('output_tokens'),'latency_ms':z.get('latency_ms'),
            'response_id':z.get('response_id'),'provider_request_id':r['provider_request_id'],
            'feedback':meta.get('feedback'),'actual_receipt':z})
    write(out/'new_request_ledger.json',ledger)
    current={v['voter_id']:v for v in votes};assert current['judge_a']['response_status']==current['judge_c']['response_status']=='ok'
    assert current['judge_a']['decision']==current['judge_c']['decision']=='approve'
    assert current['judge_b'].get('outcome_unknown')
    request_fences=[{'attempt_id':r['attempt_id'],'request_key':r['request_key'],'model':r['voter_id'],
        'started_at_epoch':r['started_at'],'http_status':504,'provider_request_id':r['provider_request_id'],
        'source_proposal_digest':p['proposal_digest'],'delta_hash':p['delta_hash'],'status':'provider_outcome_unknown',
        'local_request_key_is_provider_id':False,'automatic_resend_allowed':False,
        'resolution':'查询网关/上游日志以确认原请求实际完成状态；仅对账原请求，不换命名空间重发。'}
        for r in ledger if r['state'] in {'unknown','running'}]
    write(out/'gateway_reconciliation_required.json',request_fences)
    protected=read(out/'protected_inputs.json');unchanged=all(digest(Path(k))==v for k,v in protected.items());assert unchanged
    assert current_release_id(ROOT/'runs/ontology_releases')==m['production_release_head']
    assert current_release_id(out/'releases')==m['production_release_head']
    prior=read(out/'final_summary.json');write(out/'summary_before_field_recovery_reconciliation.json',prior)
    costs={'reported_input_tokens':sum(r['input_tokens'] or 0 for r in ledger),
        'reported_output_tokens':sum(r['output_tokens'] or 0 for r in ledger),
        'usage_missing_requests':sum(r['input_tokens'] is None or r['output_tokens'] is None for r in ledger),
        'currency_cost':None}
    summary={'at':now(),'definitions_prepared':8,'local_guard_pass':8,'new_model_calls':5,'authorized_max_calls':6,
        'effective_modeling_votes':[{k:v.get(k) for k in ('voter_id','response_status','decision','error_code','outcome_unknown')} for v in votes],
        'valid_modeling_approvals':2,'semantic_competency_executed':False,'semantic_competency_passed':False,
        'remaining_unknown_requests':request_fences,'isolated_release_published':False,'production_release_changed':False,
        'formal_definition_coverage_in_production':0,'production_written':False,'protected_inputs_unchanged':unchanged,
        'costs':costs,'consensus':final,'historical_unknown_requests_not_retried':True}
    write(out/'final_summary.json',summary);write(out/'progress.json',{'active':False,**summary})
    spec=read(ROOT/'specs/formal_definition_candidates_20261111.json');packets={x['sample_id']:x for x in read(ROOT/'outputs/dev30_v8_full_20261111/review_packets.json')['samples']}
    text=['# 正式定义处理结果','',
        '已完成8项来源绑定定义草案及原三席审核尝试，获得Qwen、DeepSeek两张有效批准票；Gemma网关504结果未知。尚未完成原三席门槛及整章能力问题，未生成已批准的新本体版本，也未切换生产。','',
        '## 定义正文与来源','',
        '|对应案例|既有关系ID|定义|来源ID|','|---|---|---|---|',
        *[f"|{x['sample_id']}|{x['relation_id']}|{x['definition']}|{', '.join(x['evidence_ids'])}|" for x in spec['definitions']],
        '', '全部定义只在UNAPPROVED_ontology.ttl与精确补丁中；源RDF 882条变为890条，新增8条skos:definition、删除0条；81个属性的ID、标签、domain/range、别名和类层级均保留。',
        '8个对应案例在隔离的元数据/具名约束guard由未通过变为pass；这不是正式语义映射通过，旧候选仍绑定原发布版。guard_effect_all30.jsonl保留全部30条前后结果，DEV-028的真实range冲突继续fail。','',
        '## 真实审核与对账','',
        '|席位|当前有效结果|处理情况|','|---|---|---|',
        '|Qwen|ok / approve|首轮两处可选evidence_scope枚举错误；一次错误反馈后返回合格票，晚到结果已离线对账；原记录保留。|',
        '|Gemma|api_error / outcome unknown|HTTP504，保留请求围栏，未重发。|',
        '|DeepSeek|ok / approve|首轮一个证据ID重复字符；一次错误反馈后引用校验通过；原记录保留。|',
        '', '共5次调用，未超过已授权6次。字段纠正使用既有失败席位恢复机制，源定义/证据/约束未改，无新增席位或投票权重。没有将格式非法的原approve直接改成有效票。',
        '整章chapter1_source_coverage仍未执行本批评估。原章节全文和完整RDF的能力问题已冻结在prepared_competency_request.json；取得Gemma确定结果且建模门槛满足后才能继续，不用局部guard通过替代。',
        f"已报告输入{costs['reported_input_tokens']}、输出{costs['reported_output_tokens']} token，{costs['usage_missing_requests']}次用量缺失；不据此估完整总量或金额。",'',
        '## 另外4个当前引用关系','',
        *[f"- {x['sample_id']} / {x['relation_id']}：{x['reason']}" for x in spec['held_relations']],
        '', 'held_relation_dossiers.json附DEV-019补充原句、DEV-022投影输入/结果原句、DEV-028正确科氏项原句与未改变的原约束。DEV-008继续复用原任务，不用新批次重投其未知/有效负面结果。','',
        '## 交付与下一步','',
        '- prepared_proposal.json / prepared_validator_payload.json：精确审核提案与无损本体交付。',
        '- owl_patch.json / UNAPPROVED_ontology.ttl：可审阅的定义增补，不是正式发布版本。',
        '- final_summary.json / new_request_ledger.json：本轮真实结果、费用用量缺失和全部请求终态。',
        '- gateway_reconciliation_required.json：Gemma原请求的对账信息；本地request_key不是服务商请求ID。',
        '- offline_verification.json：RDF重建哈希、全部约束不变、8条局部guard以及30条影响检查。',
        '', '当前必须先对账Gemma原请求。若为有效批准，再按原三席完成整章能力审核；若有有效修订/反对，先处理其具体问题。现有授权剩余额度不能成为换请求身份或重发未知请求的理由。生产发布头、历史候选、旧标签和原审核库的SHA256保护核验通过。','']
    (out/'report.md').write_text('\n'.join(text),encoding='utf-8')
    write(out/'delivery_verification.json',{'definition_drafts':8,'local_guard_pass':8,'model_approvals':2,
        'model_calls':5,'model_calls_within_authorized_six':True,'gateway_unknown_requests':len(request_fences),
        'rdf_hash_and_constraint_checks':read(out/'offline_verification.json'),
        'no_production_writes':True,'no_formal_release_claim':True,'historical_records_unchanged':unchanged,
        'source_proposal_sha256_unchanged':digest(out/'prepared_proposal.json')==m['proposal_sha256']})
    print(json.dumps({'drafts':8,'valid_approvals':2,'calls':5,'unknown_gateway_requests':len(request_fences),'published':False},ensure_ascii=False))


if __name__=='__main__':main()
