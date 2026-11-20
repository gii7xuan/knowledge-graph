"""Rerun frozen GPT-40 inputs with revision support and a shared dispatch fence."""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import time
from zipfile import ZipFile,ZIP_DEFLATED

from .agent_resolution import prepare as prepare_resolution,verify as verify_resolution,drain_reaudits,seal
from .gpt_five_group import Fence,configure_clients,labels_from_votes
from .gpt_reference import compare
from .frozen_regression import read,save
from document.hierarchy_index import load_document_index
from ontologyBuild.release_service import load_release
from review_agent.contracts import sha256_json
from review_agent.repository import ReviewRepository
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from runs.regress_v8 import ROOT,configuration
from runs.revalidate_v8 import digest

LIMITS={'llm_calls':520,'known_tokens':15000000,'seconds':43200}


def select_inputs(inputs,sample_ids=None):
    if len(inputs)!=40:raise ValueError('requires the original forty frozen inputs')
    if sample_ids is None:return inputs
    selected=list(sample_ids)
    if not selected or len(set(selected))!=len(selected):raise ValueError('select unique, nonempty sample IDs')
    if set(selected)-{s['sample_id'] for s in inputs}:raise ValueError('unknown original sample ID')
    return [s for s in inputs if s['sample_id'] in set(selected)]


def prepare(output,source,sample_ids=None):
    output=Path(output).resolve();source=Path(source).resolve()
    inputs=select_inputs(read(source/'model_inputs.json'),sample_ids)
    population=len(inputs)
    staging=output.with_name(output.name+'_inputs')
    if output.exists() or staging.exists() or not output.is_relative_to(ROOT/'outputs'):
        raise ValueError('select a new output directory under outputs')
    staging.mkdir();repository=ReviewRepository(staging/'source.sqlite3');repository.initialize()
    runs=set();bindings=[]
    for s in inputs:
        c=s['candidate'];binding=s['reference_binding']
        if sha256_json(c)!=binding['candidate_content_hash']:raise ValueError('original candidate changed')
        index=load_document_index(s['document_index_path'],c['source_index_hash'],require_verified=True)
        from evidence.verification import verify_evidence_bundle
        if verify_evidence_bundle(s['initial_evidence_bundle'],index,c)['evidence_hash']!=binding['evidence_hash']:
            raise ValueError('original initial evidence changed')
        release=load_release(c['ontology_release_path'])
        if c['run_id'] not in runs:
            repository.create_run({'run_id':c['run_id'],'mode':'shadow','document_path':s['document_index_path'],
                'source_index_hash':c['source_index_hash'],'ontology_path':str(release.snapshot_path),
                'ontology_hash':c['ontology_version'],'candidate_contract_version':'extraction_candidate_v4'})
            runs.add(c['run_id'])
        repository.add_candidate(deepcopy(c))
        bindings.append({'sample_id':s['sample_id'],'candidate_id':c['candidate_id'],'revision':c['revision'],
            'candidate_content_hash':sha256_json(c)})
    data=staging/'population.json'
    save(staging,data.name,{'source_database':str(repository.database_path),'bindings':bindings,
        'selected_raw_candidates':[s['candidate'] for s in inputs]})
    frozen=prepare_resolution(output,limit=population,data_path=data,profile='handoff_revision')
    save(output,'model_inputs.json',inputs)
    save(output,'gpt_reference.json',read(source/'gpt_reference.json'))
    limits={**LIMITS,'llm_calls':min(LIMITS['llm_calls'],population*frozen['limits']['calls_per_root_case'])}
    protocol={'version':'gpt40_handoff_revision_rerun_v2','source_directory':str(source),
        'population':population,'sample_ids':[s['sample_id'] for s in inputs],
        'selection_policy':'explicit user-requested original sample IDs' if sample_ids is not None else 'all original samples',
        'partition':'development','limits':limits,'initial_evidence':'exact original GPT-bound packet',
        'unknown_policy':'manual handoff for affected candidate; continue other candidates; no automatic resend',
        'reference_policy':'GPT reference used only on original initial ballots; never inherited by revised versions',
        'production_written':False,'zero_automatic_retries':True}
    save(output,'rerun_protocol.json',protocol)
    for path in [source/'model_inputs.json',source/'gpt_reference.json',output/'model_inputs.json',
                 output/'gpt_reference.json',output/'rerun_protocol.json']:
        frozen['assets'][str(path)]=digest(path)
    frozen['freeze_hash']=sha256_json({k:v for k,v in frozen.items() if k!='freeze_hash'})
    save(output,'experiment.json',frozen)
    return {'prepared':True,'population':population,'limits':limits,'model_calls':0,'freeze_hash':frozen['freeze_hash']}


def verify(output):
    output=Path(output).resolve();frozen,bindings=verify_resolution(output)
    protocol=read(output/'rerun_protocol.json')
    if protocol['version'] not in {'gpt40_handoff_revision_rerun_v1','gpt40_handoff_revision_rerun_v2'}:raise ValueError('wrong rerun contract')
    inputs=read(output/'model_inputs.json')
    if len(inputs)!=frozen['population'] or protocol['population']!=len(inputs):raise ValueError('rerun population mismatch')
    if protocol.get('sample_ids',[s['sample_id'] for s in inputs])!=[s['sample_id'] for s in inputs]:raise ValueError('selected sample IDs changed')
    return frozen,bindings


def report(output):
    output=Path(output).resolve();resolution=seal(output)
    inputs=read(output/'model_inputs.json');reference=read(output/'gpt_reference.json')
    repository=ReviewRepository(output/'review.sqlite3');initial=[]
    for sample in inputs:
        c=sample['candidate']
        with repository.connect() as connection:
            row=connection.execute('SELECT audit_id FROM audit_runs WHERE candidate_id=? AND revision=? ORDER BY created_at LIMIT 1',
                (c['candidate_id'],c['revision'])).fetchone()
        if not row:continue
        audit=repository.get_audit(c['candidate_id'],row[0])
        if not audit['rounds']:continue
        p=audit['rounds'][0]
        if sha256_json(p['candidate'])!=sample['reference_binding']['candidate_content_hash'] or p['evidence_bundle']['evidence_hash']!=sample['reference_binding']['evidence_hash']:
            raise ValueError('initial review departed from the exact original reference packet')
        votes=[v for v in audit['votes'] if v['proposal_digest']==p['proposal_digest']]
        initial.append({'candidate_id':c['candidate_id'],'revision':c['revision'],'candidate_content_hash':sha256_json(c),
            'binding':sample['reference_binding'],'axis_labels':labels_from_votes(votes)})
    save(output,'initial_reference_comparison.json',{'scope':'original initial ballots only; no labels transferred to revisions',
        'label_source':'gpt_preannotation','human_gold_count':0,'evaluated':len(initial),
        'metrics':compare(initial,reference),'predictions':initial})
    roots={}
    for c in resolution['cases']:
        decision='accept' if c.get('recommendation')=='admit' and c.get('semantic_status')=='completed' else 'reject' if c.get('recommendation')=='exclude' and c.get('semantic_status')=='completed' else 'manual'
        if c.get('execution_outcome_unknown'):decision='manual'
        roots[c['root_candidate_id']]={'decision':decision,'recommendation':c.get('recommendation'),
            'technical_incomplete':c.get('technical_incomplete',True),'required_human_action':c.get('required_human_action')}
    summary={'population':len(inputs),'outcomes':dict(Counter(v['decision'] for v in roots.values())),
        'technical_incomplete':resolution['technical_incomplete'],'applied_revisions':resolution['applied_root_revision_operations'],
        'costs':resolution['costs'],'roots':roots,'reference_labels_transferred_to_revisions':False,'production_written':False}
    save(output,'rerun_summary.json',summary)
    return summary


def run(output):
    output=Path(output).resolve();frozen,bindings=verify(output)
    limits=read(output/'rerun_protocol.json')['limits']
    with (output/'started.json').open('x',encoding='utf-8') as stream:
        json.dump({'freeze_hash':frozen['freeze_hash'],'started_at_epoch':time.time(),'limits':limits},stream)
    with ZipFile(output/'execution_code_snapshot.zip','x',ZIP_DEFLATED) as archive:
        for name,expected in read(output/'code_fingerprints.json').items():
            if digest(ROOT/name)!=expected:raise ValueError('execution source changed before snapshot')
            archive.write(ROOT/name,name)
    inputs=read(output/'model_inputs.json');by_id={s['candidate']['candidate_id']:s for s in inputs}
    repository=ReviewRepository(output/'review.sqlite3');config=configuration(profile='handoff_revision',directory=output)
    fence=Fence(limits);fence.register(repository.database_path);fence.stop_file=output/'STOP'
    completed=[]
    def flush(current=None):
        save(output,'progress.json',{'status':'running','population':len(inputs),'processed':len(completed),
            'current':current,'completed':completed,'usage':fence.snapshot(),'updated_at_epoch':time.time()})
    def batch(repository,run_id,cfg,*,candidate_ids,status_file=None,job_id=None,job_binding=None,on_progress=None):
        rows=[]
        for cid in candidate_ids:
            c=repository.get_revision(cid);sample=by_id[cid]
            index=load_document_index(sample['document_index_path'],c['source_index_hash'],require_verified=True)
            release=load_release(c['ontology_release_path']);voters,planner=configure_clients(cfg,fence)
            repository.job_lease_binding=job_binding
            context={'job_id':job_id} if job_id else {}
            if c['revision']==sample['candidate']['revision']:context['initial_evidence_bundle']=sample['initial_evidence_bundle']
            try:
                value=ReviewOrchestrator(repository,OntologyGuard(load_snapshot(release.snapshot_path)),voters,
                    config=cfg,planner_client=planner).audit_candidate(cid,c['revision'],index,context=context)
                rows.append(value)
            finally:repository.job_lease_binding=None
        result={'failed':0,'completed':len(rows),'results':rows}
        if status_file:save(Path(status_file).parent,Path(status_file).name,result)
        if on_progress:on_progress(result)
        return result
    for sample in inputs:
        c=sample['candidate'];flush(sample['sample_id'])
        if fence.snapshot()['stop_reason']:break
        try:
            result=batch(repository,c['run_id'],config,candidate_ids=[c['candidate_id']])
            drain_reaudits(repository,c['run_id'],config,output,max_jobs=8,
                root_candidate_ids={c['candidate_id']},batch_runner=batch)
            save(output,sample['sample_id']+'_result.json',result)
            completed.append({'sample_id':sample['sample_id'],'candidate_id':c['candidate_id'],'status':'processed'})
        except Exception as exc:
            completed.append({'sample_id':sample['sample_id'],'candidate_id':c['candidate_id'],
                'status':'human_handoff','error':type(exc).__name__+': '+str(exc)})
        flush()
        print(json.dumps({'processed':len(completed),'sample_id':sample['sample_id'],
            'usage':{k:v for k,v in fence.snapshot().items() if k!='requests'}},ensure_ascii=False),flush=True)
    verify(output)
    summary=report(output)
    save(output,'status.json',{'status':'processing_completed','processed':len(completed),'population':len(inputs),
        'usage':fence.snapshot(),'outcomes':summary['outcomes'],'production_written':False})
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','verify','run','report']);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source',type=Path,default=ROOT/'outputs/gpt40_five_group_20261119_frozen')
    parser.add_argument('--sample-ids',nargs='+',help='Explicit original sample IDs, for prepare only')
    args=parser.parse_args()
    if args.sample_ids and args.action!='prepare':parser.error('--sample-ids is frozen during prepare only')
    result=prepare(args.output,args.source,args.sample_ids) if args.action=='prepare' else run(args.output) if args.action=='run' else report(args.output) if args.action=='report' else {'verified':True,'freeze_hash':verify(args.output)[0]['freeze_hash']}
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
