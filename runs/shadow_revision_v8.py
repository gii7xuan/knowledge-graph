"""Apply one independently reviewed revision in a new clone, then audit each new version."""
import argparse
import json
from pathlib import Path
from review_agent.batch import run_review_batch
from review_agent.progress import write_status_snapshot
from review_agent.shadow_revision import clone,apply_reviewed,verification_result
from review_agent.review_observability import outcome
from runs.revalidate_v8 import committee
from runs.rerun_dev30_v8_20261109 import configuration,ROOT


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--draft-id');group.add_argument('--group-id');p.add_argument('--run',action='store_true');args=p.parse_args()
    repo=clone(args.source,args.output,allowed_root=ROOT/'outputs')
    if not args.run:
        print(json.dumps({'mode':'isolated_shadow','status':'clone_prepared','model_calls':0,'production_written':False}));return
    from review_agent.revision_service import RevisionService
    revisions=RevisionService(repo)
    parent=revisions.split(args.group_id)['parent_candidate_id'] if args.group_id else revisions.draft(args.draft_id)['candidate_id']
    previous=(repo.latest_decision(parent) or {}).get('review_outcome')
    applied=apply_reviewed(repo,draft_id=args.draft_id,group_id=args.group_id)
    write_status_snapshot(args.output/'revision_application.json',applied)
    job=repo.get_job(applied['reaudit_job_id']);ids=job['payload']['candidate_ids'];config=configuration()
    config.update(database_path=str(repo.database_path),request_cache_namespace=args.output.name)
    result=run_review_batch(repo,job['payload']['run_id'],config,candidate_ids=ids,status_file=args.output/'progress.json')
    rows=[]
    for cid in ids:
        decision=repo.latest_decision(cid) or {};audit=repo.get_audit(cid,decision.get('audit_id'))
        selected=next((r for r in audit['rounds'] if r['proposal_digest']==decision.get('selected_proposal_digest')),None)
        if not selected:continue
        votes=[v for v in audit['votes'] if v['proposal_digest']==selected['proposal_digest']]
        rows.append(outcome(selected,votes,committee(selected),audit_status=audit['status'],
            context={**audit['context'],'final_gaps':selected.get('gaps') or {}},decision=decision))
    report=verification_result(rows if len(rows)==len(ids) else [],previous=previous)
    report.update(parent_case_id=applied['parent_case_id'],production_written=False,all_child_ids=ids,batch_status=result['status'])
    write_status_snapshot(args.output/'shadow_verification.json',report)
    print(json.dumps({'output':str(args.output),'status':report['verification_status'],'production_written':False}))


if __name__=='__main__':main()
