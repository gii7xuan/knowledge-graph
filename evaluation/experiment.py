"""Isolated, label-blind controlled review experiments on one frozen pool."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
from uuid import uuid4
from ontologyBuild.release_catalog import ReleaseCatalog
from ontologyBuild.release_service import load_release
from document.hierarchy_index import persist_document_index
from document.index_integrity import validate_document_index
from review_agent.contracts import sha256_json
from review_agent.memory_scope import scope_from_split,scope_allows,scope_hash
from review_agent.repository import ReviewRepository
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.batch import versions_for
from .manifest import freeze_manifest,validate_manifest,key
from .request_meter import RequestMeter
from .adapters.baselines import single_judge,simple_majority
from .metrics import usage_metrics

METHODS={'rules','single','majority','fixed_workflow','agent'}
ABLATIONS={'no_targeted_retrieval','no_issue_resolution','no_counterevidence_resolution','no_extension'}


def save(path,value):
    with path.open('x',encoding='utf-8') as stream: json.dump(value,stream,ensure_ascii=False,indent=2)


def default_services(config):
    from review_agent.cli import _configured_validators
    from review_agent.model_settings import create_planner_client
    from ontology_review.service import effective_config
    return {'validators':_configured_validators(config),'planner':create_planner_client(config),
        'ontology_planner':create_planner_client(effective_config(config))}


def configure(config,group,scope,limits):
    result=deepcopy(config)
    result.update(mode='shadow',case_budget={k:limits[k] for k in ('llm_calls','tokens','tool_calls','extension_requests','rebinds')})
    result.setdefault('memory',{}).update(scope=scope)
    result.setdefault('routing',{}).update(auto_accept_enabled=False,semantic_auto_reject_enabled=False)
    result.setdefault('neo4j',{})['auto_import_enabled']=False
    planner=result.setdefault('planner',{})
    planner.update(enabled=group['method']=='agent',strategy='llm_react' if group['method']=='agent' else 'deterministic')
    from review_agent.planner_contracts import TOOL_SCHEMAS
    allowed=set(planner.get('allowed_tools') or TOOL_SCHEMAS)
    ablations=group.get('ablations',[])
    if 'no_targeted_retrieval' in ablations: allowed-={'search_evidence','inspect_context'}
    dynamic=group.get('ontology_mode','fixed')=='dynamic' and 'no_extension' not in ablations
    if not dynamic: allowed-={'request_ontology_extension','get_ontology_extension_status'}
    planner['allowed_tools']=sorted(allowed)
    result.setdefault('budget',{}).update(max_llm_calls_per_candidate=limits['llm_calls'],max_total_tokens=limits['tokens'],
        max_tool_calls=limits['tool_calls'],total_timeout_seconds=limits['seconds'])
    extension=result.setdefault('ontology_extension',{})
    extension.update(enabled=dynamic,auto_publish_enabled=False)
    extension.setdefault('budget',{}).update(max_llm_calls_per_candidate=limits['llm_calls'],max_total_tokens=limits['tokens'],
        max_tool_calls=limits['tool_calls'],total_timeout_seconds=limits['seconds'])
    return result


def seed_catalog(base,root):
    destination=root/base.manifest['release_id'];destination.mkdir(parents=True)
    for name in [*base.manifest['artifacts'],'release_manifest.json']: shutil.copyfile(base.path/name,destination/name)
    catalog=ReleaseCatalog(root);connection=catalog.connect()
    try:
        with connection:
            event={**base.binding(),'event_type':'isolated_experiment_baseline','base_release_id':None}
            connection.execute('INSERT INTO ontology_release_events(release_id,base_release_id,event_json) VALUES(?,NULL,?)',(base.manifest['release_id'],json.dumps(event)))
            connection.execute('INSERT INTO ontology_release_head VALUES(1,?,1)',(base.manifest['release_id'],))
    finally: connection.close()
    return catalog


def baseline(repository,candidate,document,release,config,voters,method):
    from evidence.hierarchical_retriever import retrieve_initial
    from evidence.verification import verify_evidence_bundle
    from review_agent.validators import judge_independently,model_request_count
    from review_agent.case_budget import CaseBudget
    from review_agent.memory import ReviewMemory
    from review_agent.memory_scope import resolve_scope
    budget=CaseBudget(config['budget'],repository,[repository.ensure_case(candidate['candidate_id'],config['case_budget'])],task_type='triple_review',task_id=candidate['candidate_id'])
    if not budget.consume_tool() or not budget.consume_tool(): raise RuntimeError('baseline tool budget exhausted')
    guard=OntologyGuard(load_snapshot(release.snapshot_path)).check(candidate)
    bundle=verify_evidence_bundle(retrieve_initial(candidate,document),document,candidate)
    if method=='rules':
        triple=candidate['triple']; surface=triple.get('surface_relation') or triple.get('relation_label') or ''
        terms=[triple['head'],surface,triple['tail'],*[str(v) for k,v in triple['qualifiers'].items() if k not in {'polarity','modality','quantifier'} and v]]
        positive=triple['qualifiers'].get('polarity')=='positive' and triple['qualifiers'].get('modality')=='asserted'
        supported=positive and all(terms) and any(all(term in ev['quote'] for term in terms) for ev in bundle['items'])
        return {'predicted_decision':'accept' if guard['status']=='pass' and supported else 'defer','baseline':'literal_cooccurrence_with_guard',
            'source_support':'rule_supported' if supported else 'unknown','evidence_anchors':bundle['items']}
    if not voters: raise ValueError('baseline model services are not configured')
    voters=voters[:1] if method=='single' else voters
    owner=ReviewOrchestrator(repository,OntologyGuard(load_snapshot(release.snapshot_path)),voters,config=config)
    scope=resolve_scope(config);memory=ReviewMemory(repository).lookup(scope=scope,exclude_candidate_id=candidate['candidate_id'],domain=candidate.get('domain'))
    context={**versions_for(config),'mode':'shadow','memory_scope':scope,'memory_scope_hash':scope_hash(scope),
        'memory_snapshot_id':memory['snapshot_id'],'memory_bundle_hash':sha256_json(memory),'review_memory':memory,
        'configured_strategy':method,'executed_strategy':method}
    audit_id=repository.start_audit(candidate['candidate_id'],candidate['revision'],context)
    proposal=owner._proposal(audit_id,candidate,guard,bundle,1,context)
    retry=min(int(config['budget'].get('max_retries_per_request',0)),max(0,budget.remaining_calls()//len(voters)-1))
    reserved=len(voters)*(1+retry)
    if not budget.reserve_calls(reserved): raise RuntimeError('cannot reserve baseline reviewers within shared budget')
    votes=judge_independently(voters,proposal,repository=repository,max_retries=retry,
        per_call_timeout_seconds=min(config['budget'].get('per_call_timeout_seconds',45),budget.remaining_seconds()))
    budget.refund_calls(reserved-sum(model_request_count(v) for v in votes))
    for vote in votes:
        for attempt in vote.get('attempts') or [vote]: budget.tokens(attempt)
    result=single_judge(votes[0]) if method=='single' else simple_majority(votes)
    repository.append_round(audit_id,proposal,votes,result,{})
    from review_agent.repository import utc_now
    with repository.transaction(immediate=True) as connection:
        connection.execute("UPDATE audit_runs SET status='completed',completed_at=? WHERE audit_id=?",(utc_now(),audit_id))
    prediction='accept' if result['verdict']=='valid' and guard['status']=='pass' else 'reject' if result['verdict']=='invalid' else 'defer'
    return {'audit_id':audit_id,'predicted_decision':prediction,'baseline_result':result,'evidence_anchors':bundle['items']}


def run_experiment(spec,candidates,documents,base_release,output,config,*,services_factory=None,memory_cases=None,manifest=None):
    """No truth labels are accepted by this execution API. Evaluation is separate."""
    from review_agent.configuration import normalize_config
    config=normalize_config(config)
    if set(spec)-{'split','groups','limits','prices','experiment_id','evaluation_partition'}: raise ValueError('unknown experiment spec fields; labels are evaluated separately')
    scope=scope_from_split(spec['split'])
    groups=spec.get('groups') or []
    if not groups or len({g['id'] for g in groups})!=len(groups): raise ValueError('unique experimental groups required')
    limits={'llm_calls':48,'tokens':120000,'tool_calls':48,'extension_requests':1,'rebinds':2,'seconds':240,**spec.get('limits',{})}
    if set(limits)-{'llm_calls','tokens','tool_calls','extension_requests','rebinds','seconds'}: raise ValueError('unknown experiment limit')
    if any(type(v) not in (int,float) or v<=0 for v in limits.values()): raise ValueError('experiment limits must be positive')
    if any(type(limits[k]) is not int for k in limits if k!='seconds'): raise ValueError('count budgets must be integers')
    for group in groups:
        if set(group)-{'id','method','ontology_mode','ablations'}: raise ValueError('unknown experiment group field')
        if (not re.fullmatch('[a-z][a-z0-9_-]{0,63}',group['id']) or group.get('method') not in METHODS
                or set(group.get('ablations',[]))-ABLATIONS or group.get('ontology_mode','fixed') not in {'fixed','dynamic'}): raise ValueError('invalid experimental group')
        if group.get('ontology_mode')=='dynamic' and group['method']!='agent': raise ValueError('dynamic extensions require the full agent')
        if group.get('ablations') and group['method']!='agent': raise ValueError('ablations apply to the full agent')
    if any(set(c)&{'label','labels','gold','truth','revision_truth','annotation'} for c in candidates): raise ValueError('experiment candidates must be label-blind')
    partition=spec.get('evaluation_partition','test')
    if partition not in {'test','calibration','train'}: raise ValueError('unknown evaluation partition')
    permitted=set(spec['split'].get(partition+'_document_ids',[]))
    if any(c.get('document_id') not in permitted for c in candidates): raise ValueError('candidate outside the declared evaluation partition')
    base=load_release(base_release)
    for candidate in candidates:
        if candidate.get('contract_version')!='extraction_candidate_v4': raise ValueError('controlled experiments require v4 source candidates')
        if any(candidate.get(k)!=v for k,v in base.binding().items() if k in {'ontology_release_id','ontology_version','profile_hash'}): raise ValueError('initial candidate release differs from the common baseline')
    manifest=manifest or freeze_manifest(candidates,run_id=spec.get('experiment_id','controlled_experiment'))
    validate_manifest(manifest)
    if {key(c):sha256_json(c) for c in candidates}!={key(c):c['candidate_content_hash'] for c in manifest['candidates']}: raise ValueError('candidate pool differs from frozen manifest')
    indexes=list(documents.values()) if isinstance(documents,dict) else list(documents)
    for document in indexes: validate_document_index(document)
    def document_for(candidate):
        found=[d for d in indexes if d['document_id']==candidate['document_id'] and d['document_version']==candidate['document_version'] and d['index_content_hash']==candidate['source_index_hash']]
        if len(found)!=1: raise ValueError('missing or ambiguous frozen source index')
        return found[0]
    for candidate in candidates: document_for(candidate)
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    save(output/'population_manifest.json',manifest)
    root=Path(__file__).resolve().parents[1]
    paths=[p for directory in ('evaluation','review_agent','ontology_review','schema','document','evidence','ontologyBuild','prompts') for p in (root/directory).rglob('*')
        if p.is_file() and p.suffix in {'.py','.txt','.md'} and '__pycache__' not in p.parts]
    save(output/'experiment_manifest.json',{'version':'controlled_review_experiment_v1','spec':spec,'population_hash':manifest['manifest_hash'],
        'base_release_id':base.manifest['release_id'],'memory_scope':scope,'config_hash':sha256_json(config),
        'source_sha256':{p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)},
        'model_pool':[{k:s.get(k) for k in ('id','model','family','model_identity')} for s in config.get('validators',[])],
        'production_graph_connected':False,'actual_admission_enabled':False,
        'test_text_policy':'unlabeled test text may be read; no held-out truth is provided to models'})
    summaries=[];reference_pool=None
    for group in groups:
        folder=output/group['id'];folder.mkdir();started=time.monotonic()
        catalog=seed_catalog(base,folder/'releases');repository=ReviewRepository(folder/'review.sqlite3');repository.initialize()
        effective=configure(config,group,scope,limits);effective['ontology_release_root']=str(folder/'releases')
        seeded=0
        with repository.transaction(immediate=True) as connection:
            for case in memory_cases or []:
                if not scope_allows(scope,case.get('candidate') or {}): continue
                if case.get('label_source')!='human_confirmed': raise ValueError('training memory must have explicit human provenance')
                from review_agent.labels import validate_labels
                validate_labels(case.get('labels') or {});seeded+=1
                triple=(case['candidate'].get('triple') or {})
                connection.execute('INSERT INTO memory_cases(case_id,source_event_id,memory_version,relation_id,domain,issue_type,case_json,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    ('training_'+str(seeded),'training_event_'+str(seeded),seeded,triple.get('relation_id'),case['candidate'].get('domain'),case.get('reason_code'),json.dumps(case),str(seeded)))
        runs={}
        for candidate in candidates:
            document=document_for(candidate)
            if candidate['run_id'] in runs and runs[candidate['run_id']]!=document['index_content_hash']: raise ValueError('one run cannot bind multiple documents')
            if candidate['run_id'] not in runs:
                path=persist_document_index(document,folder/('source_'+str(len(runs))+'.json'))
                repository.create_run({'run_id':candidate['run_id'],'mode':'shadow','document_path':str(path),'source_index_hash':document['index_content_hash'],
                    'ontology_path':str(catalog.current().snapshot_path),'ontology_hash':base.manifest['ontology_version'],'candidate_contract_version':'extraction_candidate_v4'})
                runs[candidate['run_id']]=document['index_content_hash']
            repository.add_candidate(deepcopy(candidate))
        meter=RequestMeter(folder/'request_journal.jsonl',limits)
        def services(case_ids,ontology=False):
            nonlocal reference_pool
            raw=(services_factory or default_services)(effective)
            pool=[{'id':v['id'],'model_id':v.get('model_id') or v['client'].model_id,
                'family':v.get('model_family') or v['client'].model_family,
                'identity':v.get('model_identity') or v.get('model_id') or v['client'].model_id} for v in raw.get('validators',[])]
            if reference_pool is None: reference_pool=pool
            elif pool!=reference_pool: raise ValueError('model pool changed between controlled groups/tasks')
            prefix='ontology' if ontology else 'triple'
            return {'validators':[{**v,'client':meter.wrap(v['client'],prefix+'_validator',case_ids)} for v in raw.get('validators',[])],
                'planner':meter.wrap(raw.get('ontology_planner' if ontology else 'planner'),prefix+'_planner',case_ids)}
        errors={};results={};extensions=[]
        def audit(cid):
            candidate=repository.get_revision(cid);document=document_for(candidate)
            release=load_release(folder/'releases'/candidate['ontology_release_id'])
            svc=services([cid])
            if group['method'] in {'rules','single','majority'}:
                result=baseline(repository,candidate,document,release,effective,svc['validators'],group['method'])
            else:
                context={'request_cache_namespace':'experiment:'+group['id'],'experimental_policy':{a:True for a in group.get('ablations',[])}}
                result=ReviewOrchestrator(repository,OntologyGuard(load_snapshot(release.snapshot_path)),svc['validators'],config=effective,planner_client=svc['planner']).audit_candidate(cid,candidate['revision'],document,context=context)
            results[cid]=result
            errors.pop(cid,None)
        for candidate in candidates:
            try: audit(candidate['candidate_id'])
            except Exception as exc: errors[candidate['candidate_id']]=type(exc).__name__+': '+str(exc)
        if effective['ontology_extension']['enabled']:
            from ontology_review.runtime import OntologyReviewRuntime
            from ontology_review.publisher import OntologyPublisher
            publisher_config=deepcopy(effective);publisher_config['mode']='active';publisher_config['ontology_extension']['auto_publish_enabled']=True
            publisher=OntologyPublisher(repository,folder/'releases',publisher_config)
            for _ in range(limits['rebinds']+1):
                tasks=[t for t in repository.extensions(limit=1_000_000) if t['status'] in {'proposed','needs_rebase'}]
                if not tasks: break
                published=False
                for task in tasks:
                    task=repository.extension_status(task['extension_id']);record={'extension_id':task['extension_id'],'delta_revision':task['delta_revision'],'recommendation':'unknown'}
                    case_ids=[s['candidate_id'] for s in task['subscriptions'] if s['status']=='active']
                    try:
                        if task['base_release_id']!=catalog.current().manifest['release_id']:
                            task=publisher.rebase(task['extension_id'],task['row_version'],catalog.current().manifest['release_id'])
                        svc=services(case_ids,ontology=True)
                        result=OntologyReviewRuntime(repository,folder/'releases',svc['validators'],svc['planner'],effective).run(task['extension_id'])
                        audited=repository.ontology_audit(result['audit_id']);proposal=next((p for p in audited['proposals'] if p['proposal_digest']==result.get('selected_proposal_digest')),None)
                        record.update(delta_revision=task['delta_revision'],recommendation=result['recommendation'],audit_id=result['audit_id'],proposal_digest=result.get('selected_proposal_digest'))
                        if proposal:
                            record['modeling_signature']=sha256_json([proposal['base_release_id'],[{k:o[k] for k in ('kind','target','changes')} for o in proposal['operations']]])
                            record['affected_candidate_ids']=[c['candidate_id'] for c in proposal['check_records']['impact']['affected_candidates']]
                        if result['recommendation']=='approve' and proposal and proposal['risk_category']=='additive':
                            preview=publisher.preview(task['extension_id'])
                            if not preview['requires_difference_confirmation']:
                                publication=publisher.publish(task['extension_id'],{'expected_row_version':preview['expected_row_version'],
                                    'proposal_digest':preview['proposal_digest'],'expected_base_release_id':preview['base_release_id'],
                                    'approved_by':'isolated_experiment_additive_policy','reason':'Fixed committee approved within isolated experiment.'},automatic=True)
                                record['published_release_id']=publication['ontology_release_id'];published=True
                    except Exception as exc: record['error']=type(exc).__name__+': '+str(exc)
                    extensions.append(record)
                if not published: break
                for candidate in candidates:
                    current=repository.get_candidate(candidate['candidate_id'])
                    if current.get('workflow_state')=='reaudit_required':
                        try: audit(candidate['candidate_id'])
                        except Exception as exc: errors[candidate['candidate_id']]=type(exc).__name__+': '+str(exc)
        # The experiment executes synchronously, so acknowledge only jobs backed
        # by an actual saved audit. Unexecuted/human-held work stays explicit.
        with repository.connect() as connection:
            queued=connection.execute("SELECT job_id,job_type,payload_json FROM jobs WHERE status='queued'").fetchall()
        for job in queued:
            payload=json.loads(job['payload_json']);audits=[]
            if job['job_type']=='ontology_extension':
                record=next((r for r in reversed(extensions) if r['extension_id']==payload.get('extension_id') and r.get('audit_id')),None)
                if record: audits=[repository.ontology_audit(record['audit_id'])]
            else:
                for cid in payload.get('candidate_ids',[]):
                    result=results.get(cid,{})
                    if result.get('audit_id') and result.get('revision')==repository.get_candidate(cid)['revision']:
                        audits.append(repository.get_audit(cid,result['audit_id']))
            if audits:
                repository.complete_job(job['job_id'],{'execution':'synchronous_isolated_experiment','audit_ids':[a['audit_id'] for a in audits]},
                    failed=any(a['status']!='completed' for a in audits))
        predictions=[]
        for original in candidates:
            cid=original['candidate_id'];current=repository.get_revision(cid);result=results.get(cid,{})
            audit_record=repository.get_audit(cid,result['audit_id']) if result.get('audit_id') else None
            rounds=(audit_record or {}).get('rounds',[])
            selected=next((r for r in rounds if r['proposal_digest']==result.get('selected_proposal_digest')),None)
            anchors=result.get('evidence_anchors') or (selected or (rounds[-1] if rounds else {})).get('evidence_bundle',{}).get('items',[])
            consensus=result.get('admission_consensus') or result.get('consensus') or {}
            verdict=consensus.get('result') if isinstance(consensus,dict) else None
            overall=(result.get('consensus') or {}).get('result')
            prediction=result.get('predicted_decision') or ('accept' if verdict=='accept_consensus' else 'reject' if overall=='reject_consensus' else 'defer')
            status=result.get('status','completed')
            if status not in {'timeout','interrupted','paused','failed','cancelled'}: status='completed'
            predictions.append({'candidate_id':cid,'revision':original['revision'],'candidate_content_hash':sha256_json(original),
                'candidate':original,'final_candidate':current,'final_revision':current['revision'],'decision':result.get('decision','manual_review'),
                'predicted_decision':prediction,'decision_is_official_admission':False,
                'status':'failed' if cid in errors else status,'error':errors.get(cid),'audit_id':result.get('audit_id'),
                'stop_reason':result.get('stop_reason'),'budget':result.get('budget'),
                'projection_status':'not_run',
                'selected_round_id':result.get('selected_round_id'),'selected_proposal_digest':result.get('selected_proposal_digest'),
                'evidence_anchors':anchors,'issue_history':result.get('issue_history') or (audit_record or {}).get('context',{}).get('issue_history',[]),
                'configured_strategy':group['method'],'executed_strategy':(audit_record or {}).get('context',{}).get('executed_strategy',group['method'])})
        requests=meter.snapshot()
        save(folder/'predictions.json',predictions);save(folder/'extensions.json',extensions);save(folder/'requests.json',requests)
        summary={'id':group['id'],'method':group['method'],'ontology_mode':group.get('ontology_mode','fixed'),'ablations':group.get('ablations',[]),
            'population_hash':manifest['manifest_hash'],'initial_release_id':base.manifest['release_id'],'final_release_id':catalog.current().manifest['release_id'],
            'memory_scope_hash':scope_hash(scope),'seeded_training_cases':seeded,'limits':limits,'runtime_seconds':time.monotonic()-started,
            'completed':sum(p['status']=='completed' for p in predictions),'failed':sum(p['status']=='failed' for p in predictions),
            'status_counts':dict(Counter(p['status'] for p in predictions)),'usage':usage_metrics(requests,spec.get('prices')),
            'formal_admission_count':sum(len(repository.current_admitted_contributions(c['candidate_id'])['desired']) for c in candidates)}
        summary['case_accounts']=[repository.case_status(repository.ensure_case(c['candidate_id'],effective['case_budget'])) for c in candidates]
        summary['model_pool']=reference_pool
        with repository.connect() as connection:
            summary['unhandled_jobs']=connection.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0]
        summary['artifact_sha256']={name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in ('predictions.json','extensions.json','requests.json')}
        save(folder/'summary.json',summary);summaries.append(summary)
    report={'version':'controlled_review_experiment_v1','groups':summaries,'population_hash':manifest['manifest_hash'],
        'truth_labels_used_during_execution':False,'quality_evaluated':False}
    save(output/'experiment_result.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',required=True);parser.add_argument('--candidates',required=True);parser.add_argument('--documents',required=True)
    parser.add_argument('--initial-release',required=True);parser.add_argument('--config',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--manifest');parser.add_argument('--training-memory')
    args=parser.parse_args()
    def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
    from document.hierarchy_index import load_document_index
    documents=[load_document_index(p,require_verified=True) for p in read(args.documents)]
    result=run_experiment(read(args.spec),read(args.candidates),documents,args.initial_release,args.output,read(args.config),
        manifest=read(args.manifest) if args.manifest else None,memory_cases=read(args.training_memory) if args.training_memory else None)
    print(json.dumps({'output':args.output,'groups':len(result['groups']),'quality_evaluated':False}))


if __name__=='__main__': main()
