"""Prepare a reviewable freeze, then execute it once without re-reviewing the smoke subset."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from document.hierarchy_index import load_document_index
from evaluation.review_samples import ReadOnlyRepository
from ontologyBuild.release_service import load_release
from review_agent.batch import run_review_batch
from review_agent.contracts import sha256_json
from review_agent.progress import write_status_snapshot
from review_agent.repository import ReviewRepository
from review_agent.shadow_revision import copy_revision_ancestry
from runs.regress_v8 import ROOT, DATA, SMOKE, configuration, public_settings, fingerprints, summarize
from runs.revalidate_v8 import digest

VERSION='frozen_development_regression_v2'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(directory,name,value):
    write_status_snapshot(directory/name,value)


def validated_population(data):
    bindings=data['bindings'];candidates=data['selected_raw_candidates']
    if not bindings or len(bindings)!=len(candidates):
        raise ValueError('population and bindings must be nonempty and have equal length')
    seen_samples=set();seen_candidates=set()
    for binding,candidate in zip(bindings,candidates):
        identity=(candidate['candidate_id'],candidate['revision'])
        if (binding['sample_id'] in seen_samples or candidate['candidate_id'] in seen_candidates or
            identity!=(binding['candidate_id'],binding['revision']) or sha256_json(candidate)!=binding['candidate_content_hash']):
            raise ValueError('duplicate, misordered or changed candidate binding')
        seen_samples.add(binding['sample_id']);seen_candidates.add(candidate['candidate_id'])
    return list(zip(bindings,candidates))


def settings_for(directory):
    config=configuration()
    config.update(database_path=str(directory/'review.sqlite3'),request_cache_namespace=directory.name)
    config['budget'].update(max_total_tokens=360000,total_timeout_seconds=420,max_llm_calls_per_candidate=18,
        max_planner_steps=3,max_validation_rounds=1,max_tool_calls=8,max_parallel_candidates=2)
    config['case_budget'].update(tokens=400000)
    return config


def prepare(directory,data_path=DATA):
    directory=Path(directory).resolve();data_path=Path(data_path).resolve()
    if not directory.is_relative_to(ROOT/'outputs') or directory.exists():
        raise ValueError('prepare requires a new separate directory under outputs')
    data=read(data_path);pairs=validated_population(data)
    if len(pairs)!=30 or not set(SMOKE)<={b['sample_id'] for b,_ in pairs}:
        raise ValueError('the fixed 30-case development population is required')
    source=ReadOnlyRepository(data['source_database'])
    original=source.get_run(pairs[0][1]['run_id'])
    indexes={};releases={};assets={str(data_path):digest(data_path)}
    for binding,candidate in pairs:
        if candidate['run_id']!=original['run_id']:
            raise ValueError('population run differs from the frozen source')
        if sha256_json(source.get_revision(candidate['candidate_id'],candidate['revision']))!=binding['candidate_content_hash']:
            raise ValueError('source candidate differs from development binding')
        index=load_document_index(original['document_path'],candidate['source_index_hash'],require_verified=True)
        if index['source_snapshot']['canonical_text']!=data['source_canonical_text']:
            raise ValueError('source document differs from frozen population')
        indexes[original['document_path']]=index
        release_path=candidate['ontology_release_path']
        if release_path not in releases:releases[release_path]=load_release(release_path)
        release=releases[release_path]
        if any(candidate.get(k)!=v for k,v in release.binding().items() if k!='ontology_release_path'):
            raise ValueError('candidate does not bind the approved ontology release')
    for path in indexes:assets[str(Path(path).resolve())]=digest(Path(path))
    for release in releases.values():
        for name in ('release_manifest.json',*release.manifest['artifacts']):
            path=release.path/name;assets[str(path)]=digest(path)
    # Freeze comparison evidence and prior labels as files, never feed them to a model.
    historical=[data_path,ROOT/'outputs/dev30_20261031/第一章30条开发样本_人工填写.xlsx',
        ROOT/'outputs/dev30_v8_full_20261111/review.sqlite3',ROOT/'outputs/dev30_v8_full_20261111/smoke_report.json']
    protected={str(path.relative_to(ROOT)):digest(path) for path in historical}
    production=ReadOnlyRepository(ROOT/'runs/v3/review.sqlite3')
    production_hashes={b['candidate_id']:sha256_json(production.get_revision(b['candidate_id'])) for b,_ in pairs}
    if any(production_hashes[b['candidate_id']]!=b['candidate_content_hash'] for b,_ in pairs):
        raise ValueError('production revision changed; do not silently replace frozen samples')
    config=settings_for(directory);settings=public_settings(config)
    directory.mkdir(parents=True)
    repo=ReviewRepository(directory/'review.sqlite3');repo.initialize()
    repo.create_run({**deepcopy(original),'mode':'shadow','experiment_id':directory.name})
    for _,candidate in pairs:repo.add_candidate(deepcopy(candidate))
    copy_revision_ancestry(source,repo,[c for _,c in pairs])
    with repo.connect() as connection:
        ancestry=[r[0] for r in connection.execute("SELECT draft_id FROM revision_drafts WHERE status='applied'")]
    code=fingerprints()
    frozen={'version':VERSION,'partition':'development','prepared_at':datetime.now(timezone.utc).isoformat(),
        'population':len(pairs),'candidate_hashes':[b['candidate_content_hash'] for b,_ in pairs],
        'sample_bindings_hash':sha256_json(data['bindings']),'settings_hash':sha256_json(settings),
        'code_hash':sha256_json(code),'assets':assets,'smoke_sample_ids':list(SMOKE),
        'execution_policy':'7 initial cases, then only the remaining 23; one ballot per stage, no repeated smoke cases',
        'limits':{'llm_calls_per_candidate':18,'llm_calls_total_upper_bound':540,
            'tokens_per_case_stop_threshold':400000,'candidate_deadline_seconds':420,
            'extra_service_probes':0,'relaunches':0},
        'quality_evaluable':False,'human_labels_in_prompt':False,'production_written':False}
    frozen['freeze_hash']=sha256_json(frozen)
    for name,value in [('sample_bindings.json',data['bindings']),('settings.json',settings),('code_fingerprints.json',code),
        ('ancestry.json',{'historical_applied_draft_ids':ancestry,'old_review_votes_imported':False}),
        ('preserved_inputs.json',{'sha256':protected,'production_candidate_hashes':production_hashes}),
        ('experiment.json',frozen),('status.json',{'status':'prepared','new_model_calls':0,'freeze_hash':frozen['freeze_hash']})]:
        save(directory,name,value)
    return frozen


def verify(directory):
    frozen=read(directory/'experiment.json')
    if frozen.get('version')!=VERSION or frozen.get('freeze_hash')!=sha256_json({k:v for k,v in frozen.items() if k!='freeze_hash'}):
        raise ValueError('unsupported or changed freeze manifest')
    if read(directory/'code_fingerprints.json')!=fingerprints() or frozen['code_hash']!=sha256_json(fingerprints()):
        raise ValueError('code changed since freeze')
    if frozen['settings_hash']!=sha256_json(public_settings(settings_for(directory))) or frozen['settings_hash']!=sha256_json(read(directory/'settings.json')):
        raise ValueError('model, prompt, budget or policy settings changed since freeze')
    if any(digest(Path(path))!=expected for path,expected in frozen['assets'].items()):
        raise ValueError('source or approved ontology artifact changed since freeze')
    bindings=read(directory/'sample_bindings.json')
    if frozen['sample_bindings_hash']!=sha256_json(bindings):raise ValueError('sample bindings changed')
    repo=ReadOnlyRepository(directory/'review.sqlite3')
    if any(sha256_json(repo.get_revision(b['candidate_id']))!=b['candidate_content_hash'] for b in bindings):
        raise ValueError('candidate revision changed since freeze')
    protected=read(directory/'preserved_inputs.json')
    if any(digest(ROOT/path)!=h for path,h in protected['sha256'].items()):raise ValueError('historical baseline changed')
    return frozen,bindings


def execute(directory):
    directory=Path(directory).resolve();frozen,bindings=verify(directory)
    # Atomic creation fences even simultaneous launches, and an interrupted launch
    # cannot be reopened automatically by preparing or executing again.
    with (directory/'started.json').open('x',encoding='utf-8') as stream:
        json.dump({'freeze_hash':frozen['freeze_hash'],'started_at':datetime.now(timezone.utc).isoformat()},stream)
    repo=ReviewRepository(directory/'review.sqlite3');config=settings_for(directory)
    initial=[b for b in bindings if b['sample_id'] in frozen['smoke_sample_ids']]
    remaining=[b for b in bindings if b['sample_id'] not in frozen['smoke_sample_ids']]
    try:
        for stage,selected in (('initial',initial),('remaining',remaining)):
            verify(directory)
            save(directory,'status.json',{'status':'running','stage':stage,'candidate_count':len(selected)})
            run_id=repo.get_revision(selected[0]['candidate_id'])['run_id']
            batch=run_review_batch(repo,run_id,config,candidate_ids=[b['candidate_id'] for b in selected],
                status_file=directory/'progress.json')
            save(directory,'batch_'+stage+'.json',batch)
            report=summarize(repo,selected,directory)
            # Waiting reconciles original receipts only; never reruns a seat.
            deadline=time.monotonic()+180
            while report['unresolved_attempts'] and time.monotonic()<deadline:
                time.sleep(5)
                report=summarize(repo,selected,directory)
            save(directory,'engineering_gate_'+stage+'.json',report)
            if not report['engineering_gate_passed']:
                save(directory,'status.json',{'status':'stopped','stage':stage,'reason':'engineering_gate_failed',
                    'requests_settled':report['requests_settled'],'automatic_relaunch':False})
                break
        else:
            save(directory,'status.json',{'status':'completed','automatic_relaunch':False})
    except BaseException as exc:
        save(directory,'status.json',{'status':'stopped','reason':type(exc).__name__,'automatic_relaunch':False})
        raise
    finally:
        verify(directory)
        summarize(repo,bindings,directory)
    return read(directory/'status.json')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','verify','run']);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();directory=args.output.resolve()
    if args.action=='prepare':result=prepare(directory)
    elif args.action=='verify':result={'verified':True,'freeze_hash':verify(directory)[0]['freeze_hash']}
    else:result=execute(directory)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
