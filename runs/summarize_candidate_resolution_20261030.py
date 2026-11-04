"""Audit-safe inventory and a concrete human disagreement worklist."""
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import hashlib
import json
import sqlite3
from review_agent.repository import ReviewRepository
from review_agent.revision_service import RevisionService
from review_agent.contracts import sha256_json

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/candidate_revision_resolution_20261030'
RUN='run_chapter1_20261028_resume1'
NOTES={
 'cand_f89f9bd12a09240375b7':'三席文字均认定小章动角的简化假设不是反证，但原票仍有 counter_evidence_ids 或检查引用不一致。已保留原票，需原审核员澄清字段；不得把人工解释替换成三席票。',
 'cand_c4ca4af2f9fe7df47915':'新增 ev_d935917f356ac1bb7edc 是标准弹定义的直接原句；修订已应用，三席新版本审核均支持。但 Gemma 的 qualifiers 检查为 pass 且证据 ID 为空，因此仍未形成来源共识。应保留原票、另附澄清记录或经受控人工证据包确认，不能自动补写其证据 ID。',
 'cand_808d8f2198c5723ef4cc':'现有原文只直接说表1-4考虑相关影响，未找到以弹丸运动方程为主体的直接句。保留支持不足意见；需决定补证或改抽表述，不能仅改类型凑约束。',
 'cand_087e253eb4e4af8bcf40':'原文为“假定”。中央契约已补入 assumed；须通过独立修订审核使用该假设模态，不能将原 asserted 版本当作无条件事实通过。',
 'cand_c0f73fb07469b204dbf7':'三席已收齐修订审核。Qwen 承认编制主体应为表，但认为“高射火炮外弹道射表”是在跨句指代中构造的完整名称，未在主证据中直接出现。新草案未应用；需人工核定“上述高射炮”的省略指代，不能反复更换同义名称去获取批准。',
 'cand_22c5626b36b51df8ff34':'三席均支持“湍流边界层是边界层”的来源内容，但 Gemma 认为“在层流边界层后面”修饰空间转化过程，不应作为分类关系的成立条件。保留现有条件和原票，需人工决定分类表达与过程表达的建模范围；不直接删除原句限制。',
 'cand_ca27ef4e78caeece9179':'投影主体应为矢量方程；本批草案保留了原假设。Qwen 票承认主体修正，却因自身保护证据评估/字段状态未齐而拒绝；Gemma 仍有契约失败。需澄清原票，不能重试有效拒绝去取批准。',
 'cand_fa077f9f4d1e0a98ef2a':'既有修订把气象标准定律改成标准气象条件、把计算空气阻力改成弹道，Qwen 已有效反对。保留 needs_human，不应用仅为满足 domain/range 的类型猜测。',
 'cand_f9a86d764470a851cd8a':'既有修订建议把“弹道系数 C”换成 C_e4f3000a2284a7d0fa12（空气阻力函数），两者并非同一源概念；有有效反对，不应用。应调查“表示”关系的复用语义/约束，不能从 domain/range 反推实体类别。',
 'cand_f4876022d0c83d291ac3':'模型建议把“弹道系数”换成“空气阻力函数”以满足“表示”的 domain/range；本体定义显示该类指速度影响部分，与本条弹丸本身特征不同。应调查既有关系语义或约束，不直接套换类型。',
}


def digest(value):return hashlib.sha256(value.encode()).hexdigest()


def main():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    backup=OUT/('after_'+stamp+'.sqlite3')
    source=sqlite3.connect((ROOT/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=15)
    target=sqlite3.connect(backup)
    try:source.backup(target)
    finally:target.close();source.close()
    repo=ReviewRepository(backup);revisions=RevisionService(repo)
    before_path=Path(json.loads((OUT/'diagnosis.json').read_text(encoding='utf-8'))['backup'])
    before=sqlite3.connect(before_path);before.row_factory=sqlite3.Row
    checked=Counter()
    baseline_counts=Counter()
    for row in before.execute('SELECT a.context_json,d.decision_json FROM candidates c LEFT JOIN decisions d ON d.decision_id=c.latest_decision_id LEFT JOIN audit_runs a ON a.audit_id=(SELECT x.audit_id FROM audit_runs x WHERE x.candidate_id=c.candidate_id ORDER BY x.created_at DESC,x.rowid DESC LIMIT 1) WHERE c.run_id=?',(RUN,)):
        context=json.loads(row['context_json'] or '{}');decision=json.loads(row['decision_json'] or '{}')
        baseline_counts['independent_completed' if context.get('independent_review_completed') else 'independent_incomplete']+=1
        baseline_counts['source_'+str(decision.get('source_support_result'))]+=1
    with repo.connect() as after:
        for table,keys in [('validator_votes',['audit_id','round_id','voter_id']),('revision_votes',['draft_id','voter_id']),('ontology_votes',['audit_id','round_id','voter_id'])]:
            for row in before.execute('SELECT * FROM '+table+" WHERE json_extract(vote_json,'$.response_status')='ok'"):
                stored=after.execute('SELECT vote_json FROM '+table+' WHERE '+' AND '.join(k+'=?' for k in keys),[row[k] for k in keys]).fetchone()
                if not stored or digest(stored[0])!=digest(row['vote_json']):raise RuntimeError('original valid vote changed: '+table)
                checked[table]+=1
        for row in before.execute('SELECT candidate_id,revision,candidate_json FROM candidate_revisions'):
            stored=after.execute('SELECT candidate_json FROM candidate_revisions WHERE candidate_id=? AND revision=?',(row['candidate_id'],row['revision'])).fetchone()
            if not stored or digest(stored[0])!=digest(row['candidate_json']):raise RuntimeError('historical candidate content changed')
            checked['candidate_revisions']+=1
        for row in before.execute('SELECT audit_id,round_id,proposal_json FROM proposals'):
            stored=after.execute('SELECT proposal_json FROM proposals WHERE audit_id=? AND round_id=?',(row['audit_id'],row['round_id'])).fetchone()
            if not stored or digest(stored[0])!=digest(row['proposal_json']):raise RuntimeError('historical source proposal changed')
            checked['source_proposals']+=1
        old_unknown=list(before.execute("SELECT request_key,status FROM validation_requests WHERE status IN ('unknown','running')"))
        unknown_changes=[{'request_key':r['request_key'],'before':r['status'],'after':(after.execute('SELECT status FROM validation_requests WHERE request_key=?',(r['request_key'],)).fetchone() or ['missing'])[0]} for r in old_unknown]
        unknown_changes=[r for r in unknown_changes if r['before']!=r['after']]
        before_candidates={r['candidate_id']:r['current_revision'] for r in before.execute('SELECT candidate_id,current_revision FROM candidates WHERE run_id=?',(RUN,))}
        old_drafts={r[0] for r in before.execute('SELECT draft_id FROM revision_drafts')}
        previously_applied={r[0] for r in before.execute("SELECT draft_id FROM revision_drafts WHERE status='applied'")}
        newly_applied=[r[0] for r in after.execute("SELECT draft_id FROM revision_drafts WHERE status='applied'") if r[0] not in previously_applied]
        new_drafts=[revisions.draft(r[0]) for r in after.execute('SELECT draft_id FROM revision_drafts') if r[0] not in old_drafts]
        draft_candidates={r['draft_id']:r['candidate_id'] for r in after.execute('SELECT draft_id,candidate_id FROM revision_drafts')}
        old_attempt_ids={r[0] for r in before.execute('SELECT attempt_id FROM request_attempts')}
        cost_by_candidate={};cost_total=Counter();request_states=Counter()
        for row in after.execute('SELECT * FROM request_attempts'):
            if row['attempt_id'] in old_attempt_ids:continue
            request_states[row['state']]+=1
            metadata=json.loads(row['metadata_json']);result=json.loads(row['result_json'] or '{}')
            binding=metadata.get('binding',{});task_id=binding.get('task_id');identity=draft_candidates.get(task_id,task_id)
            cost=cost_by_candidate.setdefault(identity,Counter())
            cost['attempt_records']+=1;cost_total['attempt_records']+=1
            if result.get('actual_calls')==0:continue
            for account in (cost,cost_total):
                account['provider_attempts']+=1
                account['outcome_unknown']+=bool(result.get('outcome_unknown'))
                account['known_input_tokens']+=result.get('input_tokens') or 0
                account['known_output_tokens']+=result.get('output_tokens') or 0
                account['missing_usage_attempts']+=result.get('input_tokens') is None or result.get('output_tokens') is None
        db_counts={table:{'before':before.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],'after':after.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]} for table in ('import_outbox','admission_authorizations','request_attempts')}
        jobs=[dict(row) for row in after.execute("SELECT job_id,job_type,status FROM jobs WHERE status IN ('running','queued')")]
        reconciliation=[]
        for request in after.execute("SELECT request_key,status,vote_json,updated_at FROM validation_requests WHERE status IN ('unknown','running') ORDER BY updated_at"):
            vote=json.loads(request['vote_json'] or '{}')
            attempts=[];binding={}
            for attempt in after.execute('SELECT * FROM request_attempts WHERE request_key=? ORDER BY started_at',(request['request_key'],)):
                metadata=json.loads(attempt['metadata_json']);binding=metadata.get('binding') or binding
                attempts.append({k:attempt[k] for k in ('attempt_id','state','started_at','deadline_at','response_received_at','response_status','provider_request_id','usage_state')})
            task_id=binding.get('task_id',vote.get('task_id'))
            candidate_id=draft_candidates.get(task_id,task_id if task_id in before_candidates else None)
            reconciliation.append({'request_key':request['request_key'],'status':request['status'],'updated_at':request['updated_at'],
                'candidate_id':candidate_id,'binding':{k:binding.get(k,vote.get(k)) for k in
                    ('task_type','task_id','object_revision','audit_id','round_id','draft_hash','proposal_digest','evidence_hash','voter_id','model_id','model_family','response_contract_version')},
                'attempts':attempts,'retry_allowed':False,'requires_provider_log_reconciliation':True})
    original_cases={r['candidate_id']:r for r in json.loads((OUT/'cases.json').read_text(encoding='utf-8'))}
    plan_items=[]
    for name in ('revision_plan.json','followup_revision_plan.json','supplement_revision_plan.json',
                 'continuation_revision_plan.json','source_mapping_revision_plan.json','final_followup_revision_plan.json'):
        if (OUT/name).exists():plan_items+=json.loads((OUT/name).read_text(encoding='utf-8'))['items']
    def plan_key(item):return (item['candidate_id'],item['base_revision'],json.dumps(item['changes'],ensure_ascii=False,sort_keys=True))
    with repo.connect() as connection:
        drafted={plan_key(json.loads(row[0])) for row in connection.execute('SELECT draft_json FROM revision_drafts')}
    uncreated=[{k:item[k] for k in ('candidate_id','base_revision','changes','reason')} for item in plan_items if plan_key(item) not in drafted]
    receipts=[];all_counts=Counter();manual=[]
    candidates=repo.list_candidates(RUN,limit=1000000)
    release_head_check={}
    ontology_path=Path(repo.get_run(RUN)['ontology_path'])
    catalogs=[parent/'release_catalog.sqlite3' for parent in ontology_path.parents]
    catalogs.append(ROOT/'runs/ontology_releases/release_catalog.sqlite3')
    for catalog in catalogs:
        if not catalog.is_file():continue
        with sqlite3.connect(catalog.resolve().as_uri()+'?mode=ro',uri=True) as catalog_connection:
            head=catalog_connection.execute('SELECT release_id FROM ontology_release_head WHERE singleton=1').fetchone()
        baseline_releases={json.loads(row[0]).get('ontology_release_id') for row in before.execute('SELECT r.candidate_json FROM candidates c JOIN candidate_revisions r ON r.candidate_id=c.candidate_id AND r.revision=c.current_revision WHERE c.run_id=?',(RUN,))}
        release_head_check={'catalog':str(catalog),'baseline_candidate_releases':sorted(x for x in baseline_releases if x),
            'current_head':head[0] if head else None,'head_matches_baseline_candidate_release':bool(head and baseline_releases=={head[0]})}
        break
    for c in candidates:
        identity=c['candidate_id'];audit=repo.get_audit(identity);decision=repo.latest_decision(identity) or {}
        complete=bool(audit['context'].get('independent_review_completed'))
        all_counts['independent_completed' if complete else 'independent_incomplete']+=1
        all_counts['source_'+str(decision.get('source_support_result'))]+=1
        if c['revision']>before_candidates[identity]:receipts.append({'candidate_id':identity,'old_revision':before_candidates[identity],
            'revision':c['revision'],'independent_completed':complete,'source_support_result':decision.get('source_support_result'),
            'workflow_state':c['workflow_state'],'audit_id':audit['audit_id']})
        if identity not in original_cases:continue
        selected=next((p for p in audit['rounds'] if p['round_id']==decision.get('selected_round_id')),None)
        votes=[v for v in audit['votes'] if selected and v['round_id']==selected['round_id']]
        issues=[i for i in audit['context'].get('issue_history',[]) if i.get('status','open')!='resolved']
        with repo.connect() as con:ids=[r[0] for r in con.execute('SELECT draft_id FROM revision_drafts WHERE candidate_id=?',(identity,))]
        drafts=[revisions.draft(d) for d in ids]
        pending=[d for d in drafts if d['status'] in {'proposed','approved','review_incomplete','needs_human'} or
            (d['status']=='stale' and d['base_revision']==c['revision'] and d.get('review_result',{}).get('votes'))]
        unknown=[v['voter_id'] for v in votes if v.get('outcome_unknown')]
        revision_unknown=[{'draft_id':d['draft_id'],'voter_id':v['voter_id']} for d in pending
            for v in d.get('review_result',{}).get('votes',[]) if v.get('outcome_unknown')]
        missing_checks=[{'voter_id':v['voter_id'],'checks':[name for name in ('triple_support','qualifiers','relation_direction') if
            not any(x['check']==name and x['status'] in {'pass','not_applicable'} and x.get('evidence_ids') for x in v.get('checks',[]))]}
            for v in votes if v.get('response_status')=='ok' and v.get('source_support')=='supported']
        missing_checks=[v for v in missing_checks if v['checks']]
        if unknown or revision_unknown:category='request_reconciliation_required'
        elif not complete:category='independent_review_incomplete'
        elif any(i['code'] in {'source_conflict','counterevidence'} for i in issues) or any(v.get('counter_evidence_ids') for v in votes):category='material_disagreement_or_vote_clarification'
        elif pending:category='revision_draft_disagreement_or_contract_failure'
        elif missing_checks:category='grounded_check_clarification'
        elif decision.get('source_support_result')=='supported' and any(v.get('schema_issue')=='candidate_mapping_error' for v in votes):category='ontology_mapping_review'
        elif decision.get('source_support_result')=='supported':category='source_supported_pending_ontology_or_issue_closure'
        else:category='expression_or_source_review_required'
        manual.append({'candidate_id':identity,'baseline_category':original_cases[identity]['category'],'current_category':category,
            'revision':c['revision'],'workflow_state':c['workflow_state'],'human_status':c['human_status'],
            'independent_completed':complete,'source_support_result':decision.get('source_support_result'),'audit_id':audit['audit_id'],
            'triple':c['triple'],'open_issues':issues,'unknown_voter_ids':unknown,'revision_unknown_voters':revision_unknown,'ungrounded_checks':missing_checks,
            'note':NOTES.get(identity),'evidence':selected['evidence_bundle']['items'] if selected else [],
            'votes':[{k:v.get(k) for k in ('voter_id','response_status','source_support','schema_issue','reason_codes','counter_evidence_ids','checks','suggested_revision','error','outcome_unknown')} for v in votes],
            'pending_drafts':[{'draft_id':d['draft_id'],'status':d['status'],'changes':d['changes'],'votes':[
                {k:v.get(k) for k in ('voter_id','response_status','decision','rationale_summary','error','outcome_unknown')} for v in d.get('review_result',{}).get('votes',[])]} for d in pending]})
        if identity=='cand_087e253eb4e4af8bcf40' and c['triple']['surface_relation']=='被假定重合于':
            manual[-1]['note']='当前版本仅在谓词中明确“被假定”，Gemma 仍反对其 asserted 模态。中央契约已补入 assumed，并提交了真实模态变更草案；若新草案尚未应用，保留现有反对票和限制。'
        if identity=='cand_087e253eb4e4af8bcf40' and c['triple']['qualifiers'].get('modality')=='assumed':
            manual[-1]['note']='当前中央契约已支持 assumed；本条经独立草案审核使用假设模态，保留本章建立方程与时刻等全部限制，旧 asserted 表达及反对票继续保留。'
        if identity=='cand_2cd7690d60efe95c709f' and c['triple']['surface_relation']=='建立时引入':
            manual[-1]['note']='已将概括性的“建立依据”修订为原文直接陈述的“建立时引入”，保留简化问题与反映质心运动主要规律的条件；旧极性草案的拒绝票保留，当前结论仅采用新版本独立审核。'
        if identity in {'cand_f9a86d764470a851cd8a','cand_f4876022d0c83d291ac3'} and not c['triple'].get('relation_id'):
            manual[-1]['note']='当前版本已保留“弹道系数”类别并撤回不相容的 R_37918d1714e9945397f3 映射；旧类型猜测草案及原反对票保留。原“表示”语义仍在，须继续完整关系查找或本体审核，尚未取得准入。'
        recommendations={
            'cand_c4ca4af2f9fe7df47915':('supported',['ev_d935917f356ac1bb7edc'],'直接定义原句支持当前已修订的肯定表述；该建议不替代 Gemma 缺少引用的原票。'),
            'cand_f89f9bd12a09240375b7':('supported',['ev_067cc6bbfd87994f631f','ev_b04e8e553504d4332197'],'因果句与小章动角的简化假设适用范围不同，后句不直接否定前句；须独立确认原票字段冲突。'),
            'cand_808d8f2198c5723ef4cc':('insufficient',['ev_b7c983cd23be47cde70b','ev_7f279c3c2665a6ab9dc2'],'现有句子直接描述表格/射程变化的考虑因素，尚不足以把主体确定为弹丸运动方程；不据证据缺失判 contradicted。')}
        if identity in recommendations and selected:
            proposed,ids,rationale=recommendations[identity]
            if set(ids)<={e['evidence_id'] for e in selected['evidence_bundle']['items']}:
                manual[-1]['manual_assessment_proposal']={'status':'prepared_for_human_review_not_committed',
                    'candidate_hash':sha256_json(repo.get_revision(identity)),'revision':c['revision'],
                    'proposal_digest':selected['proposal_digest'],'evidence_hash':selected['evidence_bundle']['evidence_hash'],
                    'proposed_source_support':proposed,'evidence_ids':ids,'rationale':rationale,
                    'human_label_written':False,'admission_authorized':False,'requires_ontology_gate':True}
        core_checks=('triple_support','qualifiers','relation_direction')
        raw_source_alignment=(len(votes)==len({v['voter_id'] for v in votes})==3 and
            all(v.get('response_status')=='ok' and v.get('source_support')=='supported' and
                not v.get('counter_evidence_ids') and
                all(any(check['check']==name and check['status'] in {'pass','not_applicable'}
                    for check in v.get('checks',[])) for name in core_checks) for v in votes))
        if (selected and raw_source_alignment and decision.get('source_support_result')!='supported' and
                not manual[-1].get('manual_assessment_proposal')):
            allowed={e['evidence_id'] for e in selected['evidence_bundle']['items']}
            ids=sorted({eid for v in votes for eid in [*v.get('supporting_evidence_ids',[]),
                *(eid for check in v.get('checks',[]) if check['check'] in core_checks for eid in check['evidence_ids'])]} & allowed)
            if ids:
                manual[-1]['manual_assessment_proposal']={'status':'prepared_for_human_review_not_committed',
                    'candidate_hash':sha256_json(repo.get_revision(identity)),'revision':c['revision'],
                    'proposal_digest':selected['proposal_digest'],'evidence_hash':selected['evidence_bundle']['evidence_hash'],
                    'proposed_source_support':'supported','evidence_ids':ids,
                    'rationale':'三席原票的来源判断均为 supported，三项核心检查文字均通过且无反证 ID；但检查引用或其他义务未闭合，系统结论仍为 unresolved。请人工对照下列原句确认来源；此建议不补写模型引用、不替代原票，仍须通过本体准入。',
                    'human_label_written':False,'admission_authorized':False,'requires_ontology_gate':True}
    summary={'run_id':RUN,'checked_at_utc':datetime.now(timezone.utc).isoformat(),'backup':str(backup),'baseline_backup':str(before_path),
        'baseline_triage_cases':len(original_cases),'triage_coverage':len(manual),'current_triage_categories':dict(Counter(r['current_category'] for r in manual)),
        'new_draft_count':len(new_drafts),'new_draft_statuses':dict(Counter(d['status'] for d in new_drafts)),
        'source_grounded_plan_item_count':len(plan_items),'uncreated_planned_drafts':uncreated,
        'applied_draft_count':len(newly_applied),'recovered_existing_applied_drafts':[d for d in newly_applied if d in old_drafts],
        'candidate_count_with_new_revisions':len(receipts),'new_revision_count':sum(r['revision']-r['old_revision'] for r in receipts),
        'current_revised_independent_completed':sum(r['independent_completed'] for r in receipts),
        'current_revised_source_supported':sum(r['source_support_result']=='supported' for r in receipts),
        'baseline_run_counts':dict(baseline_counts),'run_counts':dict(all_counts),'workflow_states':dict(Counter(c['workflow_state'] for c in candidates)),
        'preserved_history_checked':dict(checked),'original_valid_votes_unchanged':True,'historical_revisions_and_proposals_unchanged':True,
        'historical_unknown_request_status_changes':unknown_changes,'database_counts':db_counts,'active_or_queued_jobs':jobs,
        'request_cost_totals':dict(cost_total),'request_cost_by_candidate':{k:dict(v) for k,v in cost_by_candidate.items()},
        'new_request_attempt_states':dict(request_states),
        'unknown_request_reconciliation_count':len(reconciliation),
        'revised_candidates':receipts,'candidate_human_statuses':dict(Counter(c['human_status'] for c in candidates)),
        'production_release_head_check':release_head_check}
    (OUT/'resolution_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'human_disagreements.json').write_text(json.dumps(manual,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'unknown_request_reconciliation.json').write_text(json.dumps({'backup':str(backup),
        'requests':reconciliation,'automatic_resend_allowed':False,'model_calls':0},ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 候选修订与人工分歧处理清单','',f'基线待处理候选 {len(original_cases)} 条，已逐条重新分流；此清单不代表人工批准。所有人工裁定建议均未写入标签、未授权准入。','',
        '| 候选 | 当前版本 | 独立审核 | 来源结论 | 处理方向 |','|---|---:|---|---|---|']
    for row in manual:lines.append('| '+row['candidate_id']+' | '+str(row['revision'])+' | '+('三席完成' if row['independent_completed'] else '未齐')+' | '+str(row['source_support_result'])+' | '+row['current_category']+' |')
    lines+=['','## 需要澄清的具体事项','']
    for row in manual:
        if (not row.get('note') and not row['pending_drafts'] and
                row['baseline_category'] not in {'source_disagreement','revision_and_source_conflict'} and
                row['current_category'] not in {'material_disagreement_or_vote_clarification','grounded_check_clarification','revision_draft_disagreement_or_contract_failure'}):continue
        lines+=['### '+row['candidate_id'],'',row.get('note') or '按下列原票的反对点或未落地的检查引用澄清；保留原票，不自动覆盖。','',
            '当前：'+row['triple']['head']+' / '+row['triple']['surface_relation']+' / '+row['triple']['tail'],'']
        if row['ungrounded_checks']:lines+=['未落地证据检查：'+json.dumps(row['ungrounded_checks'],ensure_ascii=False),'']
        if row.get('manual_assessment_proposal'):
            proposed=row['manual_assessment_proposal']
            lines+=['待确认的来源建议：'+proposed['proposed_source_support']+'。'+proposed['rationale'],
                '建议引用：'+', '.join(proposed['evidence_ids'])+'；绑定版本 '+str(proposed['revision'])+'、提案 '+proposed['proposal_digest']+'。','']
            for evidence in row['evidence']:
                if evidence['evidence_id'] in proposed['evidence_ids']:
                    lines+=['- '+evidence['evidence_id']+'：'+evidence['quote'].replace('\n',' ')]
            lines.append('')
        for draft in row['pending_drafts']:
            lines+=['草案 '+draft['draft_id']+'：'+draft['status'],'']
            for vote in draft['votes']:
                if vote.get('decision') in {'reject','uncertain'} or vote.get('response_status')!='ok':lines+=['- '+vote['voter_id']+'：'+str(vote.get('decision') or vote['response_status'])+'；'+str(vote.get('rationale_summary') or vote.get('error') or '')]
            lines.append('')
        if row['baseline_category'] in {'source_disagreement','revision_and_source_conflict'}:
            for vote in row['votes']:
                for check in vote.get('checks') or []:
                    if check['status']=='fail' or check['check']=='counterevidence':lines+=['- '+vote['voter_id']+' '+check['check']+'：'+check['note']+'；ID='+','.join(check['evidence_ids'])]
            lines.append('')
        lines+=['证据 ID：'+', '.join(e['evidence_id'] for e in row['evidence']),'']
    (ROOT/'docs/candidate_human_disagreements_20261030.md').write_text('\n'.join(lines),encoding='utf-8')
    stages={name:json.loads((OUT/name).read_text(encoding='utf-8')).get('status') for name in
        ('status.json','recovery_status.json','followup_status.json','followup_recovery_status.json','supplement_status.json','supplement_recovery_status.json',
         'supplement_remainder_status.json','supplement_remainder_recovery_status.json',
         'continuation_status.json','continuation_recovery_status.json','source_mapping_status.json',
         'source_mapping_recovery_status.json','final_followup_status.json',
         'final_followup_recovery_status.json') if (OUT/name).exists()}
    report=['# 候选修订与人工分歧处理记录','',
        f"截至 {summary['checked_at_utc']}，已对基线 {len(original_cases)} 条待修订/人工处理候选逐条重新分流。",
        f"本次新增 {len(new_drafts)} 份草案；本轮应用 {len(newly_applied)} 份修订（其中恢复历史草案 {len(summary['recovered_existing_applied_drafts'])} 份），涉及 {len(receipts)} 条候选、{summary['new_revision_count']} 次版本递增。",
        f"当前修订后的候选中，{summary['current_revised_independent_completed']} 条已收齐三席独立审核，{summary['current_revised_source_supported']} 条达到来源支持共识。其余保留待补审或分歧，不能把“修订已批准”当作“新版本已通过”。",'',
        '| 指标 | 处理前 | 当前 |','|---|---:|---:|',
        f"| 全章独立审核完成 | {baseline_counts['independent_completed']}/320 | {all_counts['independent_completed']}/320 |",
        f"| 全章达到来源支持共识 | {baseline_counts['source_supported']} | {all_counts['source_supported']} |",
        f"| 图谱导入任务 | {db_counts['import_outbox']['before']} | {db_counts['import_outbox']['after']} |",
        f"| 准入授权 | {db_counts['admission_authorizations']['before']} | {db_counts['admission_authorizations']['after']} |",'',
        '独立完成数按同一规则读取各候选的最近审计标记；来源支持数读取当前权威决定。','',
        f"计数以候选为单位，不是去重事实数或人工金标准准确率。有依据的计划项共 {len(plan_items)} 份，尚未形成实际草案 {len(uncreated)} 份。",'',
        '## 实际处理','',
        '- 所有应用的草案均经原 Qwen、Gemma、DeepSeek 三模型独立审核一致批准；新版本重新独立来源审核。',
        '- 修正条件、频率/模态、极性、指代和主宾角色；将个别不及物变化/比较结论提交为属性表达审核。',
        '- 针对三项分解与单项关系的真实反对意见，先审核“分量”草案，再依据反对意见改为原文完整三项之和；后三份草案均获一致批准，旧草案和原票继续保留。',
        '- 检索并保留遗漏的直接定义及相邻分支句。新增证据使用真实文档偏移 ID，经过原文锚点核验。',
        '- 未应用仅为满足本体 domain/range 而猜测实体类型的建议。当前人工状态全部保留 pending。','',
        '- 后续批次逐条补回肯定定义、量词、可表示模态和“我国/上述”范围；发布包读取修复后，映射变化额外提供真实类及关系定义，仍要求三席独立批准。',
        '- 一条磁盘 I/O 失败审核已核验原检查点、提案和三席原票后零调用恢复；没有重发已成功的模型请求。','',
        '## 仍需处理','',
        '详细分流与逐条证据、反对意见见 [人工分歧清单](candidate_human_disagreements_20261030.md)，完整机器可读数据见 `runs/candidate_revision_resolution_20261030/human_disagreements.json`。','',
        '| 当前处理方向 | 条数 |','|---|---:|']
    labels={'request_reconciliation_required':'请求结果未知，待服务端对账','independent_review_incomplete':'独立审核未齐',
        'material_disagreement_or_vote_clarification':'反证分歧或原票字段需澄清','revision_draft_disagreement_or_contract_failure':'修订草案分歧或契约失败',
        'grounded_check_clarification':'有效票缺少检查项证据引用','ontology_mapping_review':'本体映射需审核',
        'source_supported_pending_ontology_or_issue_closure':'来源共识已具备，待本体/问题义务闭合','expression_or_source_review_required':'表达或来源义务仍未解决'}
    report += ['| '+labels[k]+' | '+str(v)+' |' for k,v in summary['current_triage_categories'].items()]
    report += ['', '## 代码修复与验证','',
        '- 确认失败的原请求会收到原错误、准确变更字段、保护证据 ID 及字段状态约束的反馈；有效票和未知请求不会因此重发。',
        '- 无 planner checkpoint 的确定性审核，可由已选定且内容哈希完全核验的原冻结提案恢复失败席位。',
        '- 恢复后同步问题账本与决策；已解决的旧问题不再因投影未同步而继续显示为未解决。',
        '- 新版本继承已应用草案使用的全部核验证据；校验草案/祖先版本绑定，篡改即阻断，并明确不复用旧版批准。',
        '- 已核验的真实表达变化经当前三席完整且有依据的复核后，可关闭旧表达的证据不足；当前反对、缺少引用或仅版本变化均继续阻断。',
        '- 已完成恢复、证据交接、未知请求保护、问题闭合、发布绑定、规范化与映射上下文回归；结果见 `targeted_regression.txt`。真实候选的证据交接核验记录见 `evidence_handoff_verification.json`。',
        '- 优先加载候选绑定的正式发布包并校验 release/version/profile；不能用独立 JSON 快照的目录冒充发布包。',
        '- 规范关系或类型变化需要新的语义指纹及独立草案审核；元数据变化不重用为新批准。撤回映射后不保留旧 canonical 标志。','',
        '- 为原文明示的建模假设增加 assumed 模态，中央允许值同时贯通解析、规划参数、严格修订和准入校验；抽取提示明确保留假设作用范围，旧票及旧版本不迁改。',
        '- 提交后的检查点写入再次失败时，已提交的完整审核不会被降级为 failed；异常仍正常报告。','',
        '## 历史保护','',
        f"已逐字节核对原有效票：三元组 {checked['validator_votes']}、修订 {checked['revision_votes']}、本体 {checked['ontology_votes']}；均未变化。",
        f"另核对原候选版本 {checked['candidate_revisions']}、原来源提案 {checked['source_proposals']}，内容保持不变。历史未知请求状态变化 {len(unknown_changes)} 条。",
        f"待服务端对账的请求键共 {len(reconciliation)} 个（按请求计，非候选数），已导出 `unknown_request_reconciliation.json`，包含模型、绑定、尝试时间和提供商请求 ID；未知请求不会自动重发。",
        '运行保持 shadow、关闭自动准入与自动发布；没有导入 Neo4j。','',
        '## 本轮请求成本记录','',
        f"新增服务请求尝试 {cost_total['provider_attempts']} 次；已知输入 {cost_total['known_input_tokens']} tokens、已知输出 {cost_total['known_output_tokens']} tokens；{cost_total['missing_usage_attempts']} 次缺少完整用量，{cost_total['outcome_unknown']} 次结果未知。未估算收费金额。逐候选成本见 `resolution_summary.json` 的 request_cost_by_candidate。",'',
        '阶段状态：'+json.dumps(stages,ensure_ascii=False),'',
        '早期 pilot_blocked 是首三份草案的过程记录；补审后是否继续及最后结果，应同时查看 supplement_recovery 与 supplement_remainder 两阶段。过期草案的原票仍保留；CAS 行版本变化后不得直接应用。','',
        '处理前快照：`'+str(before_path)+'`',
        '当前快照：`'+str(backup)+'`','']
    (ROOT/'docs/candidate_revision_resolution_20261030.md').write_text('\n'.join(report),encoding='utf-8')
    before.close()
    print(json.dumps({k:v for k,v in summary.items() if k not in {'revised_candidates','request_cost_by_candidate'}},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
