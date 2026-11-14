"""Prepare or run isolated, frozen V8 smoke/full regressions. Never resumes an unknown launch."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from document.hierarchy_index import load_document_index
from evaluation.review_samples import ReadOnlyRepository
from review_agent.batch import run_review_batch
from review_agent.contracts import sha256_json
from review_agent.progress import write_status_snapshot
from review_agent.repository import ReviewRepository
from runs.revalidate_v8 import committee,digest
from runs.rerun_dev30_v8_20261109 import configuration,public_settings,DATA,ROOT
from review_agent.review_observability import outcome

SMOKE=('DEV-006','DEV-008','DEV-016','DEV-021','DEV-028','DEV-029','DEV-030')


def fingerprints():
    files=[p for folder in ('review_agent','ontology_review','evidence','evaluation','schema','extract_triplets','document','confidence','ontologyBuild','to_neo4j','prompts','specs')
        for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.txt','.json'}]
    files += [Path(__file__),ROOT/'runs/revalidate_v8.py',ROOT/'runs/rerun_dev30_v8_20261109.py',
              ROOT/'runs/finalize_v8_regression.py',ROOT/'runs/report_v8_regression.py',
              ROOT/'config/review_agent.json',ROOT/'config/review_output_capabilities.json']
    return {p.relative_to(ROOT).as_posix():digest(p) for p in sorted(files)}


def summarize(repo,bindings,directory):
    rows=[]
    for b in bindings:
        decision=repo.latest_decision(b['candidate_id']) or {}
        from review_agent.repository import NotFoundError
        from review_agent.consensus import Committee
        settings=json.loads((directory/'settings.json').read_text(encoding='utf-8'))
        try:audit=repo.get_audit(b['candidate_id'],decision.get('audit_id'))
        except NotFoundError:audit={'status':'not_started','audit_id':None,'context':{'execution_failure':{'reason_code':'BATCH_STOPPED_BEFORE_START'}},'rounds':[],'votes':[]}
        rounds=audit['rounds'];p=next((p for p in rounds if p['proposal_digest']==decision.get('selected_proposal_digest')),rounds[-1] if rounds else None)
        if p is None:p={**audit['context'],'candidate':repo.get_revision(b['candidate_id']),'evidence_bundle':{'items':[]}}
        votes=[v for v in audit['votes'] if v.get('proposal_digest')==p.get('proposal_digest')]
        c=committee(p) if p.get('committee_policy') else Committee([m['id'] for m in settings['committee_members']],
            settings['consensus']['required_votes'],settings['consensus']['minimum_distinct_families'])
        derived=outcome(p,votes,c,audit_status=audit['status'],context={**audit['context'],'final_gaps':p.get('gaps') or {}},decision=decision)
        rows.append({'sample_id':b['sample_id'],'audit_id':audit['audit_id'],'candidate_id':b['candidate_id'],'outcome':derived,
            'response_provenance_errors':[e for r in derived['vote_validation'].values() for e in r['errors'] if e['category']=='provenance'],
            'runtime_error':audit['context'].get('execution_failure'),
            'systematic_version_or_binding_error':any(e['category']=='version' or
                e['category']=='provenance' and any(word in e['message'] for word in ('frozen binding mismatch','frozen source prompt','policy mismatch','binding changed'))
                for r in derived['vote_validation'].values() for e in r['errors']),
            'trace_defect_recurred':any('field correction differs from stored model fields' in e['message'] for r in derived['vote_validation'].values() for e in r['errors'])})
    with repo.connect() as c:
        from evaluation.regression_gate import request_accounting
        accounting=request_accounting(c)
        outbox=c.execute('SELECT COUNT(*) FROM import_outbox').fetchone()[0]
        requests=c.execute('SELECT state,result_json FROM request_attempts').fetchall()
        applied=c.execute("SELECT COUNT(*) FROM revision_drafts WHERE status='applied' AND draft_id NOT IN (SELECT value FROM json_each(?))",(json.dumps(
            json.loads((directory/'ancestry.json').read_text(encoding='utf-8'))['historical_applied_draft_ids']),)).fetchone()[0]
        planner=c.execute('SELECT record_json FROM planner_steps').fetchall()
    from evaluation.regression_gate import engineering_gate
    gate=engineering_gate(rows,accounting,outbox=outbox,applied=applied)
    report={'mode':'fresh_complete_pipeline','cases':rows,'population':len(rows),'new_committee_request_attempts':len(requests),
        'planner_calls':sum(len(json.loads(r[0]).get('attempts') or []) for r in planner),
        **gate,'known_trace_defect_recurred':any(r['trace_defect_recurred'] for r in rows),
        'production_written':False,'isolated_import_outbox':outbox,'isolated_revisions_applied':applied,
        'quality_evaluable':False,'human_labels':0,'expansion_rule':'nonempty explainable terminal states, all original requests settled, no systematic runtime/version/trace defects; no unanimity requirement'}
    write_status_snapshot(directory/'smoke_report.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--mode',choices=['smoke','full'],default='smoke')
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--run',action='store_true')
    parser.add_argument('--smoke-gate',type=Path);args=parser.parse_args()
    directory=args.output.resolve()
    if not directory.is_relative_to(ROOT/'outputs') or directory.exists():raise ValueError('use a brand new output directory under outputs')
    if args.mode=='full' and args.run:
        if not args.smoke_gate:raise ValueError('full run requires --smoke-gate')
        gate=json.loads((args.smoke_gate/'smoke_report.json').read_text(encoding='utf-8'))
        frozen=json.loads((args.smoke_gate/'code_fingerprints.json').read_text(encoding='utf-8'))
        from evaluation.regression_gate import request_accounting
        with ReadOnlyRepository(args.smoke_gate/'review.sqlite3').connect() as connection:
            settled=request_accounting(connection)['requests_settled']
        if (not gate.get('engineering_gate_passed') or set(r['sample_id'] for r in gate['cases'])!=set(SMOKE)
            or not settled or frozen!=fingerprints()):raise ValueError('smoke engineering gate/code version mismatch or unsettled requests; do not expand')
    data=json.loads(DATA.read_text(encoding='utf-8'));all_bindings=data['bindings'];all_candidates=data['selected_raw_candidates']
    pairs=[(b,c) for b,c in zip(all_bindings,all_candidates) if args.mode=='full' or b['sample_id'] in SMOKE]
    bindings=[b for b,_ in pairs];candidates=[c for _,c in pairs]
    for b,c in pairs:
        if sha256_json(c)!=b['candidate_content_hash']:raise ValueError('frozen candidate hash mismatch')
    source=ReadOnlyRepository(data['source_database']);original=source.get_run(candidates[0]['run_id'])
    index=load_document_index(original['document_path'],candidates[0]['source_index_hash'],require_verified=True)
    if index['source_snapshot']['canonical_text']!=data['source_canonical_text']:raise ValueError('frozen source mismatch')
    directory.mkdir(parents=True)
    config=configuration();config.update(database_path=str(directory/'review.sqlite3'),request_cache_namespace=directory.name)
    # Same limits in smoke and full; bounded minimum fallback retains existing committee rules.
    config['budget'].update(max_total_tokens=360000,total_timeout_seconds=420,max_llm_calls_per_candidate=18,
        max_planner_steps=3,max_validation_rounds=1,max_tool_calls=8,max_parallel_candidates=2)
    config['case_budget'].update(tokens=400000)
    settings=public_settings(config)
    if config['mode']!='shadow' or any(settings[k] for k in ('candidate_revisions_automatically_applied','ontology_publication_enabled','graph_import_enabled')):
        raise ValueError('production actions must remain disabled')
    repo=ReviewRepository(directory/'review.sqlite3');repo.initialize()
    repo.create_run({**deepcopy(original),'mode':'shadow','experiment_id':directory.name})
    for c in candidates:repo.add_candidate(deepcopy(c))
    from review_agent.shadow_revision import copy_revision_ancestry
    copy_revision_ancestry(source,repo,candidates)
    with repo.connect() as connection:historical=[r[0] for r in connection.execute("SELECT draft_id FROM revision_drafts WHERE status='applied'")]
    write_status_snapshot(directory/'ancestry.json',{'historical_applied_draft_ids':historical,'old_review_votes_imported':False})
    frozen=fingerprints()
    for name,value in [('sample_bindings.json',bindings),('settings.json',settings),('code_fingerprints.json',frozen),
        ('experiment.json',{'mode':args.mode,'partition':'development','prepared_at':datetime.now().isoformat(),
            'candidate_hashes':[b['candidate_content_hash'] for b in bindings],'settings_hash':sha256_json(settings),
            'code_hash':sha256_json(frozen),'old_votes_imported':False,'human_labels_imported':False,'production_written':False,
            'budget':{'candidate_deadline_seconds':420,'committee_request_reservations_per_candidate':18,'planner_steps':3,
                'candidate_tokens':400000,'new_semantic_revision_rounds':0,'initial_retries':1,'field_correction_per_seat':1},
            'stopping_rules':['candidate timeout/budget','unknown request outcome blocks resend','account failure pauses batch','no automatic retry of this launch']})]:write_status_snapshot(directory/name,value)
    if not args.run:
        print(json.dumps({'status':'prepared_only','output':str(directory),'model_calls':0}));return
    write_status_snapshot(directory/'started.json',{'mode':args.mode,'no_automatic_relaunch':True})
    result=run_review_batch(repo,candidates[0]['run_id'],config,candidate_ids=[b['candidate_id'] for b in bindings],status_file=directory/'progress.json')
    write_status_snapshot(directory/'batch_result.json',result)
    if fingerprints()!=frozen:raise ValueError('code changed during live run; result not eligible as gate')
    if any(sha256_json(repo.get_revision(b['candidate_id']))!=b['candidate_content_hash'] for b in bindings):raise ValueError('candidate changed during review-only run')
    report=summarize(repo,bindings,directory)
    print(json.dumps({'output':str(directory),'gate':report['engineering_gate_passed'],'requests':report['new_committee_request_attempts']},ensure_ascii=False))


if __name__=='__main__':main()
