"""Bounded live engineering smoke: channel preflight, two audits, one revision.

No original candidate/label writes, publication, import or automatic relaunch.
Semantic rejection/defer is an ordinary result, never a reason to reroll a case.
"""
from copy import deepcopy
import json
from pathlib import Path
import time
from uuid import uuid4
from zipfile import ZipFile,ZIP_DEFLATED

from .freeze_shared_ontology import ROOT,read,write
from .shared_coverage_review import file_hash

OUT=ROOT/'outputs/technical_smoke_20261118_v3'
FIRST=ROOT/'outputs/technical_smoke_20261118'
SECOND=ROOT/'outputs/technical_smoke_20261118_v2'
MAX_CALLS=18  # Two completed diagnostics already used the common twenty-call budget.
TOKEN_STOP=300000
SAMPLES=('PILOT-009','PILOT-034','PILOT-024')


def assess_nonce_replay(receipt,expected):
    from review_agent.validators import _parse_content
    from review_agent.schema_validation import schema_errors
    from review_agent.contracts import sha256_json
    if receipt.get('outcome_unknown') or receipt.get('finish_reason')!='stop':
        raise ValueError('only a completed nonce reply may be locally replayed')
    parsed=_parse_content(receipt['raw_response'])
    schema={'type':'object','additionalProperties':False,'required':['probe_token'],
        'properties':{'probe_token':{'type':'string','enum':[expected]}}}
    errors=schema_errors(parsed,schema)
    if errors or parsed!={'probe_token':expected}:raise ValueError('nonce replay does not match the frozen input token')
    return {'response_status':'ok','derived_local_assessment':True,'counts_as_review_vote':False,
        'new_provider_calls':0,'source_attempt_id':receipt['attempt_id'],'original_response_status':receipt['response_status'],
        'original_response_hash':sha256_json(receipt),'corrected_local_schema':schema,
        'reason':'Local harness used unsupported const; enum equality verifies the already-completed exact same nonce reply.'}


def receipts(repo):
    with repo.connect() as con:
        rows=[dict(r) for r in con.execute('SELECT attempt_id,state,result_json FROM request_attempts')]
    return [{**r,'result':json.loads(r['result_json']or'{}')} for r in rows]


def blocked(repo):
    rows=receipts(repo)
    return bool(len(rows)>=MAX_CALLS or any(r['state'] in {'running','unknown'} for r in rows)
        or sum(sum(r['result'].get(k)or 0 for k in ('input_tokens','output_tokens')) for r in rows)>=TOKEN_STOP)


def prepare():
    from ontologyBuild.release_service import load_release
    from .review_samples import ReadOnlyRepository
    from .shared_extension_version import reanchor
    from .shared_expression_repairs import changes_for
    from document.hierarchy_index import load_document_index
    from review_agent.repository import ReviewRepository
    from review_agent.revision_service import RevisionService
    from evidence.hierarchical_retriever import retrieve_initial,_evidence
    from evidence.verification import make_evidence_bundle,verify_evidence_bundle
    from runs.regress_v8 import configuration,public_settings,fingerprints
    if OUT.exists():raise ValueError('study already exists; inspect receipts, never relaunch')
    config=configuration(profile='weighted_diagnostic',directory=OUT)
    config['budget'].update(max_parallel_candidates=1,max_parallel_validators=1,max_llm_calls_per_candidate=7,
        max_retries_per_request=0,total_timeout_seconds=540,per_call_timeout_seconds=180,
        planner_timeout_seconds=90,max_total_tokens=100000,max_validation_rounds=1)
    config['case_budget'].update(llm_calls=7,tokens=100000,tool_calls=8)
    config['planner'].update(state_driven_steps=True,durable_requests=True,max_output_tokens=2048)
    config['planner'].pop('failure_policy',None)
    for slot in config['validators']:
        if slot.get('family')=='gemma':slot['thinking_mode']='disabled'
    base=load_release(read(ROOT/'outputs/ontology_freeze_20261116/live/frozen_release.json')['path'])
    samples={r['sample_id']:r for r in read(ROOT/'outputs/development_pool_20261117/pilot/review_samples.json')['samples']}
    docs={r['document_id']:r for r in read(ROOT/'outputs/development_pool_20261117/source_manifest.json')['documents']}
    supplements=read(ROOT/'outputs/preannotation_followup_20261118/source_supplements.json')
    watched=read(ROOT/'outputs/shared_extension_20261118/baseline.json')['watched_hashes']
    assert all(file_hash(p)==h for p,h in watched.items())
    OUT.mkdir();(OUT/'sources').mkdir()
    repo=ReviewRepository(OUT/'review.sqlite3');repo.initialize();service=RevisionService(repo)
    extraction=ReadOnlyRepository(ROOT/'outputs/development_pool_20261117/extraction.sqlite3')
    cases=[]
    for sid in SAMPLES:
        sample=samples[sid]
        candidate,index,doc,origin=reanchor(sample,docs[sample['candidate']['document_id']],supplements[sid],OUT,base,extraction)
        repo.register_v4_run({'run_id':candidate['run_id'],'mode':'shadow','document_path':doc['index_path'],
            'source_index_hash':doc['index_hash'],'source_hash':doc['document_version'],
            'ontology_hash':base.manifest['ontology_version'],**base.binding()},[candidate])
        row={'sample_id':sid,'role':'revision_review' if sid=='PILOT-024' else 'weighted_agent_audit',
            'original_binding':sample['binding'],'candidate':candidate,'source_document':doc,
            'candidate_hash':__import__('review_agent.contracts',fromlist=['sha256_json']).sha256_json(candidate)}
        if sid=='PILOT-024':
            initial=retrieve_initial(candidate,index,max_items=8);chunk=index['chunks'][0]
            evidence=verify_evidence_bundle(make_evidence_bundle([*initial['items'],_evidence(chunk,0,len(chunk['content']),'current_chunk')],
                ['original_quotes_and_bounded_parent_context','current_chunk']),index,candidate)
            ids=[x['evidence_id'] for x in evidence['items']]
            row['draft']=service.propose(candidate,repo.get_candidate(candidate['candidate_id'])['row_version'],changes_for(24,candidate),
                ids,'Engineering smoke of new revision delivery; a rejection is an allowed result.',evidence,index,protected_ids=ids)
        cases.append(row)
    write(OUT/'cases.json',cases);write(OUT/'baseline.json',{'watched_hashes':watched})
    write(OUT/'settings.json',public_settings(config))
    write(OUT/'protocol.json',{'version':'bounded_technical_smoke_20261118_v3','samples':list(SAMPLES),
        'parent_studies':[str(FIRST),str(SECOND)],'parent_requests':2,'cumulative_request_ceiling':20,
        'diagnostic_change':'Replay the completed matching nonce using supported enum equality; no third probe. Preserve both original receipts.',
        'channel_probe':{'model_family':'gemma','max_calls':0,'source_study':str(SECOND),'counts_as_vote':False},
        'max_new_requests':MAX_CALLS,'request_allocation':{'weighted_agent_audits':14,'revision_review':3},
        'known_token_stop':TOKEN_STOP,'study_seconds':1200,'retries':0,'parallel_validators':1,
        'stop_new_dispatch_on_unknown':True,'no_automatic_relaunch':True,'human_gold':False,
        'semantic_acceptance_required':False,'production_written':False,'published_shared_candidate_used':False,
        'base_release':base.binding()})
    frozen=fingerprints();write(OUT/'code_fingerprints.json',frozen)
    with ZipFile(OUT/'execution_code.zip','x',ZIP_DEFLATED) as archive:
        for name in frozen:
            if Path(name).suffix in {'.py','.txt'} or name.startswith('specs/'):
                archive.write(ROOT/name,name)
    return repo,base,config,cases,frozen


def run():
    from review_agent.cli import _configured_validators
    from review_agent.model_settings import create_planner_client
    from review_agent.request_execution import judge_typed
    from review_agent.channel_probe import judge_once,binding,VERSION as PROBE_VERSION
    from review_agent.contracts import sha256_json
    from review_agent.orchestrator import ReviewOrchestrator
    from review_agent.ontology_guard import OntologyGuard,load_snapshot
    from review_agent.revision_service import RevisionService
    from review_agent.case_budget import CaseBudget
    from review_agent.consensus import Committee
    from document.hierarchy_index import load_document_index
    from runs.regress_v8 import fingerprints
    from evaluation.regression_gate import request_accounting
    repo,base,config,cases,frozen=prepare();voters=_configured_validators(config)
    assert len(voters)==3 and len({v['model_family'] for v in voters})==3
    write(OUT/'started.json',{'started_at':time.time(),'automatic_relaunch':False})
    deadline=time.time()+1200;results=[];stop=None
    from .review_samples import ReadOnlyRepository
    original_receipt=read(SECOND/'channel_probe.json')
    source=ReadOnlyRepository(SECOND/'review.sqlite3').request_attempt(original_receipt['attempt_id'])
    expected=source['metadata']['proposal']['expected_token']
    receipt=assess_nonce_replay(original_receipt,expected)
    write(OUT/'channel_probe.json',receipt)
    print(json.dumps({'stage':'gemma_channel_probe','status':receipt['response_status'],
        'outcome_unknown':receipt.get('outcome_unknown',False)},ensure_ascii=False),flush=True)
    if receipt['response_status']!='ok':stop='channel_probe_not_healthy'
    for row in cases:
        if stop:break
        if blocked(repo) or time.time()>=deadline:stop='request_or_study_fence';break
        candidate=row['candidate'];sid=row['sample_id']
        try:
            if row['role']=='revision_review':
                revision_config=deepcopy(config['budget']);revision_config.update(max_llm_calls_per_candidate=3,total_timeout_seconds=540)
                case=repo.ensure_case(candidate['candidate_id'],{'llm_calls':3,'tokens':100000})
                budget=CaseBudget(revision_config,repo,[case],task_type='technical_revision_smoke',task_id=row['draft']['draft_id'],deadline_epoch=deadline)
                committee=Committee([v['id'] for v in voters],config['consensus']['required_votes'],config['consensus']['minimum_distinct_families'],
                    config['consensus']['version'],config['vote_weighting'])
                result=RevisionService(repo).review(row['draft']['draft_id'],[{**v,'case_ids':budget.case_ids} for v in voters],committee,budget)
                summary={'sample_id':sid,'role':row['role'],'status':result['status'],
                    'votes':[{k:v.get(k) for k in ('voter_id','response_status','decision','outcome_unknown','not_sent','response_projection')} for v in result.get('votes',[])],
                    'weight_snapshot':result.get('weight_snapshot')}
            else:
                index=load_document_index(row['source_document']['index_path'],candidate['source_index_hash'],require_verified=True)
                runner=ReviewOrchestrator(repo,OntologyGuard(load_snapshot(base.snapshot_path)),voters,config=config,
                    planner_client=create_planner_client(config),should_cancel=lambda:blocked(repo) or time.time()>=deadline)
                result=runner.audit_candidate(candidate['candidate_id'],candidate['revision'],index,
                    context={'execution_deadline_epoch':min(deadline,time.time()+540),'engineering_smoke':True,'labels_used':False})
                audit=repo.get_audit(candidate['candidate_id'],result.get('audit_id'))
                summary={'sample_id':sid,'role':row['role'],'status':audit['status'],'decision':repo.latest_decision(candidate['candidate_id']),
                    'execution_failure':audit['context'].get('execution_failure'),'stop_reason':audit['context'].get('stop_reason'),
                    'votes':[{k:v.get(k) for k in ('voter_id','response_status','outcome_unknown','not_sent')} for v in audit['votes']],
                    'consensus':[p.get('consensus') for p in audit['rounds']]}
            write(OUT/(sid+'_result.json'),result);write(OUT/(sid+'_summary.json'),summary)
            results.append(summary)
            print(json.dumps({'sample_id':sid,'status':summary['status'],'calls_so_far':len(receipts(repo))},ensure_ascii=False),flush=True)
        except Exception as exc:
            summary={'sample_id':sid,'role':row['role'],'status':'runtime_exception','type':type(exc).__name__,'error':str(exc)}
            write(OUT/(sid+'_error.json'),summary);results.append(summary);stop='runtime_exception'
        if blocked(repo):stop='new_request_unknown_or_budget_fence'
    rows=receipts(repo)
    with repo.connect() as con:
        accounting=request_accounting(con)
        counts={n:con.execute('SELECT COUNT(*) FROM '+n).fetchone()[0] for n in ('jobs','import_outbox','graph_outbox','ontology_publish_outbox')}
        applied=con.execute("SELECT COUNT(*) FROM revision_drafts WHERE status='applied'").fetchone()[0]
    unchanged=all(file_hash(p)==h for p,h in read(OUT/'baseline.json')['watched_hashes'].items())
    code_unchanged=fingerprints()==frozen
    sent=sum(not r['result'].get('not_sent') and r['result'].get('actual_calls')!=0 for r in rows)
    report={'protocol_version':read(OUT/'protocol.json')['version'],'cases':results,'planned_cases':3,'executed_cases':len(results),
        'channel_probe_status':receipt['response_status'],'request_attempts':len(rows),'requests_sent':sent,
        'known_input_tokens':sum(r['result'].get('input_tokens')or 0 for r in rows),
        'known_output_tokens':sum(r['result'].get('output_tokens')or 0 for r in rows),
        'unknown_attempt_ids':[r['attempt_id'] for r in rows if r['state']=='unknown'],
        'response_status_counts':dict(__import__('collections').Counter(r['result'].get('response_status') for r in rows)),
        'stop_reason':stop,'all_original_inputs_unchanged':unchanged,'code_unchanged':code_unchanged,
        'all_have_terminal_record':all(r['status']!='running' for r in results),'semantic_acceptance_required':False,
        'outbox_counts':counts,'revisions_applied':applied,'production_written':False,'human_gold':False,'price':None,
        'historical_unknown_reissued':False,'paper_accuracy':None,**accounting}
    first_rows=sum(read(folder/'report.json')['request_attempts'] for folder in (FIRST,SECOND))
    report['cumulative_new_request_attempts']=first_rows+len(rows)
    report['technical_gate_passed']=bool(len(results)==3 and receipt['response_status']=='ok' and accounting['requests_settled']
        and not any(r['status']=='runtime_exception' for r in results) and unchanged and code_unchanged and not applied and not any(counts.values()))
    assert first_rows+len(rows)<=20 and len(rows)<=MAX_CALLS and unchanged and code_unchanged and not applied and not any(counts.values())
    write(OUT/'report.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False),flush=True)


if __name__=='__main__':run()
