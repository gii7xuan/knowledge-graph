"""Operator-directed continuation of untouched cases while unresolved cases stay held."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from evaluation.frozen_regression import read, save, settings_for
from evaluation.regression_gate import request_accounting
from evaluation.review_samples import ReadOnlyRepository
from review_agent.batch import run_review_batch
from review_agent.contracts import sha256_json
from review_agent.repository import ReviewRepository
from review_agent.shadow_revision import copy_revision_ancestry
from runs.regress_v8 import ROOT, fingerprints, public_settings, summarize
from runs.revalidate_v8 import digest

VERSION='untouched_case_continuation_v1'


def pending_bindings(bindings, started_ids, requested_ids):
    expected=[b for b in bindings if b['candidate_id'] not in set(started_ids)|set(requested_ids)]
    if not expected:raise ValueError('no untouched cases remain; recovery is a separate operation')
    if len({b['candidate_id'] for b in bindings})!=len(bindings):raise ValueError('duplicate candidate bindings')
    return expected


def physical_calls(repository):
    with repository.connect() as c:
        committee=[(r['state'],json.loads(r['result_json'] or '{}')) for r in c.execute('SELECT state,result_json FROM request_attempts')]
        planner=[a for r in c.execute('SELECT record_json FROM planner_steps') for a in json.loads(r[0]).get('attempts') or []]
    return sum(state!='not_sent' and result.get('actual_calls')!=0 for state,result in committee)+sum(
        not a.get('cached') and a.get('actual_calls')!=0 for a in planner)


def collect_manual_holds(repository,bindings):
    samples={b['candidate_id']:b['sample_id'] for b in bindings};holds=[]
    with repository.connect() as c:
        audits={r['audit_id']:r['candidate_id'] for r in c.execute('SELECT audit_id,candidate_id FROM audit_runs')}
        for row in c.execute("SELECT * FROM request_attempts WHERE state NOT IN ('completed','failed','not_sent')"):
            metadata=json.loads(row['metadata_json']);result=json.loads(row['result_json'] or '{}')
            binding=metadata.get('binding') or {};cid=binding.get('task_id')
            holds.append({'kind':'committee_request','candidate_id':cid,'sample_id':samples.get(cid),
                'audit_id':binding.get('audit_id'),'attempt_id':row['attempt_id'],'request_key':row['request_key'],
                'role':binding.get('voter_id'),'original_state':row['state'],
                'original_result_hash':sha256_json(result),'http_status':result.get('http_status'),
                'gateway_request_id':result.get('gateway_request_id'),
                'source_reference':str(repository.database_path)+'#request_attempts/'+row['attempt_id']})
        for row in c.execute("SELECT r.* FROM validation_requests r WHERE r.status IN ('running','unknown') AND NOT EXISTS "
                             '(SELECT 1 FROM request_attempts a WHERE a.request_key=r.request_key)'):
            holds.append({'kind':'unbound_request_reservation','candidate_id':None,'sample_id':None,
                'request_key':row['request_key'],'original_state':row['status'],
                'source_reference':str(repository.database_path)+'#validation_requests/'+row['request_key']})
        for row in c.execute('SELECT audit_id,step_no,record_json FROM planner_steps'):
            for attempt in json.loads(row['record_json']).get('attempts') or []:
                if not attempt.get('outcome_unknown') and attempt.get('response_status') in {
                    'ok','invalid_action','invalid_json','invalid_contract','invalid_reference','api_error','not_sent'}:continue
                cid=audits[row['audit_id']]
                holds.append({'kind':'planner_request','candidate_id':cid,'sample_id':samples.get(cid),'role':'planner',
                    'audit_id':row['audit_id'],'step_no':row['step_no'],'attempt_no':attempt.get('attempt_no'),
                    'input_hash':attempt.get('input_hash'),'original_state':'unknown',
                    'original_result_hash':sha256_json(attempt),'http_status':attempt.get('http_status'),
                    'gateway_request_id':attempt.get('gateway_request_id'),
                    'source_reference':str(repository.database_path)+'#planner_steps/'+row['audit_id']+'/'+str(row['step_no'])})
    for hold in holds:
        hold.update(disposition='incomplete_requires_human_review',request_outcome='unknown',
            not_executed_confirmed=False,retry_authorized=False,approval_vote=False)
        hold['hold_id']='hold_'+sha256_json(hold)[:24]
    return holds


def validate_source(source):
    frozen=read(source/'experiment.json')
    if frozen.get('version')!='frozen_development_regression_v2' or frozen.get('freeze_hash')!=sha256_json({k:v for k,v in frozen.items() if k!='freeze_hash'}):
        raise ValueError('original frozen development run is required')
    if read(source/'status.json').get('status')!='stopped':raise ValueError('original run must be stopped')
    code=read(source/'code_fingerprints.json')
    if sha256_json(code)!=frozen['code_hash']:raise ValueError('original code fingerprint manifest changed')
    with ZipFile(source/'execution_code_snapshot.zip') as archive:
        if any(hashlib.sha256(archive.read(name)).hexdigest()!=expected for name,expected in code.items()):
            raise ValueError('original execution code archive differs from its freeze')
    if any(digest(Path(path))!=expected for path,expected in frozen['assets'].items()):
        raise ValueError('original source or approved ontology changed')
    bindings=read(source/'sample_bindings.json')
    if sha256_json(bindings)!=frozen['sample_bindings_hash']:raise ValueError('original population changed')
    repository=ReadOnlyRepository(source/'review.sqlite3')
    with repository.connect() as c:
        if c.execute("SELECT 1 FROM audit_runs WHERE status='running' LIMIT 1").fetchone() or c.execute("SELECT 1 FROM request_attempts WHERE state='running' LIMIT 1").fetchone():
            raise ValueError('original execution still running')
        started={r[0] for r in c.execute('SELECT DISTINCT candidate_id FROM audit_runs')}
        requested={json.loads(r[0]).get('binding',{}).get('task_id') for r in c.execute('SELECT metadata_json FROM request_attempts')}
    for b in bindings:
        if sha256_json(repository.get_revision(b['candidate_id']))!=b['candidate_content_hash']:
            raise ValueError('original candidate changed')
    return frozen,repository,bindings,pending_bindings(bindings,started,requested)


def prepare(source,directory,*,instruction):
    source=Path(source).resolve();directory=Path(directory).resolve()
    if not instruction.strip():raise ValueError('explicit operator instruction to hold unresolved cases and continue is required')
    if not directory.is_relative_to(ROOT/'outputs') or directory.exists():raise ValueError('use a new output directory')
    original,repository,bindings,pending=validate_source(source)
    holds=collect_manual_holds(repository,bindings)
    if {h['candidate_id'] for h in holds}&{b['candidate_id'] for b in pending}:
        raise ValueError('a held request cannot be included in untouched continuation')
    config=settings_for(directory);settings=public_settings(config)
    if sha256_json(settings)!=original['settings_hash']:raise ValueError('model, prompt or per-case budget changed')
    prior_calls=physical_calls(repository)
    if prior_calls!=read(source/'execution_receipt.json')['total_model_requests']:
        raise ValueError('prior physical request accounting differs from its receipt')
    additional_limit=len(pending)*config['budget']['max_llm_calls_per_candidate']
    original_limit=original['limits']['llm_calls_total_upper_bound']
    if prior_calls+additional_limit>original_limit:raise ValueError('continuation exceeds original total call authorization')
    candidates=[repository.get_revision(b['candidate_id']) for b in pending]
    run=repository.get_run(candidates[0]['run_id'])
    source_database_hash=digest(repository.database_path)
    directory.mkdir(parents=True)
    target=ReviewRepository(directory/'review.sqlite3');target.initialize()
    target.create_run({**deepcopy(run),'experiment_id':directory.name,'mode':'shadow'})
    for candidate in candidates:target.add_candidate(deepcopy(candidate))
    copy_revision_ancestry(repository,target,candidates)
    with target.connect() as c:ancestry=[r[0] for r in c.execute("SELECT draft_id FROM revision_drafts WHERE status='applied'")]
    protected=deepcopy(read(source/'preserved_inputs.json'))
    for path in (source/'review.sqlite3',source/'experiment.json',source/'execution_receipt.json',source/'execution_code_snapshot.zip'):
        protected['sha256'][str(path.relative_to(ROOT))]=digest(path)
    code=fingerprints()
    frozen={'version':VERSION,'prepared_at':datetime.now(timezone.utc).isoformat(),'partition':'development',
        'source_run':str(source),'source_freeze_hash':original['freeze_hash'],'source_database_sha256':source_database_hash,
        'operator_instruction':instruction,'population':len(pending),'excluded_started_candidates':sorted(set(b['candidate_id'] for b in bindings)-set(b['candidate_id'] for b in pending)),
        'manual_hold_count':len(holds),'manual_holds_hash':sha256_json(holds),
        'sample_bindings_hash':sha256_json(pending),'settings_hash':sha256_json(settings),'code_hash':sha256_json(code),
        'assets':original['assets'],'prior_model_calls':prior_calls,'additional_call_upper_bound':additional_limit,
        'combined_call_upper_bound':prior_calls+additional_limit,'original_authorized_call_upper_bound':original_limit,
        'execution_policy':'only untouched cases; unresolved requests stay unknown and go to human review; no automatic resend',
        'unknown_case_policy':'hold affected case, continue different untouched cases; preserve original votes and raw outcomes',
        'relaunches':0,'production_written':False,'human_labels_in_prompt':False}
    frozen['freeze_hash']=sha256_json(frozen)
    for name,value in [('experiment.json',frozen),('code_fingerprints.json',code),('sample_bindings.json',pending),
        ('settings.json',settings),('ancestry.json',{'historical_applied_draft_ids':ancestry,'old_review_votes_imported':False}),
        ('preserved_inputs.json',protected),('carried_manual_holds.json',holds),
        ('status.json',{'status':'prepared','new_model_calls':0,'population':len(pending)})]:save(directory,name,value)
    if digest(repository.database_path)!=source_database_hash:raise ValueError('original database changed during preparation')
    return frozen


def verify(directory):
    frozen=read(directory/'experiment.json')
    if frozen.get('version')!=VERSION or frozen.get('freeze_hash')!=sha256_json({k:v for k,v in frozen.items() if k!='freeze_hash'}):
        raise ValueError('continuation manifest changed')
    if read(directory/'code_fingerprints.json')!=fingerprints() or frozen['code_hash']!=sha256_json(fingerprints()):
        raise ValueError('continuation code changed')
    if sha256_json(public_settings(settings_for(directory)))!=frozen['settings_hash'] or sha256_json(read(directory/'settings.json'))!=frozen['settings_hash']:
        raise ValueError('continuation settings changed')
    if any(digest(Path(path))!=expected for path,expected in frozen['assets'].items()):raise ValueError('frozen source assets changed')
    bindings=read(directory/'sample_bindings.json');holds=read(directory/'carried_manual_holds.json')
    if sha256_json(bindings)!=frozen['sample_bindings_hash'] or sha256_json(holds)!=frozen['manual_holds_hash']:
        raise ValueError('continuation population or held-request scope changed')
    _,source,_,pending=validate_source(Path(frozen['source_run']))
    if bindings!=pending or digest(source.database_path)!=frozen['source_database_sha256']:
        raise ValueError('original requests or untouched-case selection changed')
    if holds!=collect_manual_holds(source,read(Path(frozen['source_run'])/'sample_bindings.json')):
        raise ValueError('held requests changed; review their original receipts before dispatch')
    repository=ReadOnlyRepository(directory/'review.sqlite3')
    for b in bindings:
        if sha256_json(repository.get_revision(b['candidate_id']))!=b['candidate_content_hash']:raise ValueError('continuation candidate changed')
    for path,h in read(directory/'preserved_inputs.json')['sha256'].items():
        if digest(ROOT/path)!=h:raise ValueError('protected historical input changed')
    return frozen,bindings


def execute(directory):
    directory=Path(directory).resolve();frozen,bindings=verify(directory)
    repository=ReviewRepository(directory/'review.sqlite3')
    with repository.connect() as c:
        if c.execute('SELECT 1 FROM audit_runs LIMIT 1').fetchone() or c.execute('SELECT 1 FROM request_attempts LIMIT 1').fetchone():
            raise ValueError('continuation already has attempts; never dispatch an automatic retry')
    source_claim=Path(frozen['source_run'])/'untouched_continuation_started.json'
    with source_claim.open('x',encoding='utf-8') as stream:
        json.dump({'continuation':str(directory),'freeze_hash':frozen['freeze_hash'],
            'candidate_ids':[b['candidate_id'] for b in bindings]},stream)
    with (directory/'started.json').open('x',encoding='utf-8') as stream:
        json.dump({'freeze_hash':frozen['freeze_hash'],'started_at':datetime.now(timezone.utc).isoformat()},stream)
    with ZipFile(directory/'execution_code_snapshot.zip','x',ZIP_DEFLATED) as archive:
        for name in read(directory/'code_fingerprints.json'):archive.write(ROOT/name,name)
    save(directory,'status.json',{'status':'running','population':len(bindings),'held_previous_requests':frozen['manual_hold_count']})
    try:
        config=settings_for(directory);run_id=repository.get_revision(bindings[0]['candidate_id'])['run_id']
        result=run_review_batch(repository,run_id,config,candidate_ids=[b['candidate_id'] for b in bindings],status_file=directory/'progress.json')
        save(directory,'batch_result.json',result)
        verify(directory)
        report=summarize(repository,bindings,directory)
        holds=collect_manual_holds(repository,bindings)
        save(directory,'new_manual_holds.json',holds)
        with repository.connect() as c:
            processed=c.execute('SELECT COUNT(DISTINCT candidate_id) FROM audit_runs').fetchone()[0]
            accounting=request_accounting(c)
        status='processing_completed_with_manual_review' if processed==len(bindings) else 'stopped_before_all_cases_started'
        save(directory,'status.json',{'status':status,'processed':processed,'population':len(bindings),
            'previous_manual_holds':frozen['manual_hold_count'],'new_manual_holds':len(holds),
            'requests_settled':accounting['requests_settled'],'engineering_gate_passed':report['engineering_gate_passed'],
            'automatic_relaunch':False,'production_written':False})
    except BaseException as exc:
        save(directory,'status.json',{'status':'stopped','reason':type(exc).__name__,'automatic_relaunch':False})
        raise
    return read(directory/'status.json')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','verify','run']);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source',type=Path);parser.add_argument('--instruction')
    args=parser.parse_args();directory=args.output.resolve()
    if args.action=='prepare':
        if not args.source or not args.instruction:parser.error('prepare requires --source and --instruction')
        frozen=prepare(args.source,directory,instruction=args.instruction)
        result={k:frozen[k] for k in ('population','manual_hold_count','additional_call_upper_bound','combined_call_upper_bound','freeze_hash')}
    elif args.action=='verify':result={'verified':True,'freeze_hash':verify(directory)[0]['freeze_hash']}
    else:result=execute(directory)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
