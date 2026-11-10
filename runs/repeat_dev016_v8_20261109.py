"""Two additional independent ballots on one identical frozen DEV-016 proposal."""
import json
import time
from copy import deepcopy
from pathlib import Path
from evaluation.review_samples import ReadOnlyRepository
from evaluation.review_quality import repeat_consistency
from review_agent.contracts import sha256_json
from review_agent.repository import ReviewRepository
from review_agent.validators import judge_independently,model_request_count
from review_agent.consensus import Committee,aggregate_votes
from review_agent.cli import _configured_validators
from review_agent.execution import AuditPaused
from review_agent.progress import write_status_snapshot
from runs.rerun_dev30_v8_20261109 import OUT,DB,configuration,public_settings


def main():
    source=ReadOnlyRepository(DB);config=configuration();settings=public_settings(config)
    binding=next(b for b in json.loads((OUT/'sample_bindings.json').read_text(encoding='utf-8')) if b['sample_id']=='DEV-016')
    cid=binding['candidate_id'];decision=source.latest_decision(cid) or {}
    audit=source.get_audit(cid,decision.get('audit_id'))
    proposal=next((p for p in audit['rounds'] if p['proposal_digest']==decision.get('selected_proposal_digest')),None)
    if not proposal:
        write_status_snapshot(OUT/'repeat_report.json',{'status':'not_evaluable','reason':'DEV-016没有可复用的完整冻结提案，未发起重复请求'});return
    fingerprint=sha256_json({'proposal':proposal,'settings':settings})
    trials=[{'trial':0,'kind':'original_ballot','input_fingerprint':fingerprint,
        'votes':[deepcopy(v) for v in audit['votes'] if v['proposal_digest']==proposal['proposal_digest']]}]
    run=source.get_run(binding['run_id']);total_calls=0
    for number in (1,2):
        directory=OUT/('repeat_'+str(number));directory.mkdir(exist_ok=True)
        result_file=directory/'result.json'
        if result_file.exists():
            trial=json.loads(result_file.read_text(encoding='utf-8'))
            assert trial['input_fingerprint']==fingerprint,'repeat input changed'
            trials.append(trial);continue
        marker=directory/'started.json'
        if marker.exists():
            write_status_snapshot(OUT/'repeat_report.json',{'status':'incomplete','reason':'此前重复请求结果未完成，禁止自动重发','trials':trials});return
        write_status_snapshot(marker,{'input_fingerprint':fingerprint,'max_model_requests':6,'automatic_rerun':False})
        repo=ReviewRepository(directory/'review.sqlite3');repo.initialize()
        repo.create_run(deepcopy(run));repo.add_candidate(deepcopy(proposal['candidate']))
        context=deepcopy(audit['context']);context['audit_id']=audit['audit_id']
        repo.start_audit(cid,binding['revision'],context)
        repo.append_round(audit['audit_id'],proposal,[],{}, {})
        voters=_configured_validators(config)
        def received(vote):repo.append_round(audit['audit_id'],proposal,[vote],{}, {})
        try:
            votes=judge_independently(voters,proposal,max_workers=3,max_retries=1,repository=repo,
                per_call_timeout_seconds=180,deadline_epoch=time.time()+360,on_vote=received)
        except AuditPaused:
            write_status_snapshot(OUT/'repeat_report.json',{'status':'paused','reason':'服务余额或账户问题，保留已发请求，不自动重发','trials':trials});return
        total_calls+=sum(model_request_count(v) for v in votes)
        policy=proposal['committee_policy'];committee=Committee([v['id'] for v in voters],policy['required_votes'],
            policy['minimum_distinct_families'],proposal['committee_version'])
        consensus=aggregate_votes(votes,committee,proposal_digest=proposal['proposal_digest'],proposal=proposal)
        trial={'trial':number,'kind':'new_independent_ballot','input_fingerprint':fingerprint,'votes':votes,
            'consensus':consensus,'model_calls':sum(model_request_count(v) for v in votes)}
        write_status_snapshot(result_file,trial);trials.append(trial)
    report=repeat_consistency(trials)
    report.update(sample_id='DEV-016',trials_detail=trials,new_model_calls=sum(t.get('model_calls',0) for t in trials),
        calls_this_invocation=total_calls,
        sampling=settings['sampling'],production_written=False,baseline_votes_overwritten=False,
        human_gold_used=False)
    write_status_snapshot(OUT/'repeat_report.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='trials_detail'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
