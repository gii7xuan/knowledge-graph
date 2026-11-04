"""Continue unaffected short-context revision work without rerolling opposition."""
from pathlib import Path
import json
import time
from document.hierarchy_index import load_document_index
from review_agent.case_budget import CaseBudget
from review_agent.cli import _configured_validators
from review_agent.consensus import Committee
from review_agent.jobs import JobRunner
from review_agent.progress import write_status_snapshot
from review_agent.repository import ReviewRepository
from review_agent.revision_patch import merge_triple_patch
from review_agent.revision_service import RevisionService
from review_agent.service import ReviewService,load_config

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/review_followup_20261030/revisions'
OUT.mkdir(exist_ok=True)


def main():
    config=load_config();repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');service=RevisionService(repo)
    voters=_configured_validators(config);policy=config['consensus']
    committee=Committee([v['id'] for v in voters],required_votes=policy['required_votes'],minimum_distinct_families=policy['minimum_distinct_families'])
    with repo.connect() as c:
        ids=[r[0] for r in c.execute("SELECT draft_id FROM revision_drafts WHERE status IN ('proposed','review_incomplete') AND candidate_id!='cand_fa077f9f4d1e0a98ef2a' ORDER BY rowid")]
    state={'status':'running','selected':len(ids),'processed':0,'applied':0,'results':[],'started_at_epoch':time.time()}
    def save(): write_status_snapshot(OUT/'status.json',{**state,'updated_at_epoch':time.time()})
    save()
    review=ReviewService(repo,config=config)
    runner=JobRunner(repo,review.execute_audit_job,max_workers=1,job_types=['audit'])
    try:
        for identity in ids:
            old=service.draft(identity)
            if any(v.get('response_status')=='ok' and v.get('decision')!='approve' for v in old.get('review_result',{}).get('votes',[])):
                result={'original_draft_id':identity,'status':'existing_opposition_preserved'}
            else:
                current=repo.get_candidate(old['candidate_id']);candidate=repo.get_revision(old['candidate_id'])
                patched=merge_triple_patch(candidate['triple'],old['changes'],strict=True)
                if patched==candidate['triple']:
                    result={'original_draft_id':identity,'status':'already_matches_current_expression'}
                else:
                    manifest=repo.get_run(candidate['run_id'])
                    document=load_document_index(manifest['document_path'],candidate['source_index_hash'],require_verified=True)
                    account=repo.case_status(repo.ensure_case(candidate['candidate_id']))
                    room={'llm_calls':12,'tokens':200000,'tool_calls':12}
                    additions={k:max(0,account['usage'].get(k,0)+v-account['limits'][k]) for k,v in room.items()}
                    additions={k:v for k,v in additions.items() if v}
                    if additions:
                        repo.authorize_case_budget(candidate['candidate_id'],additions,reason='用户继续后续任务：依当前基线完成未有有效反对票的修订与新版本重审',
                            actor=config.get('local_operator','local_operator'),authorization_key='followup-short-revision:'+identity)
                    fresh=service.propose(candidate,current['row_version'],old['changes'],old['evidence_ids'],
                        '当前基线重新核验待审表达修订，原草案、旧票及反证均保留',old['evidence_bundle'],document,
                        protected_ids=old['protected_evidence_ids'],source_audit_id=old.get('source_audit_id'))
                    budget=CaseBudget({'max_llm_calls_per_candidate':6,'max_total_tokens':160000,'total_timeout_seconds':480},repo,
                        [repo.ensure_case(candidate['candidate_id'])],task_type='candidate_revision_review',task_id=fresh['draft_id'])
                    judged=service.review(fresh['draft_id'],voters,committee,budget)
                    if judged['status']=='review_incomplete' and service.recovery_available(fresh['draft_id']):
                        judged=service.review(fresh['draft_id'],voters,committee,budget)
                    write_status_snapshot(OUT/(fresh['draft_id']+'.json'),judged)
                    result={'original_draft_id':identity,'draft_id':fresh['draft_id'],'candidate_id':candidate['candidate_id'],'status':judged['status']}
                    if judged['status']=='approved':
                        result.update(service.apply(fresh['draft_id'],fresh['draft_hash']))
                        state['applied']+=1
                        job=runner.run_available_once()
                        write_status_snapshot(OUT/(fresh['draft_id']+'_reaudit.json'),job)
                        result['reaudit_job_status']=job['status']
                        result['independent_review_completed']=job['progress'].get('independent_review_completed')
            state['results'].append(result);state['processed']+=1;save()
            print(json.dumps(result,ensure_ascii=False),flush=True)
    finally: runner.shutdown()
    with repo.transaction(immediate=True) as c:
        pending=[r[0] for r in c.execute("SELECT job_id FROM jobs WHERE job_type='ontology_extension' AND status='queued'")]
        for job_id in pending:
            c.execute("UPDATE jobs SET status='paused',progress_json=? WHERE job_id=?",(json.dumps({'reason_code':'LONG_CONTEXT_GATEWAY_504',
                'note':'本体长审核等待原模型网关恢复，保留入队与源材料，不自动重发未知请求'},ensure_ascii=False),job_id))
    state.update(status='completed',ontology_jobs_deferred=pending,finished_at_epoch=time.time());save()


if __name__=='__main__': main()
