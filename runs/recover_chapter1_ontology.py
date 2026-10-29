"""Retry only failed chapter chunks; preserve the first run unchanged."""
import concurrent.futures
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_chapter1_ontology as generation
from ontologyBuild import pipeline
from ontologyBuild.ontology_candidate import normalize_extraction_result
from ontologyBuild.document_preprocessor import preprocess_markdown, write_preprocess_artifacts
from ontologyBuild.candidate_validator import validate_candidate_batch, write_validation_artifacts
from ontologyBuild.ontology_normalizer import normalize_candidate_data

ROOT = generation.ROOT
ORIGINAL = ROOT/'runs/ontology/20261027_173531_405f6f75'
RUN = ORIGINAL.parent/'20261027_chapter1_recovery'


def read(path): return json.loads(path.read_text(encoding='utf-8'))
def write(path, value): generation.write(path, value)


def main():
    from openai import OpenAI
    import httpx
    RUN.mkdir(exist_ok=True)
    checkpoint = RUN/'recovery_checkpoint.json'
    recovered = read(checkpoint) if checkpoint.exists() else {}
    manifest = read(ORIGINAL/'manifest.json')
    statuses = read(ORIGINAL/'raw_responses/chunk_status.json')
    data = preprocess_markdown(Path(manifest['markdown_path']), chunk_size=5000, chunk_overlap=180)
    chunks = {c['chunk_id']:c for c in data['included_chunks']}
    failed = [s['chunk_id'] for s in statuses if s['status']=='failed']
    settings = generation.configured_planner(generation.load_config())
    raw_client = OpenAI(api_key=settings['api_key'], base_url=settings['base_url'], timeout=90, max_retries=0,
        http_client=httpx.Client(trust_env=False, verify=settings.get('verify_ssl', True)))
    client = generation.ScopedCompletions(raw_client)
    client.journal = generation.INPUT/'recovery_model_requests.jsonl'
    if client.journal.exists():
        client.count=max((r.get('request_no',0) for r in [json.loads(line) for line in client.journal.read_text(encoding='utf-8').splitlines()]),default=0)
    template = {'candidate_classes':[{'name':'原文概念名','normalized_name':'原文概念名',
        'parent_candidate':None,'definition_candidate':'简短抽象定义','status':'new_candidate',
        'matched_existing_class':None,'evidence':[{'chunk_id':'当前ID','text':'原文逐字短引文'}],
        'confidence':0.8,'reason':'原文明示概念'}],
        'candidate_object_properties':[], 'candidate_datatype_properties':[], 'candidate_constraints':[],
        'ontology_mapping':[], 'uncertain_candidates':[]}
    instruction = ('从用户给定片段提取非操作性的教学概念和描述性关系，基础本体为空。只使用当前片段，'
        '不依据标题推断父类。无概念可返回六个空数组。输出严格JSON；六个键必须都有。'
        '每项必须有原文逐字证据、正确的chunk_id和置信度。定义简短，无公式和操作步骤。'
        '对象属性若有，必须包含name,normalized_name,definition_candidate,status(new_candidate),'
        'matched_existing_property(null),domain_candidate,range_candidate,evidence,confidence,reason。'
        '不输出数值数据属性。结构示例：'+json.dumps(template, ensure_ascii=False))

    def retry(chunk_id):
        try:
            response = client.create(model=settings['model'], temperature=0, max_tokens=4096,
                extra_body={'chat_template_kwargs':{'enable_thinking':False}},
                response_format={'type':'json_object'}, messages=[{'role':'system','content':instruction},
                {'role':'user','content':'chunk_id: '+chunk_id+'\nchunk_text:\n'+chunks[chunk_id]['content']+'\n/no_think'}])
            result = normalize_extraction_result(json.loads(response.choices[0].message.content), chunks[chunk_id])
            count = sum(len(result[k]) for k in pipeline.RESULT_FIELDS)
            return chunk_id, {'status':'succeeded' if count else 'succeeded_empty','candidate_count':count,'result':result}
        except Exception as exc:
            return chunk_id, {'status':'failed','error_type':type(exc).__name__}

    try:
        pending = [c for c in failed if recovered.get(c,{}).get('status') not in {'succeeded','succeeded_empty'}]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            for cid, result in executor.map(retry, pending):
                recovered[cid] = result
                write(checkpoint, recovered)
                print(json.dumps({'chunk_id':cid,'status':result['status'],'candidates':result.get('candidate_count')},ensure_ascii=False),flush=True)
    finally:
        raw_client.close()
    raw = pipeline.merge_candidate_batches([read(ORIGINAL/'raw_candidates.json')]+[
        r['result'] for r in recovered.values() if r.get('result') is not None])
    new_statuses = []
    for original in statuses:
        row = copy.deepcopy(original)
        result = recovered.get(row['chunk_id'])
        if result and result['status'] in {'succeeded','succeeded_empty'}:
            row = {'chunk_id':row['chunk_id'],'status':result['status'],'candidate_count':result['candidate_count'],
                'recovered_from':str(ORIGINAL),'original_failure_retained':True}
            if result.get('extraction_method'): row['extraction_method']=result['extraction_method']
        new_statuses.append(row)
    stats = {'total_chunks':len(statuses),'successful_chunks':sum(s['status'] in {'succeeded','succeeded_empty'} for s in new_statuses),
        'failed_chunks':sum(s['status']=='failed' for s in new_statuses),
        'legal_empty_chunks':sum(s['status']=='succeeded_empty' for s in new_statuses)}
    for name in ('bootstrap_base.json','ontology_snapshot.json','base_audit.json'):
        shutil.copy2(ORIGINAL/name,RUN/name)
    write_preprocess_artifacts(data,RUN)
    write(RUN/'raw_candidates.json',raw)
    (RUN/'raw_responses').mkdir(exist_ok=True)
    write(RUN/'raw_responses/chunk_status.json',new_statuses)
    validation = validate_candidate_batch(raw,list(chunks.values()),ontology_path=RUN/'bootstrap_base.json')
    write_validation_artifacts(validation,RUN)
    run_id = RUN.name
    validated = pipeline.attach_run_metadata(validation['validated_candidates'],chunks_by_id=chunks,
        module_index=data['module_index'],run_id=run_id)
    write(RUN/'validated_candidates.json',validated)
    normalized = normalize_candidate_data(read(RUN/'bootstrap_base.json'),validated,data['module_index'],
        corpus_role='build',allow_new_concepts=False)
    for name,key in (('normalized_candidates.json','candidates'),('ontology_modules.json','modules'),
        ('topic_schema_mapping.json','topic_mapping'),('ontology_preview.json','ontology_preview')):
        write(RUN/name,normalized[key])
    review = pipeline.build_review_result(normalized['candidates'],review_mode='manual',reviewer='system',run_id=run_id)
    write(RUN/'review_result.json',review)
    manifest.update(run_id=run_id,status=pipeline.run_status_from_stats(stats,sum(len(raw[k]) for k in pipeline.RESULT_FIELDS)),
        parent_pipeline_run_id=ORIGINAL.name,extraction_stats=stats,
        candidate_counts={k:len(v) for k,v in normalized['candidates'].items() if isinstance(v,list)},
        finished_at=pipeline.now_iso(),outputs={'run_dir':str(RUN)},
        extraction_recovery={'retried_chunks':failed,'recovered_chunks':sum(r['status']!='failed' for r in recovered.values()),
            'journal':str(client.journal),'prompt_sha256':hashlib.sha256(instruction.encode()).hexdigest(),
            'model':settings['model'],'tls_verify':settings.get('verify_ssl',True)})
    manifest['stage_status'].update(candidate_extraction=manifest['status'],candidate_validation='passed' if validation['passed'] else 'partial')
    manifest['config']['tls_verify']=settings.get('verify_ssl',True)
    assistant_chunks=[r['chunk_id'] for r in new_statuses if r.get('extraction_method')=='current_session_assistant_concept_extraction']
    manifest['source_generation_methods']={'qwen_successful_chunks':stats['successful_chunks']-len(assistant_chunks),
        'assistant_concept_supplement_chunks':len(assistant_chunks),'assistant_chunk_ids':assistant_chunks,
        'scope':'concept names only for assistant supplement; no inferred definitions or relations'}
    manifest.pop('execution_note',None)
    write(RUN/'manifest.json',manifest)
    generation.build_draft(manifest)
    pipeline.write_compatibility_outputs(normalized=normalized,raw_candidates=raw,review_result=review)
    write(pipeline.CURRENT_POINTER_PATH,{'run_id':run_id,'run_dir':str(RUN),'manifest':str(RUN/'manifest.json')})
    print(json.dumps({'status':manifest['status'],'coverage':stats},ensure_ascii=False),flush=True)


if __name__ == '__main__': main()
