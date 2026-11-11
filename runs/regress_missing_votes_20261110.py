"""Bounded new-committee pilot on frozen failure cases; no planner or production writes."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4

from evaluation.review_samples import ReadOnlyRepository
from review_agent.cli import _configured_validators
from review_agent.contracts import proposal_digest,sha256_json
from review_agent.progress import write_status_snapshot
from review_agent.repository import ReviewRepository
from review_agent.review_observability import assess_vote,redact
from review_agent.validator_contracts import contract_for,prompt_for
from review_agent.validators import judge_validator,validator_payload
from review_agent.consensus import aggregate_votes
from review_agent.output_structure import resolve_profile
from runs.focus_v8_followup_20261110 import proposal_for
from runs.regress_v8 import fingerprints
from runs.rerun_dev30_v8_20261109 import configuration,public_settings
from runs.revalidate_v8 import committee,digest,read

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'outputs/dev30_v8_full_20261110_0913'
DERIVED=ROOT/'outputs/dev30_v8_revalidated_20261110_final'
CASES=('DEV-006','DEV-019','DEV-027')


def now():return datetime.now(timezone(timedelta(hours=8))).isoformat()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path);parser.add_argument('--run',action='store_true')
    parser.add_argument('--source',type=Path,default=SOURCE)
    parser.add_argument('--derived',type=Path,default=DERIVED)
    parser.add_argument('--current-correction-policy',action='store_true')
    parser.add_argument('--samples',nargs='+',default=list(CASES))
    parser.add_argument('--voters',nargs='+',choices=('judge_a','judge_b','judge_c'),default=['judge_a','judge_b','judge_c'])
    args=parser.parse_args()
    out=args.output.resolve()
    if out.exists() or not out.is_relative_to(ROOT/'outputs'):raise ValueError('new isolated output directory required')
    out.mkdir(parents=True)
    def save(name,value):write_status_snapshot(out/name,redact(value))
    config=configuration();voters=_configured_validators(config)
    assert len(voters)==3
    voters=[v for v in voters if v['id'] in args.voters]
    for v in voters:
        u=urlsplit(v['client'].base_url)
        if (u.scheme,u.hostname,u.port,u.path.rstrip('/')) not in {
            ('https','43.143.151.41',3000,'/v1'),('https','api.deepseek.com',None,'')}:
            raise ValueError('pilot may use only the user-authorized destinations')
    watched=[args.source/'review.sqlite3',args.derived/'review_packets.json',args.derived/'smoke_report.json',
        ROOT/'runs/v3/review.sqlite3',ROOT/'outputs/dev30_v8_focus_20261110_final/annotation_first5/annotations.json']
    protected={str(p):digest(p) for p in watched}
    old_repo=ReadOnlyRepository(args.source/'review.sqlite3');cases=read(args.derived/'smoke_report.json')['cases']
    contract=contract_for('validator_response_v8');prepared=[]
    for sid in args.samples:
        case=next(c for c in cases if c['sample_id']==sid)
        audit,decision,p,old_votes=proposal_for(old_repo,case)
        old_p=deepcopy(p);p=deepcopy(p)
        p.update(audit_id='missing_vote_pilot_'+uuid4().hex,round_id=1,
            prompt_version=contract.PROMPT_VERSION,prompt_hash=contract.PROMPT_HASH,request_cache_namespace=out.name)
        if args.current_correction_policy:
            from review_agent.response_corrections import policy_for
            policy=policy_for(config)
            p.update(correction_policy=policy,correction_policy_version=policy['version'],correction_policy_hash=sha256_json(policy))
        for generation in p['validator_generation']:
            voter=next((v for v in voters if v['id']==generation['voter_id']),None)
            if voter:generation['output_transport']=resolve_profile(voter,config,contract.RESPONSE_SCHEMA)
        p['proposal_digest']=proposal_digest(p)
        assert p['proposal_digest']!=old_p['proposal_digest']
        # No source, ontology, field rules or old opinions are changed/replayed.
        for key in ('candidate','evidence_bundle','ontology_context','field_spec','field_standardization',
                    'review_scope_policy','committee_policy','committee_members'):
            assert p[key]==old_p[key]
        if not args.current_correction_policy:assert p['correction_policy']==old_p['correction_policy']
        prompt_for(p)
        for v in voters:validator_payload(p,v)
        prepared.append({'sample_id':sid,'old_proposal_digest':old_p['proposal_digest'],'proposal':p,
            'baseline_whole_eligible':[v['voter_id'] for v in old_votes if v['voter_id'] in args.voters and assess_vote(v,old_p)['whole_vote_eligible']],
            'baseline_statuses':{v['voter_id']:v['response_status'] for v in old_votes}})
    save('prepared.json',{'cases':prepared,'settings':public_settings(config),'source_sha256':protected,
        'mode':'new_frozen_committee_only','full_pipeline_rerun':False,'planner_calls':0,'old_votes_reused':False,
        'human_labels_in_prompt':False,'production_written':False,'max_paid_attempts':len(prepared)*len(voters)*2,
        'executed_voter_ids':args.voters,'seat_only_probe':len(voters)<3,
        'sequential_candidates':True,'old_batch_parallel_candidates':2,'case_deadline_seconds':420,
        'latency_comparison_caveat':'candidate concurrency differs; this is not a randomized latency experiment'})
    save('code_fingerprints.json',{**fingerprints(),str(Path(__file__).relative_to(ROOT)):digest(Path(__file__))})
    if not args.run:
        print(json.dumps({'output':str(out),'prepared':len(prepared),'new_model_calls':0},ensure_ascii=False));return
    repo=ReviewRepository(out/'review.sqlite3');repo.initialize();results=[]
    save('progress.json',{'active':True,'started_at':now(),'cases_done':0,'total':len(prepared)})
    for case in prepared:
        sid=case['sample_id'];p=case['proposal'];deadline=time.time()+420;votes=[]
        save('progress.json',{'active':True,'current_sample':sid,'cases_done':len(results),'total':len(prepared),'at':now()})
        print(json.dumps({'starting':sid,'at':now()},ensure_ascii=False),flush=True)
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs={pool.submit(judge_validator,v,p,max_retries=1,per_call_timeout_seconds=v['request_timeout_seconds'],
                repository=repo,deadline_epoch=deadline):v['id'] for v in voters}
            for future in as_completed(jobs):
                v=future.result();votes.append(v)
                print(json.dumps({'sample_id':sid,'voter_id':v['voter_id'],'status':v['response_status'],
                    'actual_calls':v.get('actual_calls'),'finish_reason':v.get('finish_reason'),
                    'output_tokens':v.get('output_tokens'),'transport_metrics':v.get('transport_metrics')},ensure_ascii=False),flush=True)
        consensus=aggregate_votes(votes,committee(p),proposal=p,proposal_digest=p['proposal_digest'])
        row={'sample_id':sid,'proposal_digest':p['proposal_digest'],'baseline_whole_eligible':case['baseline_whole_eligible'],
            'baseline_statuses':case['baseline_statuses'],'votes':votes,'consensus':consensus,
            'vote_assessments':{v['voter_id']:assess_vote(v,p) for v in votes}}
        results.append(row);save('case_results.json',{'cases':results})
        if any(v.get('outcome_unknown') or v.get('pause_job') for v in votes):
            print(json.dumps({'batch_stopped':'unknown provider outcome or account pause; no resend','sample_id':sid}),flush=True)
            break
    with repo.connect() as c:
        attempts=[{'attempt_id':r['attempt_id'],'state':r['state'],'metadata':json.loads(r['metadata_json']),
            'result':json.loads(r['result_json'] or '{}')} for r in c.execute('SELECT * FROM request_attempts')]
    assert len(attempts)<=len(prepared)*len(voters)*2
    assert protected=={str(p):digest(p) for p in watched},'pilot changed protected source data'
    initial=[a for a in attempts if not a['metadata'].get('feedback')]
    patches=[a for a in attempts if (a['metadata'].get('feedback') or {}).get('kind')=='field_patch']
    summary={'completed_at':now(),'samples_executed':len(results),'planned_samples':len(prepared),
        'new_committee_attempts':len(attempts),'initial_attempts':len(initial),'field_patch_attempts':len(patches),
        'initial_ok':sum(a['result'].get('response_status')=='ok' for a in initial),
        'baseline_eligible_seats':sum(len(r['baseline_whole_eligible']) for r in results),
        'new_eligible_seats':sum(len(r['consensus']['eligible_voter_ids']) for r in results),
        'new_full_committees':sum(len(r['consensus']['eligible_voter_ids'])==3 for r in results),
        'unknown_attempts':[a['attempt_id'] for a in attempts if a['state'] in {'unknown','running'}],
        'source_files_unchanged':True,'production_written':False,'human_labels_reused_as_model_evidence':False,
        'full_pipeline_rerun':False,'planner_calls':0,'old_votes_reused':False,'latency_comparison_randomized':False}
    summary.update(executed_voter_ids=args.voters,seat_only_probe=len(voters)<3)
    save('summary.json',summary);save('attempt_ledger.json',{'attempts':attempts})
    save('progress.json',{'active':False,**summary})
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
