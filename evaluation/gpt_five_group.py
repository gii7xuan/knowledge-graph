"""Five-arm development comparison with shared, immutable first-round ballots."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

from review_agent.contracts import sha256_json
from .freeze_shared_ontology import ROOT,read,write

VERSION='gpt40_shared_ballot_comparison_v2'
GROUPS=('b0_all_keep','b1_single','b2_majority','b3_fixed_workflow','agent')


def settings(directory):
    from runs.regress_v8 import configuration
    from .formal_preparation import configuration_overrides,merge_overrides
    config=merge_overrides(configuration(profile='weighted_diagnostic',directory=directory),configuration_overrides())
    config['budget'].update(max_parallel_validators=3,max_evidence_items=64,max_evidence_chars=40000)
    for slot in config['validators']:slot['stream_responses']=True
    return config


class Fence:
    def __init__(self,limits):
        self.limits=limits;self.paths=[];self.records={};self.reservations=0
        self.stop=None;self.started=time.monotonic();self.lock=threading.RLock()
        self.stop_file=None;self.prior_semantics=set();self.suppressed_duplicates=0;self.prior_requests=0
        # Preserve unknown request identities, route the affected arm to human
        # review, and continue unrelated candidates/arms by default.
        self.skip_unknown_candidates=True;self.quarantined_candidates=set();self.handoff_targets=set()

    def register(self,path):
        with self.lock:self.paths.append(Path(path))

    def refresh(self):
        if self.stop_file and self.stop_file.exists():self.stop=self.stop or 'operator_stop_file'
        for path in self.paths:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as c:
                for attempt,state,raw,meta in c.execute('SELECT attempt_id,state,result_json,metadata_json FROM request_attempts'):
                    r=json.loads(raw or '{}')
                    metadata=json.loads(meta or '{}')
                    self.records[attempt]={'attempt_id':attempt,'state':state,
                        'arm':('common' if metadata.get('task_type')=='triple_review' else
                            path.stem if path.stem in {'agent','b3_fixed_workflow'} else 'common'),
                        'task_type':metadata.get('task_type'),'voter_id':metadata.get('binding',{}).get('voter_id'),
                        'candidate_id':(metadata.get('proposal') or {}).get('candidate_id') or
                            ((metadata.get('proposal') or {}).get('candidate') or {}).get('candidate_id'),
                        **{k:r.get(k) for k in ('input_tokens','output_tokens','model_id','response_status','not_sent','latency_ms')}}
        unknown=[r for r in self.records.values() if r['state']=='unknown']
        if unknown and self.skip_unknown_candidates:
            if any(not r.get('candidate_id') for r in unknown):self.stop=self.stop or 'unscoped_unknown_request'
            self.quarantined_candidates.update(r['candidate_id'] for r in unknown if r.get('candidate_id'))
            self.handoff_targets.update((r['candidate_id'],r['arm']) for r in unknown if r.get('candidate_id'))
        elif unknown:self.stop=self.stop or 'new_request_outcome_unknown'
        tokens=sum((r.get('input_tokens') or 0)+(r.get('output_tokens') or 0) for r in self.records.values())
        if tokens>=self.limits['known_tokens']:self.stop=self.stop or 'known_token_stop'
        if time.monotonic()-self.started>=self.limits['seconds']:self.stop=self.stop or 'study_timeout'

    def allow(self,candidate_id=None,arm='common'):
        with self.lock:
            self.refresh()
            if self.skip_unknown_candidates and (candidate_id is None and self.quarantined_candidates or
                (candidate_id,'common') in self.handoff_targets or (candidate_id,arm) in self.handoff_targets):return False
            if self.reservations>=self.limits['llm_calls']:self.stop=self.stop or 'study_call_cap'
            if self.stop:return False
            self.reservations+=1;return True

    def allow_request(self,voter,proposal,task_type,arm='common'):
        from review_agent.request_identity import semantic_request_key
        with self.lock:
            if self.skip_unknown_candidates:
                self.refresh()
                candidate_id=proposal.get('candidate_id') or proposal.get('candidate',{}).get('candidate_id')
                if (candidate_id,'common') in self.handoff_targets or (candidate_id,arm) in self.handoff_targets:return False
            if task_type=='planner_action' and semantic_request_key(task_type,voter,proposal,proposal.get('prompt_hash')) in self.prior_semantics:
                self.suppressed_duplicates+=1;return False
            # In skip mode this request-aware guard atomically reserves the call;
            # the separate zero-argument dispatch guard is disabled.
            if self.skip_unknown_candidates:return self.allow(candidate_id,arm)
        return True

    def snapshot(self):
        with self.lock:
            self.refresh();rows=list(self.records.values())
            return {'dispatch_reservations':self.reservations,'unique_provider_attempts':sum(not r.get('not_sent') for r in rows),
                'known_tokens':sum((r.get('input_tokens') or 0)+(r.get('output_tokens') or 0) for r in rows),
                'unknown_requests':sum(r['state'] in {'running','unknown'} for r in rows),
                'unknown_usage_requests':sum(not r.get('not_sent') and any(r.get(k) is None for k in ('input_tokens','output_tokens')) for r in rows),
                'prior_engineering_requests':self.prior_requests,'suppressed_exact_prior_tasks':self.suppressed_duplicates,
                'quarantined_candidate_ids':sorted(self.quarantined_candidates),
                'unknown_result_candidate_ids':sorted(self.quarantined_candidates),
                'unknown_requests_trigger_human_review':self.skip_unknown_candidates,'excluded_candidates':0,
                'stop_reason':self.stop,'limits':self.limits,'requests':rows}


def configure_clients(config,fence):
    from review_agent.cli import _configured_validators
    from review_agent.model_settings import create_planner_client
    voters=_configured_validators(config);planner=create_planner_client(config)
    arm=config.get('comparison_arm','common')
    guard=lambda voter,proposal,task_type:fence.allow_request(voter,proposal,task_type,arm)
    for v in voters:
        v['client'].dispatch_guard=None if fence.skip_unknown_candidates else fence.allow
        v['client'].request_dispatch_guard=guard
    if planner:
        planner.dispatch_guard=None if fence.skip_unknown_candidates else fence.allow
        planner.request_dispatch_guard=guard
    return voters,planner


def prepare(output,continue_from=None,resume_from=None):
    from .gpt_reference import build_reference
    from .manifest import freeze_manifest,save_manifest
    from document.hierarchy_index import load_document_index,persist_document_index
    from evidence.verification import verify_evidence_bundle
    from runs.regress_v8 import public_settings
    output=Path(output).resolve()
    if continue_from and resume_from:raise ValueError('choose interrupted-prefix recovery or remaining-candidate resume')
    if not output.is_relative_to(ROOT/'outputs') or output.exists():raise ValueError('new comparison folder required')
    reference=build_reference();samples=read(ROOT/'outputs/development_pool_20261117/pilot/review_samples.json')['samples']
    documents={d['document_id']:d for d in read(ROOT/'outputs/development_pool_20261117/source_manifest.json')['documents']}
    output.mkdir();(output/'sources').mkdir();inputs=[];indexes={}
    for s in samples:
        c=s['candidate'];d=documents[c['document_id']]
        index=load_document_index(d['index_path'],d['index_hash'],require_verified=True)
        verified=verify_evidence_bundle(s['evidence_bundle'],index,c)
        if verified['evidence_hash']!=s['binding']['evidence_hash'] or sha256_json(c)!=s['binding']['candidate_content_hash']:
            raise ValueError('original GPT packet material changed')
        if c['document_id'] not in indexes:
            path=output/'sources'/(c['document_id']+'.json');persist_document_index(index,path)
            indexes[c['document_id']]=str(path)
        inputs.append({'sample_id':s['sample_id'],'candidate':c,'reference_binding':s['binding'],
                       'initial_evidence_bundle':verified,'document_index_path':indexes[c['document_id']]})
    if len(inputs)!=40:raise ValueError('this protocol requires the original forty packets')
    config=settings(output);public=public_settings(config)
    protocol={'version':VERSION,'population':40,'partition':'development','label_source':'gpt_preannotation',
        'groups':GROUPS,'shared_first_round':True,'first_round_reuse':'B1/B2 are derived controls; B3/Agent fork identical original ballots without rebinding votes',
        'single_slot':'judge_c','majority_rule':'at least two of three explicit same verdicts; axis labels also use absolute majority',
        'comparison_limits':{'llm_calls':520,'known_tokens':15000000,'seconds':43200},
        'per_case_limits':config['budget'],'initial_packet_policy':'exact GPT-reference initial candidate and evidence; GPT labels never sent',
        'branches':'B3 and Agent start concurrently from the same post-initial checkpoint and deadline',
        'weight_policy':'frozen dynamic check_completion_v1 with common cold-start prior; no online reliability update',
        'primary_metrics':['source_support_reference_agreement','expression_reference_agreement','manual_recommendations','technical_incomplete','provider_usage'],
        'full_admission_quality_evaluated':False,'prior_extraction_unknown':'retained separately; no extraction request is repeated',
        'reference_binding_role':'identifies original GPT packet; current audit/proposal IDs are recorded separately',
        'technical_status_policy':'response/contract/execution errors are separate from valid semantic unknowns',
        'recovery_policy':'two supplements plus one focused review, no extra final committee reservation; global action caps still enforced',
        'clock_policy':'both branches share a post-initial deadline; administrative recovery pause excluded; actual initial-review wall time remains charged',
        'public_settings_hash':sha256_json(public),'human_gold_count':0,'production_writes':False}
    protocol['unknown_policy']='affected method/candidate goes to human review; continue remaining candidates; no unknown request is resent'
    protocol['manual_review_includes_technical_failure']=True
    if continue_from:
        from .gpt_comparison_recovery import continuation
        protocol['continuation']=continuation(Path(continue_from).resolve(),inputs)
    if resume_from:
        from .gpt_comparison_resume import preparation
        protocol['resume']=preparation(Path(resume_from).resolve(),inputs)
        protocol['unknown_policy']='human review includes semantic uncertainty and technical timeout/failure; retain all candidates in the denominator'
    write(output/'protocol.json',protocol);write(output/'public_settings.json',public)
    write(output/'model_inputs.json',inputs);write(output/'gpt_reference.json',reference)
    save_manifest(freeze_manifest([s['candidate'] for s in inputs],run_id=VERSION),output/'population_manifest.json')
    folders=('review_agent','evaluation','evidence','document','ontologyBuild','ontology_review','prompts','specs')
    files=[p for f in folders for p in (ROOT/f).rglob('*') if p.is_file() and p.suffix in {'.py','.txt','.json'} and '__pycache__' not in p.parts]
    write(output/'code_fingerprints.json',{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    write(output/'preparation_receipt.json',{'version':VERSION,'artifacts':{
        p:hashlib.sha256((output/p).read_bytes()).hexdigest() for p in
        ('protocol.json','public_settings.json','model_inputs.json','gpt_reference.json','code_fingerprints.json','population_manifest.json')}})
    return {'output':str(output),'population':40,'model_calls':0,'limits':protocol['comparison_limits']}


def axis_labels(axes):
    expression={'standard':'faithful','lossless_normalization':'needs_revision','semantic_revision':'needs_revision'}
    mapping={'valid':'correct','conflict':'incorrect','unassigned':'unassigned'}
    return {'source_support':axes.get('source',{}).get('status','unresolved'),
        'expression_status':expression.get(axes.get('expression',{}).get('status'),'unresolved'),
        'mapping_status':mapping.get(axes.get('mapping',{}).get('status'),'unresolved'),
        'knowledge_scope':axes.get('scope',{}).get('knowledge_scope','unresolved')}


def labels_from_votes(votes,slot=None):
    from review_agent.review_axes import vote_axes
    eligible=[v for v in votes if v.get('response_status')=='ok' and (slot is None or v['voter_id']==slot)]
    labels=[axis_labels(vote_axes(v)) for v in eligible];required=1 if slot else 2
    result={}
    for key in ('source_support','expression_status','mapping_status','knowledge_scope'):
        counts=Counter(r[key] for r in labels if r[key]!='unresolved')
        result[key]=next((k for k,n in counts.items() if n>=required),'unresolved')
    return result


def frame(sample,status='not_started',decision='defer',**extra):
    c=sample['candidate']
    return {'sample_id':sample['sample_id'],'candidate_id':c['candidate_id'],'revision':c['revision'],
        'candidate_content_hash':sha256_json(c),'binding':sample['reference_binding'],
        'binding_role':'original GPT reference packet, not a newly issued model request',
        'status':status,'predicted_decision':decision,'axis_labels':{},**extra}


def backup(source,destination):
    with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True)) as a:
        with closing(sqlite3.connect(destination)) as b, b:a.backup(b)


def deployment_usage(output,group,history=None):
    """Logical deployment cost includes the shared initial ballot once per arm."""
    if group=='b0_all_keep':return {'provider_attempts':0,'known_tokens':0,'unknown_usage_requests':0}
    database='common.sqlite3' if group in {'b1_single','b2_majority'} else group+'.sqlite3'
    records={}
    for folder in [*(Path(history).glob('case_*') if history else []),*output.glob('case_*')]:
        path=folder/database
        if not path.exists():path=folder/'common.sqlite3'
        if not path.exists():continue
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as connection:
            for attempt,metadata,result in connection.execute('SELECT attempt_id,metadata_json,result_json FROM request_attempts'):
                m=json.loads(metadata);r=json.loads(result or '{}')
                if group=='b1_single' and m.get('binding',{}).get('voter_id')!='judge_c':continue
                if not r.get('not_sent'):records[attempt]=r
    return {'provider_attempts':len(records),
        'known_tokens':sum((r.get('input_tokens') or 0)+(r.get('output_tokens') or 0) for r in records.values()),
        'unknown_usage_requests':sum(any(r.get(k) is None for k in ('input_tokens','output_tokens')) for r in records.values()),
        'accounting':'standalone deployment attribution, includes shared first-round requests; do not sum across arms'}


def action(name,**args):
    return {'action':name,'arguments':args,'target_issue':'shared frozen initial review',
            'rationale_summary':'Common initial evidence and independent ballot for all experimental arms.'}


def run(output,*,services=None):
    from document.hierarchy_index import load_document_index
    from ontologyBuild.release_service import load_release
    from review_agent.repository import ReviewRepository
    from review_agent.orchestrator import ReviewOrchestrator
    from review_agent.ontology_guard import OntologyGuard,load_snapshot
    from review_agent.runtime import AgentRuntime
    from .adapters.baselines import single_judge,simple_majority
    from .gpt_reference import compare,reliability
    from .vote_weighting_replay import replay
    from runs.regress_v8 import public_settings
    output=Path(output).resolve();protocol=read(output/'protocol.json')
    if (output/'started.json').exists():raise ValueError('comparison already started; inspect original receipts, do not relaunch')
    for p,h in read(output/'preparation_receipt.json')['artifacts'].items():
        if hashlib.sha256((output/p).read_bytes()).hexdigest()!=h:raise ValueError('frozen comparison artifact changed: '+p)
    if any(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h for p,h in read(output/'code_fingerprints.json').items()):
        raise ValueError('comparison code changed after preparation')
    config=settings(output)
    if sha256_json(public_settings(config))!=protocol['public_settings_hash']:raise ValueError('model or endpoint settings changed')
    inputs=read(output/'model_inputs.json');reference=read(output/'gpt_reference.json')
    fence=Fence(protocol['comparison_limits']);factory=services or configure_clients
    fence.stop_file=output/'STOP'
    if protocol.get('continuation'):
        from .gpt_comparison_recovery import seed
        seed(fence,protocol['continuation'])
    predictions={g:[frame(s) for s in inputs] for g in GROUPS};comparisons=[];replays=[]
    predictions['b0_all_keep']=[frame(s,'completed','accept',decision_is_official_admission=False) for s in inputs]
    resume_done=set();history=None
    if protocol.get('resume'):
        from .gpt_comparison_resume import restore
        predictions,comparisons,replays,resume_done=restore(output,protocol['resume'],inputs,fence)
        history=protocol['resume']['prior_directory']
    write(output/'started.json',{'time':time.time(),'protocol':VERSION,'limits':protocol['comparison_limits'],'zero_retries':True})

    def flush():
        metrics={}
        for group,rows in predictions.items():
            from .metrics import fixed_candidate_metrics
            operational=fixed_candidate_metrics(rows)
            metrics[group]={**compare(rows,reference),'status_counts':dict(Counter(r['status'] for r in rows)),
                'operational_metrics':operational,'manual_handoff_count':operational['outcomes']['manual'],
                'decision_counts':dict(Counter(r['predicted_decision'] for r in rows)),
                'manual_or_pending_count':sum(r['predicted_decision']=='defer' for r in rows),
                'completed_manual_recommendations':sum(r['status']=='completed' and r['predicted_decision']=='defer' for r in rows),
                'technical_incomplete_count':operational['technical_failure_handoff_count'],
                'skipped_unknown_count':sum(r['status']=='skipped_unknown' for r in rows),
                'not_started_count':sum(r['status']=='not_started' for r in rows),
                'deployment_usage':deployment_usage(output,group,history),'population':len(inputs)}
            path=output/(group+'_predictions.json');path.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
            from .human_handoff import outcome,normalize
            queue=[normalize(r) for r in rows if outcome(r)=='manual']
            (output/(group+'_human_review_queue.json')).write_text(json.dumps(queue,ensure_ascii=False,indent=2),encoding='utf-8')
        report={'version':VERSION,'label_source':'gpt_preannotation','human_gold_count':0,'population':len(inputs),
            'groups':metrics,'usage':fence.snapshot(),'full_admission_quality_evaluated':False,'production_written':False,
            'technical_failure_is_subset_of_manual':True,'excluded_candidates':0}
        (output/'comparison_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return report

    flush()
    for ordinal,sample in enumerate(inputs):
        if fence.snapshot()['stop_reason']:break
        if sample['candidate']['candidate_id'] in fence.quarantined_candidates:
            from .gpt_comparison_resume import quarantine
            quarantine(output,ordinal,sample,predictions,fence);flush();continue
        if sample['sample_id'] in resume_done:continue
        folder=output/('case_'+str(ordinal+1).zfill(2));folder.mkdir()
        reused=(protocol.get('continuation') or {}).get('common_databases',{}).get(sample['sample_id'])
        if reused:backup(reused,folder/'common.sqlite3')
        repository=ReviewRepository(folder/'common.sqlite3');repository.initialize();fence.register(repository.database_path)
        c=sample['candidate'];index=load_document_index(sample['document_index_path'],c['source_index_hash'],require_verified=True)
        release=load_release(c['ontology_release_path'])
        if not reused:
            repository.register_v4_run({'run_id':c['run_id'],'mode':'shadow','document_path':sample['document_index_path'],
                'source_hash':c['document_version'],'candidate_contract_version':c['contract_version'],
                'source_index_hash':c['source_index_hash'],'ontology_hash':c['ontology_version'],**release.binding()},[c])
        voters,planner=factory(config,fence)
        owner=ReviewOrchestrator(repository,OntologyGuard(load_snapshot(release.snapshot_path)),voters,config=config,planner_client=planner)
        runtime=AgentRuntime(owner)
        try:
            if reused:
                from .gpt_comparison_recovery import restore_initial
                restore_initial(runtime,c,index)
            else:
                runtime.initialize(c['candidate_id'],c['revision'],index,{'initial_evidence_bundle':sample['initial_evidence_bundle'],
                    'comparison_input_packet_hash':sha256_json(sample['reference_binding'])})
                runtime.state.step_no+=1
                runtime.execute(action('inspect_ontology',relation_id=c['triple'].get('relation_id'),
                    surface_relation=c['triple']['surface_relation'],issue='Check common frozen ontology'))
                runtime.state.step_no+=1;runtime.execute(action('run_validation'));runtime.checkpoint()
            audit=repository.get_audit(c['candidate_id'],runtime.state.audit_id)
            proposal=audit['rounds'][-1];votes=[v for v in audit['votes'] if v.get('proposal_digest')==proposal['proposal_digest']]
            if proposal['evidence_bundle']['evidence_hash']!=sample['reference_binding']['evidence_hash']:
                raise ValueError('shared first ballot departed from the frozen initial evidence')
            single=next((v for v in votes if v['voter_id']=='judge_c'),{'response_status':'not_sent'})
            for group,result,labels in (
                ('b1_single',single_judge(single),labels_from_votes(votes,'judge_c')),
                ('b2_majority',simple_majority(votes),labels_from_votes(votes))):
                verdict=result['verdict'];technical=verdict not in {'valid','invalid'} and any(v.get('response_status')!='ok' for v in ([single] if group=='b1_single' else votes))
                decision='accept' if verdict=='valid' and runtime.state.guard['status']=='pass' else 'reject' if verdict=='invalid' else 'defer'
                predictions[group][ordinal]=frame(sample,'failed' if technical else 'completed',decision,
                    axis_labels=labels,shared_initial_proposal_digest=proposal['proposal_digest'],baseline=result)
            for v in votes:
                if v.get('response_status')=='ok':comparisons.append({**frame(sample),
                    'axis_labels':labels_from_votes([v],v['voter_id']),
                    **{k:v[k] for k in ('voter_id','model_identity','model_family')}})
            try:replays.append(replay(proposal,votes,config.get('vote_weighting')))
            except (ValueError,KeyError,TypeError) as exc:replays.append({'error':str(exc),'candidate_id':c['candidate_id']})
            if fence.snapshot()['stop_reason']:
                flush();break
            if c['candidate_id'] in fence.quarantined_candidates:
                from .gpt_comparison_resume import quarantine
                quarantine(output,ordinal,sample,predictions,fence);flush()
                print(json.dumps({'case':ordinal+1,'status':'skipped_unknown','usage':{k:v for k,v in fence.snapshot().items() if k!='requests'}},ensure_ascii=False),flush=True)
                continue
            from .gpt_comparison_recovery import branch_window
            branch_window(runtime,folder)
            branch_paths={}
            for group in ('b3_fixed_workflow','agent'):
                path=folder/(group+'.sqlite3');backup(repository.database_path,path);branch_paths[group]=path;fence.register(path)

            def branch(group):
                branch_config=deepcopy(config)
                branch_config['comparison_arm']=group
                if group=='b3_fixed_workflow':branch_config['planner']['fixed_workflow_policy']='fixed_candidate_workflow_v1'
                branch_repo=ReviewRepository(branch_paths[group]);vs,pl=factory(branch_config,fence)
                engine=ReviewOrchestrator(branch_repo,OntologyGuard(load_snapshot(release.snapshot_path)),vs,config=branch_config,planner_client=pl)
                result=engine.audit_candidate(c['candidate_id'],c['revision'],index,context={'audit_id':runtime.state.audit_id})
                current=branch_repo.get_revision(c['candidate_id'])
                if sha256_json(current)!=sha256_json(c):raise ValueError('fixed candidate was modified')
                operation=result.get('research_operation');decision={'accept':'accept','discard':'reject'}.get(operation,'defer')
                from .gpt_comparison_recovery import projection
                projected=projection(branch_repo,c,result,proposal['proposal_digest'])
                return frame(sample,projected['status'],decision,axis_labels=axis_labels(projected['axes']),
                    research_operation=operation,stop_reason=result.get('stop_reason'),audit_id=result.get('audit_id'),
                    independent_review_completed=result.get('independent_review_completed'),
                    technical_status=result.get('technical_status'),required_human_action=result.get('required_human_action'),
                    axis_projection_role=projected['role'],projection_evidence_hash=projected['evidence_hash'],
                    technical_failures=projected['technical_failures'],
                    shared_initial_proposal_digest=proposal['proposal_digest'],weight_snapshot=result.get('weight_snapshot'))
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures={g:pool.submit(branch,g) for g in branch_paths}
                for group,future in futures.items():
                    try:predictions[group][ordinal]=future.result()
                    except Exception as exc:
                        predictions[group][ordinal]=frame(sample,'failed',error=type(exc).__name__+': '+str(exc))
                        fence.stop=fence.stop or 'branch_runtime_error'
        except Exception as exc:
            for group in GROUPS[1:]:
                if predictions[group][ordinal]['status']=='not_started':predictions[group][ordinal]=frame(sample,'failed',error=type(exc).__name__+': '+str(exc))
            fence.stop=fence.stop or 'shared_initial_execution_error'
        fence.snapshot()
        if c['candidate_id'] in fence.quarantined_candidates:
            from .gpt_comparison_resume import quarantine
            quarantine(output,ordinal,sample,predictions,fence)
        report=flush()
        print(json.dumps({'case':ordinal+1,'sample_id':sample['sample_id'],'usage':{k:v for k,v in report['usage'].items() if k!='requests'}},ensure_ascii=False),flush=True)
    write(output/'initial_model_reference_comparisons.json',comparisons)
    write(output/'exploratory_gpt_reliability.json',reliability(comparisons,reference))
    write(output/'weight_replay.json',replays)
    report=flush();write(output/'finished.json',{'time':time.time(),'stop_reason':report['usage']['stop_reason'],'real_provider_requests':report['usage']['unique_provider_attempts']})
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    parser.add_argument('--continue-from')
    parser.add_argument('--resume-from')
    parser.add_argument('--run',action='store_true');args=parser.parse_args()
    if args.run:run(args.output)
    else:print(json.dumps(prepare(args.output,args.continue_from,args.resume_from),ensure_ascii=False))


if __name__=='__main__':main()
