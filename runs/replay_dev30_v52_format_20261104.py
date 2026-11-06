"""Read-only compatibility replay; does not promote historical responses to votes."""
from collections import Counter
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

from evidence.verification import verify_vote_references
from evaluation.review_samples import ReadOnlyRepository
from review_agent import response_contract_v5_1 as v51,response_contract_v5_2 as v52
from review_agent.contracts import sha256_json
from review_agent.progress import write_status_snapshot
from review_agent.validator_contracts import proposal_errors,versions_for
from review_agent.validators import _parse_content

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'outputs/dev30_v51_rerun_20261104'
OUT=ROOT/'outputs/dev30_v52_format_compat_20261104'


def semantic_projection(raw):
    result=deepcopy(raw)
    result.setdefault('schema_change_hint',None)
    for check in result.get('checks',[]):
        if isinstance(check,dict):check.pop('issue_code',None)
    if isinstance(result.get('schema_assessment'),dict):result['schema_assessment'].pop('issue_code',None)
    return result


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    repo=ReadOnlyRepository(SOURCE/'review.sqlite3')
    saved=json.loads((SOURCE/'settings.json').read_text(encoding='utf-8'))
    assert versions_for({'validator_contract_version':v51.CONTRACT_VERSION})['response_contract_hash']==saved['versions']['response_contract_hash']
    assert v51.PROMPT_HASH==saved['versions']['prompt_hash']
    with repo.connect() as conn:
        snapshot=[dict(r) for r in conn.execute("SELECT attempt_id,state,result_json FROM request_attempts WHERE state='failed' ORDER BY started_at")]
    proposals={};rows=[];by_model={};paths=Counter()
    for attempt in snapshot:
        stored=json.loads(attempt['result_json'] or '{}')
        if stored.get('response_status')!='invalid_contract' or 'raw_response' not in stored:continue
        try:raw=_parse_content(stored['raw_response'])
        except (ValueError,TypeError):continue
        old_errors=v51.validate_response(raw)
        new_errors=v52.validate_response(raw)
        canonical,changes=v52.canonicalize(raw)
        assert semantic_projection(raw)==semantic_projection(canonical),'format normalization changed substantive content'
        normalized=None
        if not new_errors:
            audit_id=stored['audit_id']
            if audit_id not in proposals:
                audit=repo.get_audit(stored['task_id'],audit_id)
                proposals[audit_id]={p['proposal_digest']:p for p in audit['rounds']}
            original=proposals[audit_id][stored['proposal_digest']]
            new_errors.extend(proposal_errors(raw,{**original,'response_contract_version':v52.CONTRACT_VERSION}))
            normalized=v52.normalize(raw)
            normalized['schema_assessment']=deepcopy(raw['schema_assessment'])
            assert semantic_projection(v52.raw_from_vote(normalized))==semantic_projection(raw)
            refs=verify_vote_references({**normalized,'response_status':'ok','response_contract_version':v52.CONTRACT_VERSION},original['evidence_bundle']['items'])
            if refs.get('response_status')!='ok':new_errors.append(refs.get('error') or refs.get('response_status'))
        accepted=bool(old_errors) and not new_errors
        model=stored.get('model_id') or 'unknown'
        totals=by_model.setdefault(model,Counter())
        totals['replayed_failed_responses']+=1
        totals['auxiliary_changes_present']+=bool(changes)
        totals['acceptable_under_new_contract']+=accepted
        totals['still_rejected']+=bool(new_errors)
        paths.update(c['path'].rsplit('/',1)[-1] for c in changes)
        rows.append({'attempt_id':attempt['attempt_id'],'original_request_key':stored.get('request_key'),
            'original_audit_id':stored['audit_id'],'original_proposal_digest':stored['proposal_digest'],
            'model_id':model,'original_contract':v51.CONTRACT_VERSION,'evaluated_contract':v52.CONTRACT_VERSION,
            'stored_result_hash':sha256_json(stored),'original_raw_response':deepcopy(stored['raw_response']),
            'original_errors':old_errors,'new_errors':new_errors,'format_normalizations':changes,
            'acceptable_under_new_contract':accepted,'semantic_content_unchanged':True,
            'original_vote_promoted':False,'provider_calls':0})
    data=json.loads((ROOT/'outputs/dev30_20261031/sample_data.json').read_text(encoding='utf-8'))
    live=ReadOnlyRepository(ROOT/'runs/v3/review.sqlite3')
    for b in data['bindings']:
        assert sha256_json(live.get_revision(b['candidate_id']))==b['candidate_content_hash']
    book=ROOT/'outputs/dev30_20261031/第一章30条开发样本_人工填写.xlsx'
    assert hashlib.sha256(book.read_bytes()).hexdigest()=='99fa28fb1efc37ccbe1e1193bd0f3fae01e2a473a054cdcb0e86bf250da63e9e'
    summary={'snapshot_at_utc':datetime.now(timezone.utc).isoformat(),
        'source_database':str(repo.database_path),'failed_attempts_observed':len(snapshot),
        'replayed_contract_failures':len(rows),'acceptable_under_new_contract':sum(x['acceptable_under_new_contract'] for x in rows),
        'still_rejected':sum(bool(x['new_errors']) for x in rows),'per_model':{k:dict(v) for k,v in by_model.items()},
        'semantic_content_unchanged':True,'provider_calls':0,'database_writes':0,'old_votes_promoted':False,
        'original_v51_hashes_unchanged':True,'source_workbook_unchanged':True,'production_30_hashes_unchanged':True,
        'versions':versions_for({'validator_contract_version':v52.CONTRACT_VERSION})}
    payload={'summary':summary,'snapshot_hash':sha256_json(snapshot),'rows':rows}
    write_status_snapshot(OUT/'replay.json',payload)
    lines=['# 辅助格式兼容离线回放','',
        f"回放快照：{summary['snapshot_at_utc']}",
        f"回放 {len(rows)} 份原契约失败响应，其中 {summary['acceptable_under_new_contract']} 份可被新契约接收，{summary['still_rejected']} 份仍存在其他问题。",'',
        '这是对既有响应的离线验证，不是新增模型审核票，也没有恢复或重发未知请求。',
        '实际新模型响应的稳定性仍需另行验证。本次修改不改写正在运行的V5.1实验或其统计。','',
        '|模型|回放失败响应|新契约可接收|仍有问题|','|---|---:|---:|---:|']
    for model,counts in by_model.items():
        lines.append(f"|{model}|{counts['replayed_failed_responses']}|{counts['acceptable_under_new_contract']}|{counts['still_rejected']}|")
    lines+=['','只规范化辅助诊断码的null、空白和缺省形式，以及可选空提示；所有状态、理由、映射、证据ID及角色保持原值。',
        '原工作簿和生产库中30条候选的哈希核对一致。模型调用0次，数据库写入0次。',
        '逐条原始响应、兼容变更及剩余错误见 replay.json。']
    (OUT/'replay.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
