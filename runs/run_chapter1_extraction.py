"""Run the current v4 extractor against the explicitly published chapter draft."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(Path(__file__).resolve().parent))
from generate_chapter1_ontology import SCOPE
from chapter1_recovery_support import atomic_json, progress_json, SourceResponseReplay


def write(path,data):
    atomic_json(path,data)


def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')


def prepare(job):
    from document.snapshot import read_snapshot
    from document.hierarchy_index import split_markdown_text
    from ontologyBuild.release_service import load_release
    generation=ROOT/'runs/ontology/20261027_chapter1_recovery'
    published=json.loads((generation/'publication_receipt.json').read_text(encoding='utf-8'))
    release=load_release(published['ontology_release_path'])
    source=json.loads((generation/'chapter1_generation_report.json').read_text(encoding='utf-8'))['chapter_source']
    input_path=Path(source['excerpt_path'])
    snapshot=read_snapshot(input_path)
    assert hashlib.sha256(snapshot['canonical_text'].encode()).hexdigest()==source['excerpt_sha256']
    chunks=split_markdown_text(snapshot['canonical_text'],document_id=snapshot['document_id'],chunk_size=600,chunk_overlap=80,snapshot=snapshot)
    config=json.loads((ROOT/'config/review_agent.json').read_text(encoding='utf-8'))
    config=copy.deepcopy(config)
    config['mode']='shadow'
    config['routing']={**config.get('routing',{}),'auto_accept_enabled':False,'semantic_auto_reject_enabled':False}
    config['extraction']={**config.get('extraction',{}),'request_attempts':2,'max_tokens':4096,
        'max_model_calls':len(chunks)*4+32,'max_split_depth':2,'min_scope_chars':120,
        'filter_policy':{'exercise_sections':'defer','modeling_scope':'non-operational concepts, definitions, taxonomy and descriptive relations',
            'excluded_content':['equations','numeric data attributes','calculation or solution procedures','firing or aiming corrections','weapon design or optimization rules']}}
    prompt=(ROOT/'prompts/extraction_v4.txt').read_text(encoding='utf-8')
    scope=SCOPE.replace('candidate_datatype_properties 必须为空。','数值 literal 与数据属性不属于本次范围。')
    scope=scope.replace('输出严格沿用上面的六个数组及候选格式，不另加文字。','')
    prompt+='\n\n【本次任务的来源范围与优先政策】\n'+scope
    prompt+='\n本次输出仍严格遵守上文的 triplets 和 coverage_status 契约，不使用本体候选六数组格式。'
    prompt+='\n只提取本政策允许的概念事实；coverage_status 表示该政策内的覆盖，不代表原文中公式或操作内容已抽取。\n/no_think\n'
    job.mkdir(parents=True,exist_ok=False)
    write(job/'review_config.json',config)
    (job/'extraction_prompt.txt').write_text(prompt,encoding='utf-8')
    state={'job_id':job.name,'run_id':'run_'+job.name,'status':'prepared','created_at':now(),
        'input':str(input_path),'chapter_source':source,'source_scope_count':len(chunks),
        'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'review_mode':'shadow',
        'attempts_started':0,'attempts_finished':0,'successful_model_calls':0,'failed_model_calls':0,
        'model_call_budget':config['extraction']['max_model_calls'],**release.binding()}
    write(job/'status.json',state)
    return state


def run(job):
    from review_agent.service import load_config
    from review_agent.model_settings import configured_planner
    state=json.loads((job/'status.json').read_text(encoding='utf-8'))
    if state['status']!='prepared': raise ValueError('job is already started; do not launch duplicate extraction')
    settings=configured_planner(load_config())
    authorization=state.get('destination_authorization') or {}
    if authorization.get('source')!='explicit_user_reply':
        raise ValueError('this job requires the recorded explicit destination authorization')
    if authorization.get('model_endpoint','').rstrip('/')!=settings['base_url'].rstrip('/'):
        raise ValueError('model endpoint differs from the user-authorized destination')
    if authorization.get('tls_verify') is not settings.get('verify_ssl'):
        raise ValueError('TLS settings differ from the user-authorized connection')
    os.environ.update(OPENAI_API_KEY=settings['api_key'],OPENAI_BASE_URL=settings['base_url'],
        OPENAI_MODEL=settings['model'],TRIPLET_MODEL=settings['model'],KG_RUN_ID=state['run_id'],
        KG_REVIEW_MODE='shadow',KG_LLM_TIMEOUT_SECONDS='90',
        KG_LLM_VERIFY_SSL='false' if settings.get('verify_ssl') is False else 'true')
    from extract_triplets import extrct as bridge
    from extract_triplets import pipeline_v4 as extraction
    extraction.PROMPT_PATH=job/'extraction_prompt.txt'
    args=['--input',state['input'],'--output-stem','火炮弹道学_钱林方_第一章_'+job.name,
        '--config',str(job/'review_config.json'),'--review-mode','shadow',
        '--ontology-release',state['ontology_release_path']]
    bridge.configure_from_cli(bridge.parse_cli_args(args))
    original_client=bridge.client
    replay=None
    if state.get('resume_checkpoint'):
        replay=SourceResponseReplay(state['resume_checkpoint'],prompt=(job/'extraction_prompt.txt').read_text(encoding='utf-8'),
            release_binding=bridge.ONTOLOGY_RELEASE.binding(),model=settings['model'],
            filter_policy=bridge.REVIEW_CONFIG['extraction']['filter_policy'])

    class Completions:
        def create(self,**kwargs):
            response=original_client.chat.completions.create(**kwargs,
                extra_body={'chat_template_kwargs':{'enable_thinking':False}})
            content=response.choices[0].message.content or ''
            # Preserve the original response separately when removing formatting.
            cleaned=content.strip()
            if '</think>' in cleaned: cleaned=cleaned.rsplit('</think>',1)[1].strip()
            if cleaned.startswith('```'): cleaned=re.sub(r'^```(?:json)?\s*|\s*```$','',cleaned,flags=re.I).strip()
            if cleaned!=content:
                raw_dir=job/'response_formatting';raw_dir.mkdir(exist_ok=True)
                key=hashlib.sha256(content.encode()).hexdigest()
                write(raw_dir/(key+'.json'),response.model_dump(mode='json'))
                response.choices[0].message.content=cleaned
            return response

    bridge.client=SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    original_extract=extraction.extract_source_scope
    def traced(scope,**kwargs):
        options=dict(kwargs.get('settings') or {})
        original_observer=options.get('on_attempt')
        def observe(event,record):
            if original_observer: original_observer(event,record)
            if event=='started': state['attempts_started']+=1
            elif event=='cache_replayed':
                state['replayed_model_records']=state.get('replayed_model_records',0)+1
                if record['status']=='completed': state['reused_successful_responses']=state.get('reused_successful_responses',0)+1
            else:
                state['attempts_finished']+=1
                state['successful_model_calls']+=record['status']=='completed'
                state['failed_model_calls']+=record['status']=='failed'
                state['last_attempt_status']=record['status']
            state['last_scope']={k:scope[k] for k in ('source_chunk_id','core_start','core_end')}
            state['updated_at']=now()
            progress_json(job/'status.json',state)
            if event=='finished':
                print(json.dumps({'finished_calls':state['attempts_finished'],'status':record['status'],
                    'chunk_id':scope['source_chunk_id']},ensure_ascii=False),flush=True)
        options['on_attempt']=observe
        kwargs['settings']=options
        if replay:
            recovered=replay.lookup(scope,index=kwargs['index'],model=kwargs['model'],settings=options)
            if recovered is not None:
                for record in recovered['attempts']: observe('cache_replayed',record)
                return recovered
        return original_extract(scope,**kwargs)
    extraction.extract_source_scope=traced
    state.update(status='running',started_at=now(),model=settings['model'],tls_verify=settings.get('verify_ssl'),
        checkpoint=str(ROOT/'runs/v3'/(state['run_id']+'.pending')))
    progress_json(job/'status.json',state)
    try:
        exit_code=extraction.run_v4_extraction(bridge.parse_cli_args(args),bridge)
        final=ROOT/'runs/v3'/state['run_id']
        manifest=json.loads((final/'manifest.json').read_text(encoding='utf-8'))
        assert manifest['ontology_release_id']==state['ontology_release_id']
        state.update(status=manifest['extraction_status'],exit_code=exit_code,finished_at=now(),
            candidate_count=manifest['extraction_candidate_count'],final_run_path=str(final))
        progress_json(job/'status.json',state)
        return exit_code
    except BaseException as exc:
        state.update(status='failed',finished_at=now(),error_type=type(exc).__name__)
        progress_json(job/'status.json',state)
        raise
    finally: original_client.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--job',required=True)
    parser.add_argument('--prepare',action='store_true')
    args=parser.parse_args()
    job=Path(args.job).resolve()
    if job.parent!=ROOT/'runs/chapter1_extraction': raise ValueError('job must be inside runs/chapter1_extraction')
    if args.prepare:
        state=prepare(job)
        print(json.dumps({k:state[k] for k in ('job_id','run_id','source_scope_count','ontology_release_id','model_call_budget')},ensure_ascii=False,indent=2))
        return 0
    return run(job)


if __name__=='__main__': raise SystemExit(main())
