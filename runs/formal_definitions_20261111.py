"""Prepare and review sourced existing-property definitions in an isolated database.

Preparation is offline. --execute requires an existing, frozen preparation and
never retries an executed launch. Production release activation is not performed.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import shutil
import sqlite3
import time
from types import SimpleNamespace
from uuid import uuid4

from evaluation.review_followup import read,write,jsonl,digest
from evaluation.review_samples import ReadOnlyRepository
from review_agent.contracts import sha256_json
from review_agent.repository import ReviewRepository
from review_agent.shadow_revision import clone
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.agent_state import AgentState
from review_agent.tools import AgentTools
from review_agent.cli import _configured_validators
from review_agent.service import load_config
from review_agent.case_budget import CaseBudget
from document.hierarchy_index import load_document_index
from ontologyBuild.release_service import load_release
from schema.relation_registry import RelationRegistry
from ontology_review.draft_repair import repair_draft
from ontology_review.evidence import load_sources
from ontology_review.runtime import OntologyReviewRuntime
from ontology_review.contracts import versions,proposal_digest
from ontology_review.review_payload import validator_payload
from ontology_review.validators import judge_independently,consensus
from ontology_review.publisher import current_release_id,OntologyPublisher
from ontology_review import semantic_competency

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'outputs/dev30_v8_full_20261111'
PRODUCTION=ROOT/'runs/v3/review.sqlite3'
SPEC=ROOT/'specs/formal_definition_candidates_20261111.json'


def now():return datetime.now(timezone(timedelta(hours=8))).isoformat()


def source_proposal(repo,packet):
    decision=repo.latest_decision(packet['candidate']['candidate_id'])
    audit=repo.get_audit(packet['candidate']['candidate_id'],decision['audit_id'])
    return next(p for p in audit['rounds'] if p['proposal_digest']==packet['binding']['proposal_digest'])


def code_hashes():
    paths=[p for folder in ('ontology_review','ontologyBuild','review_agent','evidence','document','schema','prompts')
           for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.txt','.json'}]
    paths += [Path(__file__),SPEC,ROOT/'config/review_agent.json']
    return {str(p.relative_to(ROOT)):digest(p) for p in paths}


def roster(voters):
    return [{k:v[k] for k in ('id','model_id','model_family')}|{
        'model_identity':str(v.get('model_identity') or v['model_id']).casefold()} for v in voters]


def prepare(out):
    if out.exists() or not out.is_relative_to(ROOT/'outputs'):raise ValueError('Use a new isolated output directory')
    packets={p['sample_id']:p for p in read(SOURCE/'review_packets.json')['samples']}
    spec=read(SPEC);baseline=load_release(packets['DEV-004']['candidate']['ontology_release_path'])
    production_head=current_release_id(ROOT/'runs/ontology_releases')
    if production_head!=baseline.manifest['release_id']:raise ValueError('Production head changed; explicit rebase needed')
    protected={str(p):digest(p) for p in [SOURCE/'review.sqlite3',PRODUCTION,SOURCE/'review_packets.json',
        ROOT/'outputs/dev5_v8_human_20261110_150238/annotations.json',baseline.path/'ontology.ttl']}
    repo=clone(SOURCE/'review.sqlite3',out,allowed_root=ROOT/'outputs')
    repo.initialize()
    releases=out/'releases';releases.mkdir()
    shutil.copytree(baseline.path,releases/baseline.manifest['release_id'])
    with sqlite3.connect((ROOT/'runs/ontology_releases/release_catalog.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(releases/'release_catalog.sqlite3') as dst:src.backup(dst)
    props=RelationRegistry(baseline.ontology).properties
    required={p['candidate']['triple']['relation_id']:p['sample_id'] for p in packets.values() if p['candidate']['triple'].get('relation_id')}
    inventory=[{'relation_id':rid,'label':p.get('label'),'domain':p.get('domain'),'range':p.get('range'),
        'has_definition':bool(p.get('definitions') or p.get('description')),
        'assigned_in_current_30':required.get(rid)} for rid,p in props.items()]
    write(out/'relation_inventory.json',{'properties':inventory,'population':len(props),'assigned_relation_ids':len(required)})
    evidence={};operations=[]
    for item in spec['definitions']:
        p=packets[item['sample_id']];available={e['evidence_id']:e for e in p['evidence_bundle']['items']}
        assert p['candidate']['triple']['relation_id']==item['relation_id']
        assert not props[item['relation_id']].get('definitions') and not props[item['relation_id']].get('description')
        assert set(item['evidence_ids'])<=available.keys()
        evidence.update({eid:deepcopy(available[eid]) for eid in item['evidence_ids']})
        operations.append({'kind':'revise_definition','target':item['relation_id'],
            'changes':{'definition':item['definition'],'definition_language':'zh'},'evidence_ids':item['evidence_ids']})
    primary=packets['DEV-004'];candidate=primary['candidate'];p=source_proposal(repo,primary)
    guard=OntologyGuard(load_snapshot(baseline.ontology))
    state=AgentState(candidate,{},p['audit_id'])
    helper=SimpleNamespace(state=state,orchestrator=SimpleNamespace(ontology_guard=guard,config={}),issue=lambda *a,**k:None)
    lookup=AgentTools(helper).inspect_ontology(relation_id=candidate['triple']['relation_id'],
        surface_relation=candidate['triple']['surface_relation'],issue='正式属性已有ID但缺定义正文；执行完整冻结本体检索，优先原ID补定义')['existing_schema_search']
    current=repo.get_candidate(candidate['candidate_id'])
    created=repo.create_extension_request(candidate,expected_row_version=current['row_version'],proposal=p,
        change_kind='revise_definition',description='8项有逐条原文锚点的现有关系定义增补；不改ID、标签、端点约束、层级，不包含旧未知票据的横风定义任务。',
        proposed_changes={'operations':operations},evidence_ids=spec['definitions'][1]['evidence_ids'],full_lookup=lookup)
    identity=created['task']['extension_id'];task=repo.extension_status(identity)
    with repo.connect() as connection:
        queued=[r['job_id'] for r in connection.execute("SELECT job_id,payload_json FROM jobs WHERE status IN ('queued','running')")
                if json.loads(r['payload_json']).get('extension_id')==identity]
    for job_id in queued:repo.request_cancel(job_id)
    repo.extension_update(identity,task['row_version'],changes={'review_evidence':list(evidence.values())})
    task=repo.extension_status(identity)
    receipt=repair_draft(repo,releases,identity,expected_row_version=task['row_version'],repair_key='source-bound-formal-definitions-20261111',
        action='replace',reason='用户要求下一步处理正式定义；本次由AI起草8项来源绑定定义，仅准备供原三席独立审核的精确差异，不宣称已获语义批准。',
        actor='codex_ai_definition_preparation',evidence_ids=list(evidence),operations=operations,
        resolution={'status':'unapproved_source_bound_definitions','reuse_schema_ids':[o['target'] for o in operations],
            'domain_range_unchanged':True,'published':False,'independent_approval':False})
    write(out/'draft_preparation_receipt.json',receipt)
    task=repo.extension_status(identity)
    verified,documents,candidates,cases=load_sources(repo,task)
    config=load_config();voters=_configured_validators(config)
    assert len(voters)==3 and config['consensus']['required_votes']==config['consensus']['minimum_distinct_families']==3
    runtime=OntologyReviewRuntime(repo,releases,voters,None,config)
    runtime.base=load_release(releases/task['base_release_id']);runtime.documents=documents;runtime.candidates=candidates
    policy={k:config['consensus'][k] for k in ('committee_size','required_votes','minimum_distinct_families')}
    runtime.context={'versions':versions(),'committee_members':roster(voters),'committee_policy':policy,
        'planner_model':None,'planner_family':None,'options_hash':sha256_json(runtime.options),'mode':'shadow',
        'configured_strategy':'operator_prepared','executed_strategy':'operator_prepared_delta_real_tools',
        'no_planner_output_or_semantic_vote_fabricated':True}
    audit_id=repo.start_ontology_audit(identity,runtime.context,'ontology_audit_'+uuid4().hex)
    runtime.budget=CaseBudget({**runtime.options['budget'],'max_llm_calls_per_candidate':3,
        'max_total_tokens':450000,'max_retries_per_request':0,'total_timeout_seconds':600},repo,cases,task_type='ontology_extension',task_id=audit_id)
    runtime.state={'audit_id':audit_id,'task':task,'delta':task['delta'],'evidence':verified,'checks':{},
        'history':[],'validations':{},'validation':None,'fingerprints':{},'critical_issues':[],
        'full_schema_searched':False,'stop_reason':'prepared_without_network','step_no':0,'prior_counter_evidence_ids':[]}
    for number,(name,execute) in enumerate([
        ('search_existing_schema',lambda:runtime.tools.search_existing_schema(' '.join(o['target'] for o in operations))),
        ('validate_schema_structure',runtime.tools.validate_schema_structure),
        ('analyze_schema_impact',runtime.tools.analyze_schema_impact),
        ('run_competency_checks',runtime.tools.run_competency_checks)],1):
        result=execute();repo.ontology_step(audit_id,number,'tool',{'tool':name,**result})
    assert runtime.state['checks']['structure']['status']=='pass'
    runtime.budget.usage['validation_rounds']=1
    proposal=runtime.freeze_proposal(runtime.state)
    proposal['semantic_context_delivery']='verified_frozen_spans_v1'
    proposal['proposal_digest']=proposal_digest(proposal)
    repo.ontology_proposal(proposal);runtime.checkpoint('ready')
    delta=task['delta'];patch=delta['owl_patch']
    assert not patch['removed'] and len(patch['added'])==len(operations)==8
    assert all('skos/core#definition' in s for s in patch['added'])
    # Projected constraints and every unmodified RDF axiom must remain intact.
    after_props=RelationRegistry(delta['merged_schema']).properties
    for rid,old in props.items():
        new=after_props[rid]
        for key in ('id','label','domain','range','domain_semantics','range_semantics','aliases','iri'):
            assert old.get(key)==new.get(key),(rid,key)
    assert set(props)==set(after_props)
    local=[]
    check_guard=OntologyGuard(load_snapshot(delta['merged_schema']));check_guard.require_relation_definition=True;check_guard.require_explicit_mapping=True
    for item in spec['definitions']:
        c=packets[item['sample_id']]['candidate'];result=check_guard.check(c)
        assert result['status']=='pass',(item['sample_id'],result)
        local.append({'sample_id':item['sample_id'],'relation_id':item['relation_id'],'guard':result,
            'semantic_mapping_approved':False,'candidate_version_changed':False})
    write(out/'isolated_mapping_checks.json',local)
    write(out/'prepared_proposal.json',proposal)
    write(out/'prepared_validator_payload.json',validator_payload(proposal,delivery_version='ontology_validator_payload_v3'))
    competency_request,_=semantic_competency.request_for(repo,releases,proposal)
    write(out/'prepared_competency_request.json',competency_request)
    write(out/'owl_patch.json',patch)
    (out/'UNAPPROVED_ontology.ttl').write_text(patch['merged_rdf'],encoding='utf-8')
    production=ReadOnlyRepository(PRODUCTION);fences=[];existing=[]
    with production.connect() as con:
        for row in con.execute('SELECT extension_id,status,task_json FROM ontology_extension_tasks'):
            t=json.loads(row['task_json']);targets={o['target'] for o in (t.get('delta') or {}).get('operations',[])}
            if targets & set(required):existing.append({'extension_id':row['extension_id'],'status':row['status'],
                'delta_revision':t['delta_revision'],'targets':sorted(targets & set(required)),
                'selected_proposal_digest':t.get('selected_proposal_digest'),'operations':(t.get('delta') or {}).get('operations',[])})
        for row in con.execute('SELECT attempt_id,request_key,state,metadata_json,result_json FROM request_attempts WHERE state IN (\'running\',\'unknown\')'):
            m=json.loads(row['metadata_json']);r=json.loads(row['result_json'] or '{}');q=m.get('proposal') or m.get('request') or {}
            if q.get('task_id')=='ext_4272d42b83144e8c8da86d3530f76019':
                fences.append({'attempt_id':row['attempt_id'],'request_key':row['request_key'],'state':row['state'],
                    'task_id':q['task_id'],'object_revision':q.get('object_revision'),'response_status':r.get('response_status'),
                    'outcome_unknown':r.get('outcome_unknown'),'automatic_resend_allowed':False})
    write(out/'existing_task_reuse_and_fences.json',{'relevant_existing_tasks':existing,'crosswind_unknown_requests':fences,
        'held_relations':spec['held_relations'],'production_tasks_changed':False})
    write(out/'protected_inputs.json',protected);assert all(digest(Path(k))==v for k,v in protected.items())
    settings=[{'id':v['id'],'model_id':v['model_id'],'model_family':v['model_family'],
        'base_url':v['client'].base_url,'request_timeout_seconds':v.get('request_timeout_seconds'),
        'http_timeout_seconds':v.get('http_timeout_seconds')} for v in voters]
    write(out/'prepared_manifest.json',{'at':now(),'extension_id':identity,'audit_id':audit_id,
        'proposal_digest':proposal['proposal_digest'],'proposal_sha256':digest(out/'prepared_proposal.json'),
        'competency_request_sha256':digest(out/'prepared_competency_request.json'),
        'roster':roster(voters),'destinations':settings,'production_release_head':production_head,
        'new_model_calls':0,'max_model_calls':6,'modeling_calls':3,'semantic_competency_calls':3,
        'no_retries':True,'production_written':False,'independent_approval':False,
        'local_constraint_checks_passed':8,'definition_operations':8,'rdf_added':8,'rdf_removed':0,
        'source_document_chars':sum(len(d['text']) for d in competency_request['source_documents']),
        'code_fingerprints':code_hashes()})
    (out/'report.md').write_text('# 正式关系定义处理\n\n已盘点81项关系，定义正文原覆盖0/81；30条实际引用12个ID。\n\n'
        '已在新数据库副本对8个既有ID准备来源绑定定义，精确新增8条skos:definition，删除0条RDF，不改ID、标签、domain/range或层级。8个对应案例在副本的元数据/约束检查均pass；这不等于正式语义映射通过。\n\n'
        'DEV-008沿用旧定义草案及未知请求围栏；DEV-019需补原句并澄清诸元指称；DEV-022需纠正投影对象建模；DEV-028的range冲突不能靠定义注释解决。未将这4项混入本批通过数。\n\n'
        'prepared_proposal.json、prepared_validator_payload.json和prepared_competency_request.json为可审阅的精确输入。拟由原Qwen/Gemma/DeepSeek三席分别审核定义差异，再评估原有chapter1_source_coverage问题，最多6次调用、不重试。能力问题仍覆盖完整冻结章节和完整RDF，不削减原标准。\n\n'
        '当前未经独立审核，UNAPPROVED_ontology.ttl不是正式发布版本；没有切换生产发布头、改候选或导入图谱。\n',encoding='utf-8')
    return {'output':str(out),'definitions':8,'local_guard_pass':8,'held_relations':4,'model_calls':0,'source_unchanged':True}


def execute(out):
    manifest=read(out/'prepared_manifest.json')
    if (out/'execution_started.json').exists():raise ValueError('This launch already exists; do not retry or reroll')
    if manifest['code_fingerprints']!=code_hashes():raise ValueError('Prepared code/config changed; review a fresh preparation')
    proposal=read(out/'prepared_proposal.json')
    assert proposal_digest(proposal)==proposal['proposal_digest']==manifest['proposal_digest']
    assert digest(out/'prepared_proposal.json')==manifest['proposal_sha256']
    assert digest(out/'prepared_competency_request.json')==manifest['competency_request_sha256']
    config=load_config();voters=_configured_validators(config)
    assert roster(voters)==manifest['roster']
    repo=ReviewRepository(out/'review.sqlite3');releases=out/'releases'
    write(out/'execution_started.json',{'at':now(),'max_calls':6,'no_automatic_relaunch':True})
    write(out/'progress.json',{'active':True,'phase':'modeling','at':now()})
    initial_count=0
    with repo.connect() as con:initial_count=con.execute('SELECT COUNT(*) FROM request_attempts').fetchone()[0]
    # Sequential dispatch avoids concurrent full-context pressure at shared gateways.
    votes=[]
    for voter in voters:
        current={**voter,'ontology_delivery_version':'ontology_validator_payload_v3'}
        batch=judge_independently([current],proposal,repo,retries=0,
            timeout=min(300,float(voter.get('request_timeout_seconds',180))))
        votes.extend(batch);write(out/'modeling_votes.json',votes)
        print(json.dumps({'phase':'modeling','voter_id':voter['id'],
            'status':batch[0]['response_status'],'decision':batch[0].get('decision')},ensure_ascii=False),flush=True)
        if batch[0].get('outcome_unknown') or batch[0].get('pause_job'):break
    for vote in votes:repo.ontology_vote(vote)
    raw_consensus=consensus(votes,proposal)
    write(out/'modeling_consensus.json',raw_consensus)
    semantic=None
    all_approved=len(votes)==3 and all(v.get('response_status')=='ok' and v.get('decision')=='approve' for v in votes)
    if all_approved:
        write(out/'progress.json',{'active':True,'phase':'semantic_competency','at':now()})
        semantic=semantic_competency.review(repo,releases,proposal,voters,
            reason='用户授权的正式定义处理：原三席分别核对完整冻结章节与完整本体的原能力问题；不复投有效fail/unknown，不发布生产。')
        write(out/'semantic_competency_result.json',semantic)
    verified=semantic_competency.verified_result(repo,releases,proposal) if semantic else None
    final=consensus(votes,proposal,semantic_competency=verified)
    result={'extension_id':manifest['extension_id'],'recommendation':final['result'],
        'task_status':'approved' if final['result']=='approve' else 'needs_human',
        'selected_round_id':proposal['round_id'],'selected_proposal_digest':proposal['proposal_digest'],
        'consensus':final,'stop_reason':'bounded_definition_review_completed_or_held'}
    task=repo.extension_status(manifest['extension_id'])
    repo.finish_ontology_audit(manifest['audit_id'],result,task['row_version'])
    publication_gate={'passed':False}
    publication=None
    try:
        publisher=OntologyPublisher(repo,releases,config)
        publisher.reviewed(manifest['extension_id'])
        publication_gate={'passed':True,'production_publication_performed':False,
            'next_step':'Publish only inside this explicitly isolated release catalog.'}
        publication=publisher.publish(manifest['extension_id'],{
            'approved_by':'user_authorized_codex_definition_processing',
            'reason':'用户允许的8项来源绑定正式定义处理；三席建模和原能力问题均通过后，仅在隔离发布库形成不可变版本，不切换生产。',
            'proposal_digest':proposal['proposal_digest'],'expected_base_release_id':proposal['base_release_id'],
            'confirm_high_impact':True},automatic=False)
        write(out/'isolated_publication.json',publication)
    except (ValueError,RuntimeError) as exc:publication_gate={'passed':False,'reason':str(exc)}
    with repo.connect() as con:
        attempts=con.execute('SELECT attempt_id,state,metadata_json,result_json FROM request_attempts').fetchall()
    new=[r for r in attempts if (json.loads(r['metadata_json']).get('proposal') or {}).get('task_id')==manifest['extension_id']]
    calls=len(attempts)-initial_count
    assert calls<=6
    protected=read(out/'protected_inputs.json');assert all(digest(Path(k))==v for k,v in protected.items())
    summary={'at':now(),'modeling_receipts':len(votes),'all_modeling_approved':all_approved,
        'modeling_consensus':raw_consensus,'semantic_competency':verified,'final_consensus':final,
        'publication_gate':publication_gate,'isolated_publication':publication,
        'new_model_calls':calls,'max_calls':6,'production_written':False,
        'source_and_production_unchanged':True,'new_unknown_requests':[r['attempt_id'] for r in new if r['state'] in {'unknown','running'}]}
    write(out/'summary.json',summary);write(out/'progress.json',{'active':False,**summary})
    with (out/'report.md').open('a',encoding='utf-8') as f:f.write('\n## 真实审核结果\n\n```json\n'+json.dumps(summary,ensure_ascii=False,indent=2)+'\n```\n')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    print(json.dumps(execute(args.output.resolve()) if args.execute else prepare(args.output.resolve()),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
