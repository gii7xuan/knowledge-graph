"""Verify preserved history and prepare all current ontology publication dossiers."""
from collections import Counter
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
import hashlib,json,sqlite3,re
from review_agent.repository import ReviewRepository,_ClosingConnection
from review_agent.proposal_resolver import selected_proposal
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.contracts import sha256_json
from ontologyBuild.release_service import load_release
from ontology_review.publisher import current_release_id,OntologyPublisher
from ontology_review import evidence as source_tools
from ontology_review.tools import OntologyTools
from ontology_review.delta import catalog
from ontology_review.validators import consensus
from ontologyBuild.rdf_graph import parse_turtle
from ontologyBuild.rdf_artifact import graph_hash

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/ontology_publication_20261031'
stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
snapshot=OUT/('after_'+stamp+'.sqlite3')
source=sqlite3.connect((ROOT/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=30)
target=sqlite3.connect(snapshot)
try:source.backup(target)
finally:target.close();source.close()

class SnapshotRepository(ReviewRepository):
    def connect(self):
        c=sqlite3.connect(self.database_path.as_uri()+'?immutable=1',uri=True,factory=_ClosingConnection)
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');return c
    def ensure_case(self,identity,limits=None):
        with self.connect() as c:row=c.execute('SELECT case_id FROM review_cases WHERE candidate_id=?',(identity,)).fetchone()
        if not row:raise ValueError('no existing case account')
        return row[0]

repo=SnapshotRepository(snapshot);before=sqlite3.connect((OUT/'before.sqlite3').as_uri()+'?mode=ro',uri=True);before.row_factory=sqlite3.Row
checked=Counter();unknown_changes=[];new_attempts=[]
with repo.connect() as after:
    for table,keys,column in (
        ('validator_votes',('audit_id','round_id','voter_id'),'vote_json'),('revision_votes',('draft_id','voter_id'),'vote_json'),
        ('ontology_votes',('audit_id','round_id','voter_id'),'vote_json'),
        ('candidate_revisions',('candidate_id','revision'),'candidate_json'),
        ('proposals',('audit_id','round_id'),'proposal_json'),('ontology_proposals',('audit_id','round_id'),'proposal_json')):
        where=" WHERE json_extract(vote_json,'$.response_status')='ok'" if column=='vote_json' else ''
        for row in before.execute('SELECT * FROM '+table+where):
            stored=after.execute('SELECT '+column+' FROM '+table+' WHERE '+' AND '.join(k+'=?' for k in keys),[row[k] for k in keys]).fetchone()
            if not stored or stored[0]!=row[column]:raise RuntimeError('protected historical row changed: '+table)
            checked[table]+=1
    for row in before.execute("SELECT request_key,status FROM validation_requests WHERE status IN ('unknown','running')"):
        current=after.execute('SELECT status FROM validation_requests WHERE request_key=?',(row['request_key'],)).fetchone()
        if not current or current[0]!=row['status']:unknown_changes.append({'request_key':row['request_key'],'before':row['status'],'after':current[0] if current else 'missing'})
    if unknown_changes:raise RuntimeError('historical unknown request was altered')
    old_attempts={r[0] for r in before.execute('SELECT attempt_id FROM request_attempts')}
    cost=Counter();states=Counter()
    for row in after.execute('SELECT * FROM request_attempts'):
        if row['attempt_id'] in old_attempts:continue
        result=json.loads(row['result_json'] or '{}');meta=json.loads(row['metadata_json']);states[row['state']]+=1
        item={k:row[k] for k in ('attempt_id','request_key','state','started_at','response_status','provider_request_id')}
        item.update({k:meta.get('binding',{}).get(k) for k in ('task_type','task_id','audit_id','voter_id','model_id','model_family')})
        item.update({k:result.get(k) for k in ('error_code','outcome_unknown','input_tokens','output_tokens')});new_attempts.append(item)
        if result.get('actual_calls')!=0:
            cost['provider_attempts']+=1;cost['known_input_tokens']+=result.get('input_tokens') or 0;cost['known_output_tokens']+=result.get('output_tokens') or 0
            cost['missing_usage_attempts']+=result.get('input_tokens') is None or result.get('output_tokens') is None
    planner_cost=Counter();planner_attempts=[]
    old_steps={(r['audit_id'],r['step_no'],r['role']) for r in before.execute('SELECT * FROM ontology_steps')}
    for row in after.execute("SELECT * FROM ontology_steps WHERE role='planner'"):
        if (row['audit_id'],row['step_no'],row['role']) in old_steps:continue
        for number,attempt in enumerate(json.loads(row['record_json']).get('attempts',[]),1):
            item={k:attempt.get(k) for k in ('response_status','error','outcome_unknown','input_tokens','output_tokens','response_id','returned_model_id')}
            item.update(audit_id=row['audit_id'],step_no=row['step_no'],attempt_no=number)
            planner_attempts.append(item);planner_cost['provider_attempts']+=1
            planner_cost['known_input_tokens']+=attempt.get('input_tokens') or 0
            planner_cost['known_output_tokens']+=attempt.get('output_tokens') or 0
            planner_cost['missing_usage_attempts']+=attempt.get('input_tokens') is None or attempt.get('output_tokens') is None
    semantic=[]
    for row in after.execute('SELECT * FROM ontology_semantic_competency'):
        request=json.loads(row['request_json']);result=json.loads(row['result_json'])
        seats=[]
        for vote in result['votes']:
            seats.append({k:vote.get(k) for k in ('voter_id','response_status','coverage','assessments','unresolved_terms','error_code','outcome_unknown','request_key')})
        semantic.append({'extension_id':request['task_id'],'parent_proposal_digest':row['parent_digest'],
            'object_revision':request['object_revision'],'delta_hash':request['delta_hash'],'request_hash':row['request_hash'],
            'assessment':result['assessment'],'seats':seats})
    tasks=[repo.extension_status(r[0]) for r in after.execute('SELECT extension_id FROM ontology_extension_tasks ORDER BY rowid')]
    jobs=[dict(r) for r in after.execute("SELECT job_id,job_type,status FROM jobs WHERE status IN ('queued','running')")]
    limits={t:{'before':before.execute('SELECT COUNT(*) FROM '+t).fetchone()[0],'after':after.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]} for t in ('import_outbox','admission_authorizations')}

source_tools.load_document_index=lru_cache(maxsize=4)(source_tools.load_document_index)
head=current_release_id(ROOT/'runs/ontology_releases');base=load_release(ROOT/'runs/ontology_releases'/head)
publisher=OntologyPublisher(repo,ROOT/'runs/ontology_releases');dossiers=[];natural=[];queue=[];documents={}
questions=json.loads((base.path/'competency_questions.json').read_text(encoding='utf-8'))
for task in tasks:
    identity=task['extension_id'];row={k:task.get(k) for k in ('extension_id','status','change_kind','description','row_version','delta_revision','base_release_id')}
    row.update(has_delta=task.get('delta') is not None,blockers=[],warnings=[])
    try:
        task_base=load_release(ROOT/'runs/ontology_releases'/task['base_release_id'])
        counter=repo.ontology_counter_history(identity);material=deepcopy(task)
        material['review_evidence']=list({e['evidence_id']:e for e in [*task.get('review_evidence',[]),*counter]}.values())
        evidence,docs,candidates,cases=source_tools.load_sources(repo,material)
        row.update(source_evidence_count=len(evidence),current_candidates=[])
        for candidate_id,candidate in candidates.items():
            d=repo.latest_decision(candidate_id) or {};proposal,votes=selected_proposal(repo,candidate,d)
            complete=len(votes)==3;source_supported=complete and d.get('source_support_result')=='supported'
            row['current_candidates'].append({'candidate_id':candidate_id,'revision':candidate['revision'],
                'independent_completed':complete,'source_support_result':d.get('source_support_result'),
                'head_type':candidate['triple'].get('head_type'),'tail_type':candidate['triple'].get('tail_type')})
            if not source_supported:row['blockers'].append('current_source_support_not_confirmed')
            document=docs[candidate_id];documents[(document['document_id'],document['document_version'])]={
                'document_id':document['document_id'],'document_version':document['document_version'],
                'source_index_hash':candidate.get('source_index_hash'),'canonical_text':document['source_snapshot']['canonical_text']}
        if task.get('delta'):
            proxy=SimpleNamespace(base=task_base,repository=repo,documents=docs,candidates=candidates,
                state={'task':material,'delta':task['delta'],'evidence':evidence,'checks':{},'validation':None})
            tools=OntologyTools(proxy)
            checks={'structure':tools.validate_schema_structure(),'impact':tools.analyze_schema_impact(),'competency':tools.run_competency_checks()}
            row['deterministic_checks']=checks
            for name,check in checks.items():
                if check['status']!='pass':row['blockers'].append(name+'_'+check['status'])
            row['delta']=task['delta']['operations'];row['verified_evidence']=evidence
            natural.append({'extension_id':identity,'base_release_id':head,'delta_revision':task['delta_revision'],
                'delta_hash':task['delta']['delta_hash'],'questions':questions,'source_document_bindings':[
                    {'document_id':doc['document_id'],'document_version':doc['document_version'],'source_index_hash':candidates[cid].get('source_index_hash')}
                    for cid,doc in docs.items()],
                'source_evidence_hash':sha256_json(evidence),
                'semantic_reviews':[s for s in semantic if s['extension_id']==identity and s['object_revision']==task['delta_revision'] and s['delta_hash']==task['delta']['delta_hash']],
                'executable_trigger_results':checks['competency']['checks'],'ontology_votes_do_not_substitute_for_unexecuted_questions':True})
        else:row['blockers'].append('no_current_delta')
    except Exception as exc:row['blockers'].append(type(exc).__name__+': '+str(exc))
    with repo.connect() as connection:ids=[r[0] for r in connection.execute('SELECT audit_id FROM ontology_audits WHERE extension_id=? ORDER BY rowid',(identity,))]
    row['frozen_reviews']=[]
    for aid in ids:
        audit=repo.ontology_audit(aid)
        for p in audit['proposals']:
            votes=[v for v in audit['votes'] if v['round_id']==p['round_id']];aggregate=consensus(votes,p)
            review={'audit_id':aid,'round_id':p['round_id'],'object_revision':p['object_revision'],'delta_hash':p['delta_hash'],
                'proposal_digest':p['proposal_digest'],'current_delta':p['object_revision']==task['delta_revision'] and p['delta_hash']==(task.get('delta') or {}).get('delta_hash'),
                'consensus':aggregate,'votes':[{k:v.get(k) for k in ('voter_id','response_status','decision','reason_codes','requested_context','suggested_changes','error_code','outcome_unknown','request_key')} for v in votes]}
            row['frozen_reviews'].append(review)
            for vote in votes:
                if vote.get('outcome_unknown'):
                    queue.append({'extension_id':identity,'audit_id':aid,'round_id':p['round_id'],'delta_revision':p['object_revision'],
                        'current_delta':review['current_delta'],'voter_id':vote['voter_id'],'request_key':vote.get('request_key'),
                        'status':'requires_provider_reconciliation','automatic_resend_allowed':False})
            if review['current_delta']:
                if any(v.get('outcome_unknown') for v in votes):row['blockers'].append('original_request_outcome_unknown')
                if any(v.get('response_status')=='ok' and v.get('decision') in {'revise','reject'} for v in votes):row['blockers'].append('valid_modeling_objection')
                row['current_independent_review_completed']=len(aggregate['eligible_voter_ids'])==3
    try:publisher.preview(identity);row['review_preview_ready']=True
    except Exception as exc:row['review_preview_ready']=False;row['publication_blocker']=str(exc)
    row['blockers']=sorted(set(row['blockers']));dossiers.append(row)

for item in natural:
    item['executed']=any(s['assessment'].get('executed') for s in item['semantic_reviews'])
    item['status']='not_passed' if item['semantic_reviews'] else 'awaiting_real_semantic_review'
for item in semantic:
    task=next(t for t in tasks if t['extension_id']==item['extension_id'])
    item['current_delta']=item['object_revision']==task['delta_revision'] and item['delta_hash']==(task.get('delta') or {}).get('delta_hash')
    for vote in item['seats']:
        if vote.get('outcome_unknown'):
            queue.append({'extension_id':item['extension_id'],'task_type':'ontology_competency_review',
                'parent_proposal_digest':item['parent_proposal_digest'],'delta_revision':item['object_revision'],
                'current_delta':item['current_delta'],'voter_id':vote['voter_id'],'request_key':vote.get('request_key'),
                'status':'requires_provider_reconciliation','automatic_resend_allowed':False})
for attempt in planner_attempts:
    if attempt.get('outcome_unknown'):
        queue.append({**attempt,'task_type':'ontology_preparation_planner','status':'requires_provider_reconciliation','automatic_resend_allowed':False})

owl_drafts=[]
for task,dossier in zip(tasks,dossiers):
    delta=task.get('delta') or {};patch=delta.get('owl_patch')
    if not patch or task['status']=='published':continue
    if not re.fullmatch(r'ext_[0-9a-f]+',task['extension_id']):raise ValueError('unexpected ontology task ID')
    if patch['patch_hash']!=sha256_json({k:v for k,v in patch.items() if k!='patch_hash'}):raise ValueError('stored OWL patch changed')
    text=patch['merged_rdf'];content=text.encode('utf-8')
    if hashlib.sha256(content).hexdigest()!=patch['source_sha256'] or graph_hash(parse_turtle(text))!=patch['graph_hash']:
        raise ValueError('prepared OWL source differs from its hash')
    if patch['normalized_operations']!=delta['operations']:raise ValueError('prepared OWL operations changed')
    folder=OUT/'unapproved_owl_drafts'/task['extension_id']/('delta_'+str(task['delta_revision'])+'_'+delta['delta_hash'][:12])
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'ontology.ttl').write_bytes(content)
    for name,value in (('patch.json',patch),('project_schema.json',delta['merged_schema'])):
        (folder/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    item={'extension_id':task['extension_id'],'delta_revision':task['delta_revision'],'delta_hash':delta['delta_hash'],
        'base_release_id':task['base_release_id'],'source_sha256':patch['source_sha256'],'graph_hash':patch['graph_hash'],
        'patch_hash':patch['patch_hash'],'before_triples':patch['before_triple_count'],'after_triples':patch['after_triple_count'],
        'ontology_ttl':str(folder/'ontology.ttl'),'patch_json':str(folder/'patch.json'),'project_schema_json':str(folder/'project_schema.json'),
        'published':False,'requires_independent_approval':True,'publication_blocker':dossier.get('publication_blocker'),
        'snapshot_database':str(snapshot)}
    (folder/'UNAPPROVED.json').write_text(json.dumps(item,ensure_ascii=False,indent=2),encoding='utf-8')
    dossier['unapproved_owl_draft']=item;owl_drafts.append(item)

mapping_results=[]
mapping_plans=[json.loads((OUT/name).read_text(encoding='utf-8')) for name in ('existing_mapping_plan.json','crosswind_reuse_plan.json')]
for plan in [p for batch in mapping_plans for p in batch['items']]:
    c=repo.get_revision(plan['candidate_id']);d=repo.latest_decision(plan['candidate_id']);p,v=selected_proposal(repo,c,d)
    guard=OntologyGuard(load_snapshot(base.ontology)).check(c)
    if c['triple']['relation_id']!=plan['changes']['relation_id'] or len(v)!=3 or d.get('source_support_result')!='supported' or guard['status']!='pass':raise RuntimeError('existing property mapping verification failed')
    prior=json.loads(before.execute('SELECT candidate_json FROM candidate_revisions WHERE candidate_id=? AND revision=?',(c['candidate_id'],plan['base_revision'])).fetchone()[0])
    for field in ('head_type','tail_type','surface_relation','qualifiers','qualifier_status'):
        if prior['triple'].get(field)!=c['triple'].get(field):raise RuntimeError('mapping inferred a type or changed source restrictions')
    mapping_results.append({'candidate_id':c['candidate_id'],'revision':c['revision'],'relation_id':c['triple']['relation_id'],
        'source_support_result':d['source_support_result'],'schema_guard':'pass','three_current_independent_seats':True,
        'types_and_source_restrictions_unchanged':True})
live_candidates=repo.list_candidates('run_chapter1_20261028_resume1',limit=1000000)
prep_path=OUT/'remaining_preparation_status.json'
preparation=json.loads(prep_path.read_text(encoding='utf-8')) if prep_path.exists() else {'results':[]}
latest_preparation=list({r['extension_id']:r for r in preparation['results']}.values())
recall=json.loads((OUT/'grounding_recall_summary.json').read_text(encoding='utf-8'))
combined_cost={key:cost[key]+planner_cost[key] for key in set(cost)|set(planner_cost)}
def preparation_category(item):
    if item.get('error_type'):return 'error:'+item['error_type']
    if item.get('has_delta'):
        return 'draft_with_all_local_checks' if all(k in item.get('checks',{}) for k in ('structure','impact','competency')) else 'draft_missing_local_checks_in_original_audit'
    if item.get('stop_reason')=='ontology_planner_failed':return 'planner_contract_or_budget_failure'
    return 'no_current_delta'
test_evidence=[]
for name in ('final_full_regression.txt','final_targeted_regression.txt','preparation_and_runtime_regression.txt','clarification_metadata_regression.txt','preparation_mode_tests.txt'):
    path=OUT/name;content=path.read_text(encoding='utf-8-sig') if path.exists() else ''
    match=re.search(r'Ran (\d+) tests? in ',content)
    test_evidence.append({'file':str(path),'tests':int(match[1]) if match else None,'passed':bool(match and re.search(r'^OK\s*$',content,re.M))})
summary={'checked_at_beijing':datetime.now(timezone(timedelta(hours=8))).isoformat(),'before':str(OUT/'before.sqlite3'),'after':str(snapshot),
    'ontology_task_count':len(tasks),'task_states':dict(Counter(t['status'] for t in tasks)),
    'current_delta_count':sum(t['has_delta'] for t in dossiers),
    'complete_current_ontology_reviews':sum(t.get('current_independent_review_completed',False) for t in dossiers),
    'publishable_review_count':sum(t['review_preview_ready'] for t in dossiers),'published_extensions':sum(t['status']=='published' for t in tasks),
    'unapproved_owl_draft_count':len(owl_drafts),
    'release_head':head,'release_head_unchanged':head==json.loads((OUT/'inventory.json').read_text(encoding='utf-8'))['head'],
    'existing_property_mappings_completed':len(mapping_results),'mapping_results':mapping_results,
    'protected_history_checked':dict(checked),'original_valid_votes_and_frozen_history_unchanged':True,
    'historical_unknown_request_status_changes':unknown_changes,'unknown_ontology_request_count':len(queue),
    'new_attempt_states':dict(states),'durable_review_cost':dict(cost),'preparation_planner_cost':dict(planner_cost),'cost':combined_cost,
    'semantic_competency_reviews':semantic,
    'remaining_preparation':{'status':preparation.get('status'),'investigated':len(latest_preparation),
        'preparation_runs':len(preparation['results']),
        'distinct_tasks':len(latest_preparation),
        'deltas_generated':sum(bool(r.get('has_delta')) for r in latest_preparation),
        'result_categories':dict(Counter(preparation_category(r) for r in latest_preparation)),
        'historical_preparation_errors':sum(bool(r.get('error_type')) for r in preparation['results'])},
    'grounding_recall':{k:recall[k] for k in ('term_count','terms_with_rdf_labels','terms_with_exact_source_name','terms_with_contextual_source_recall')},
    'test_evidence':test_evidence,
    'active_or_queued_jobs':jobs,'database_boundaries':limits,
    'human_statuses':dict(Counter(c['human_status'] for c in live_candidates)),
    'source_support_states':dict(Counter((repo.latest_decision(c['candidate_id']) or {}).get('source_support_result') for c in live_candidates)),
    'user_service_constraint':'暂时无法提供网关调整或服务端查询，先完成其余可推进工作'}
for name,value in (('summary.json',summary),('current_task_dossiers.json',dossiers),('semantic_competency_preparation.json',natural),
                   ('verified_source_documents.json',list(documents.values())),('ontology_request_reconciliation.json',queue),
                   ('semantic_competency_results.json',semantic),('new_request_costs.json',new_attempts),('new_planner_costs.json',planner_attempts)):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'unapproved_owl_draft_manifest.json').write_text(json.dumps(owl_drafts,ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# 本体扩展审核与发布处理（2026-10-31）','',
    '截至北京时间 '+summary['checked_at_beijing']+'。用户已说明暂时无法调整网关或提供服务端查询，按要求先推进其余工作。**本轮尚未发布新本体。**','',
    f"完整盘点 {len(tasks)} 个任务，当前有 {summary['current_delta_count']} 份草案；{summary['complete_current_ontology_reviews']} 份当前草案已收齐有效三席意见，{summary['publishable_review_count']} 份达到审核预览条件。草案准备不代表独立审核通过。",'',
    '## 已完成的候选复用','',
    f"{len(mapping_results)} 条候选改用已有正式属性：修订均获原三模型一致批准，新版本也完成三席来源审核，schema guard 均为 pass。端点类型、限定及表面关系未被推断或改写，候选仍待人工处理。",'',
    '| 候选 | 当前版本 | 复用的正式属性 |','|---|---:|---|']
lines+=['| '+r['candidate_id']+' | '+str(r['revision'])+' | '+r['relation_id']+' |' for r in mapping_results]
lines+=['','## 本体草案与真实分歧','',
    '- 气压机制草案：Gemma、DeepSeek 批准，Qwen 的有效 revise 要求说明既有关系能否复用及独立建模必要性。该真实反对意见保留；触发候选仍缺 head_type，未从关系 domain 反填。',
    '- 横风机制按有效复用意见修订为既有属性 R_1654fcf38913d94cb712 的一条定义注释。delta revision 2 仅增加 1 条 skos:definition，RDF 882→883；未增加类/属性，未删除三元组，未修改 domain/range/层级。旧草案及其原票全部保留。',
    '- 横风定义草案当前 Gemma、DeepSeek 批准，Qwen 有效 revise，但七项检查均 pass，且没有具体修订项。该票仍按 revise 处理；只能在能力问题真实通过后，向原模型进行独立、留痕的中性澄清，不能由服务端转换为 approve。当前尚未调用澄清。',
    '- 别名草案、“飞行于”及降弧比较草案仍有未知请求；没有通过重建提案、换模型、降低三席门槛或自动重发规避。','',
    '## 自然语言能力问题的实际执行','',
    '已单独执行 chapter1_source_coverage，提交经校验的完整章节（55,505 字符）、完整 RDF 基线及哈希核验后的精确补丁。该检查与本体建模票分开记录，不能用结构通过代替。','',
    '| 草案 | 版本 | 席位 | 响应 | 覆盖 | 问题结果 |','|---|---:|---|---|---|---|']
for item in semantic:
    for seat in item['seats']:
        assessments=','.join(a['question_id']+':'+a['status'] for a in seat.get('assessments') or [])
        lines.append('| '+item['extension_id']+' | '+str(item['object_revision'])+('（当前）' if item['current_delta'] else '（历史）')+' | '+str(seat['voter_id'])+' | '+str(seat['response_status'])+' | '+str(seat.get('coverage') or '')+' | '+assessments+' |')
lines+=['',
    '气压机制及横风定义当前均有 Qwen 网关 504 未知、Gemma pass、DeepSeek 有效 fail/partial，未达到原三模型一致通过。已修正已确认的 JSON/引文契约失败；所有有效负面结果和未知请求均保留，不再抽票重试。',
    '本地逐术语核验：270 个正式术语均有 RDF 标签；249 个可在原文中匹配声明名称，263 个有原文或端点上下文召回。21 个名称无精确词面匹配，已列出供语义复核。该索引反驳“RDF普遍缺标签”的结构性判断，但不能自动推翻覆盖不完整的语义 fail，也不能将词面召回视为完整覆盖。','',
    '## 其余任务准备','',
    '准备模式执行真实完整本体检索、证据调查及最小草案构建，保存结构、影响和能力预检；明确禁止独立投票与发布。修正了准备模式错误预留三席调用预算的问题，并保留所有历史用量。',
    '当前准备统计：'+json.dumps(summary['remaining_preparation'],ensure_ascii=False)+'。缺少可靠类端点、预算用尽或模型动作失败的任务按实际原因留待处理，未虚构草案或批准。',
    '两份早期草案在原规划审计中未跑齐预检，本次已在一致性快照上补做结构、影响和能力检查，结果写入 current_task_dossiers.json；原审计保持原样。',
    '详细执行轨迹见 remaining_preparation_status.json；所有 102 项的现行来源、草案、确定性预检和发布阻塞见 current_task_dossiers.json。','',
    f"另已导出 {len(owl_drafts)} 份未批准 OWL 草案包（ontology.ttl、精确 patch、项目视图与 UNAPPROVED 标记），逐份复核源码、图及补丁哈希。目录为 unapproved_owl_drafts；清单见 unapproved_owl_draft_manifest.json。这些文件没有写入正式发布目录或发布目录库。各草案独立基于同一发布头；发布一份后，其余需重基并重审。",'',
    '## 代码与验证','',
    '- 提供无损完整基线 RDF 加精确补丁交付，发送前重建图并核对哈希；匿名节点差异保留双完整图。冻结审核输入与请求语义身份不随传输压缩变化。',
    '- 补齐独立能力问题执行、引文精确校验、已确认失败恢复、原模型中性澄清及发布校验链。原有效票不可覆盖；未知结果不可自动重发；三模型三家族门槛保持。',
    '- 完整回归曾通过 89 项；语义/澄清/恢复定向回归 28 项通过；准备模式及共享运行时 44 项通过；澄清元数据回归 7 项通过；准备模式最终 3 项回归通过。各组有重叠，不相加。原始运行记录及实际状态见 summary.json 的 test_evidence。测试使用隔离数据库与模拟模型，不将测试通过当作生产审核通过。',
    '完整历史核对：'+json.dumps(dict(checked),ensure_ascii=False)+'；原有效票、候选旧版及冻结提案均未改变。历史未知请求状态变化 0 条。','',
    f"本轮可对账的审核请求尝试 {cost['provider_attempts']} 次，新增规划调用 {planner_cost['provider_attempts']} 次；合计已知输入 {combined_cost['known_input_tokens']}、输出 {combined_cost['known_output_tokens']} tokens；{combined_cost['missing_usage_attempts']} 次缺少完整用量。规划日志与持久化审核请求分开统计，缺失用量不补估。",'',
    '发布头保持 '+head+'。自动准入、自动发布继续关闭，未新增人工标签，未导入 Neo4j。','',
    '## 后续条件','',
    '发布仍需：对账未知请求、处理有效建模反对意见及能力覆盖失败、完成其余草案的原三模型独立审核，再逐份进行发布绑定和新版本重审。用户目前不能提供网关或日志；待服务端条件变化后从已保存请求继续，不能把“准备完毕”报告成“发布完毕”。','',
    '服务端对账清单中的 request_key 为本地请求身份，未必是服务商可查询的请求 ID。原始 provider_request_id 若可得，保存在 new_request_costs.json 中。','',
    '[完整任务与阻塞](D:/knowledge-huopao/runs/ontology_publication_20261031/current_task_dossiers.json) · [能力问题实际结果](D:/knowledge-huopao/runs/ontology_publication_20261031/semantic_competency_results.json) · [服务端对账清单](D:/knowledge-huopao/runs/ontology_publication_20261031/ontology_request_reconciliation.json) · [统计与历史核验](D:/knowledge-huopao/runs/ontology_publication_20261031/summary.json)','']
(ROOT/'docs/ontology_publication_20261031.md').write_text('\n'.join(lines),encoding='utf-8')
before.close();print(json.dumps(summary,ensure_ascii=False,indent=2))
