"""Zero-call, reproducible preparation and cost sizing for the fixed-pool study."""
import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from review_agent.contracts import sha256_json
from .freeze_shared_ontology import ROOT,read,write

VERSION='fixed_candidate_formal_preparation_v1'
SOURCE_PATTERNS=('现代火炮自动机设计理论.md','火炮与自动武器动力学*.md','火炮构造与原理*.md')
EXCLUDE=re.compile(r'炮|枪|武器|弹丸|弹药|装药|膛|射击|击发|后坐|复进|供弹|弹道|爆炸|燃烧|战术|军用|发射|杀伤|穿甲|火药')
CONCEPT=re.compile(r'刚体|柔性体|广义坐标|虚功|虚位移|质点|质心|惯性|阻尼|线性系统|弹性|黏性|键合|通口|能量|热力学|热传导|传热|温度|热容|疲劳|应力|应变|有限元|摩擦|振动|质量守恒|运动学|力学|流体|连续介质')


def prescreen(text):
    """Text-only, pre-extraction selection; never use judgments or candidate scores."""
    accepted=[];rejected=[];heading=''
    for match in re.finditer(r'\S[\s\S]*?(?=\n\s*\n|\Z)',text):
        value=match.group().rstrip();reason=None
        if value.startswith('#'):
            heading=value.splitlines()[0];continue
        if not 60<=len(value)<=1800:reason='paragraph_length'
        elif value.startswith(('!','<','|')):reason='non_prose'
        elif EXCLUDE.search(value):reason='outside_general_concept_scope'
        elif '$' in value or re.search(r'[=＝]|\d{3,}',value):reason='formula_or_numeric_material'
        elif not CONCEPT.search(value):reason='outside_predeclared_concept_terms'
        row={'start':match.start(),'end':match.start()+len(value),'section':heading,
             'text_sha256':hashlib.sha256(value.encode()).hexdigest()}
        if reason:rejected.append({**row,'reason':reason})
        else:accepted.append({**row,'text':value})
    return accepted,rejected


def pack_paragraphs(rows,limit=2600):
    """Batch small sections together; exact per-paragraph source markers remain."""
    groups=[];current=[];size=0
    for row in rows:
        if current and size+len(row['text'])>limit:
            groups.append(current);current=[];size=0
        current.append(row);size+=len(row['text'])
    if current:groups.append(current)
    return groups


def historical_usage():
    grouped=defaultdict(list);latency=defaultdict(list)
    for name in ('technical_smoke_20261118','technical_smoke_20261118_v2',
                 'technical_smoke_20261118_v3','technical_smoke_20261118_planner_fix'):
        path=ROOT/'outputs'/name/'review.sqlite3'
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as connection:
            for meta,raw in connection.execute('SELECT metadata_json,result_json FROM request_attempts'):
                result=json.loads(raw or '{}');metadata=json.loads(meta or '{}')
                task=metadata.get('task_type','unknown')
                if all(type(result.get(k)) is int for k in ('input_tokens','output_tokens')):
                    grouped[task].append(sum(result[k] for k in ('input_tokens','output_tokens')))
                if type(result.get('latency_ms')) in (int,float):latency[task].append(result['latency_ms'])
    return {k:{'calls':len(v),'known_tokens':sum(v),'mean_tokens':sum(v)/len(v),'maximum_tokens':max(v),
               'mean_latency_ms':sum(latency[k])/len(latency[k]) if latency[k] else None}
            for k,v in grouped.items()}


def budget(target,history):
    reviewer=history['triple_review']['mean_tokens'];planner=history['planner_action']['mean_tokens']
    extraction=read(ROOT/'outputs/development_pool_20261117/intake_summary.json')
    extraction_mean=extraction['known_tokens']/extraction['calls']
    def scenario(focus,planning):
        rows=[]
        for name,ballots,plans in (('b0_all_keep',0,0),('b1_single',1,0),('b2_majority',3,0),
                                  ('b3_fixed_workflow',3+focus,0),('agent_dynamic',3+focus,planning),
                                  ('agent_static',3+focus,planning)):
            rows.append({'group':name,'review_requests':target*ballots,'planner_requests':target*plans,
                         'physical_requests':target*(ballots+plans),
                         'estimated_tokens':round(target*(ballots*reviewer+plans*planner))})
        serial_hours=sum(r['review_requests']*history['triple_review']['mean_latency_ms']+
                         r['planner_requests']*history['planner_action']['mean_latency_ms'] for r in rows)/3600000
        return {'groups':rows,'review_and_planning_requests':sum(r['physical_requests'] for r in rows),
                'review_and_planning_estimated_tokens':sum(r['estimated_tokens'] for r in rows),
                'fully_serial_request_latency_hours':round(serial_hours,2),
                'candidate_extraction_request_allowance':60,'extraction_estimated_tokens':round(60*extraction_mean),
                'total_requests_including_extraction':60+sum(r['physical_requests'] for r in rows),
                'total_estimated_tokens_including_extraction':round(60*extraction_mean)+sum(r['estimated_tokens'] for r in rows)}
    return {'target':target,'historical_task_usage':history,
            'light_scenario_no_focus_two_planner_calls':scenario(0,2),
            'full_action_scenario_three_focus_four_planner_calls':scenario(3,4),
            'assumptions':['No request reuse discount','B1 fixed before test, not selected by held-out accuracy',
                'B3 and both Agent arms share the same per-case limits','No retries or model-based format repair',
                'Focused-review cost uses ordinary-review mean as an unvalidated proxy',
                'Extraction allowance does not guarantee 300 natural candidates',
                'Historical means come from six ordinary and three planner requests, not a representative cost benchmark'],
            'currency_cost':None,'price_status':'gateway/model-specific billed prices not supplied; currency cost cannot be inferred',
            'token_limit_kind':'known-usage dispatch stop; not a guaranteed billed-token or currency ceiling',
            'independent_double_annotation_hours_at_4_minutes':2*target*4/60,
            'annotation_time_status':'illustrative only; excludes adjudication and requires human timing'}


def configuration_overrides():
    return {'mode':'shadow','validator_contract_version':'validator_response_v8',
        'planner':{'enabled':True,'strategy':'llm_react','durable_requests':True,'compact_view':True,
            'state_driven_steps':True,'fixed_candidate_limits':True,'fallback':'manual','max_output_tokens':2048,
            'allowed_tools':['inspect_ontology','run_validation','search_evidence','inspect_context','review_dispute','finish_review']},
        'budget':{'max_llm_calls_per_candidate':10,'max_total_tokens':250000,'max_planner_steps':4,
            'max_validation_rounds':1,'max_tool_calls':12,'max_retries_per_request':0,
            'max_parallel_candidates':1,'max_parallel_validators':1,'total_timeout_seconds':540,
            'per_call_timeout_seconds':180,'max_evidence_items':12,'max_evidence_chars':12000},
        'case_budget':{'llm_calls':10,'tokens':250000,'tool_calls':12,'extension_requests':1,'rebinds':1},
        'response_correction':{'max_corrections_per_seat':0,'max_calls_per_candidate_version':0},
        'semantic_clarification':{'enabled':False},'dispute_review':{'enabled':True},
        'agent_resolution':{'enabled':False},'ontology_extension':{'enabled':False,'auto_publish_enabled':False},
        'memory':{'enabled':False,'scope':{'mode':'disabled'}},
        'routing':{'auto_accept_enabled':False,'semantic_auto_reject_enabled':False},
        'neo4j':{'auto_import_enabled':False},'vote_weighting':{'mode':'dynamic'}}


def merge_overrides(base,overrides):
    result=deepcopy(base)
    for key,value in overrides.items():
        if isinstance(value,dict) and isinstance(result.get(key),dict):result[key]=merge_overrides(result[key],value)
        else:result[key]=deepcopy(value)
    # The old failure policy would force an extra planner retry even with retries=0.
    result.get('planner',{}).pop('failure_policy',None)
    return result


def prepare_extraction_proposals(output,sources,paragraphs,release,*,streaming=False):
    from document.snapshot import make_snapshot
    from document.hierarchy_index import split_markdown_text,build_document_index,persist_document_index
    from extract_triplets.pipeline_v4 import prompt_template,source_scope
    from extract_triplets.candidate_contract import RESPONSE_SCHEMA
    from schema.subontology_retriever import SubOntologyRetriever
    from .development_pool import excerpt
    prompt,_=prompt_template()
    prompt+='\n仅抽取一般力学、热学和系统描述的非操作性概念关系；不提取武器设计、性能改进、公式、数值或计算步骤。主谓宾及限定保持原文含义，引文逐字保留。段落之间可能有省略，不跨省略推断。不给审核结论或人工标签，不设每段候选数量目标。\n'
    (output/'extraction_prompt.txt').write_text(prompt,encoding='utf-8')
    (output/'sources').mkdir();documents=[];by_source=defaultdict(list)
    for source in sources:
        rows=[r for r in paragraphs if r['source_group_id']==source['source_group_id']]
        for ordinal,group in enumerate(pack_paragraphs(rows,limit=900 if streaming else 2600),1):
            docid=source['source_group_id']+('_stream_extract_' if streaming else '_formal_extract_')+str(ordinal).zfill(3)
            text,segments=excerpt(group,docid);path=output/'sources'/(docid+'.md')
            path.write_text(text,encoding='utf-8');snapshot=make_snapshot(text,docid)
            chunks=split_markdown_text(text,document_id=docid,chunk_size=max(12000,len(text)+1),chunk_overlap=0,snapshot=snapshot)
            index=build_document_index(chunks);index_path=output/'sources'/(docid+'_index.json')
            persist_document_index(index,index_path)
            if len(chunks)!=1:raise ValueError('one source scope required per extraction proposal')
            scope=source_scope(index,chunks[0],start=0,end=len(text),neighbors=0)
            scope.update(context_start=0,context_end=len(text))
            documents.append({'document_id':docid,'document_version':index['document_version'],
                'index_path':str(index_path),'path':str(path),'index_hash':index['index_content_hash'],
                'scope':scope,'segments':segments,'source_group_id':source['source_group_id'],
                'parent_path':source['path'],'parent_document_version':source['document_version'],
                'parent_raw_file_hash':source['raw_file_hash'],'language':'zh',
                'source_sections':sorted({r['section'] for r in group}),
                'source_section':' | '.join(dict.fromkeys(r['section'] for r in group)),
                'source_characters':sum(len(r['text']) for r in group),
                'source_character_target':900 if streaming else 2600,
                'oversized_single_paragraph':bool(streaming and len(group)==1 and len(group[0]['text'])>900)})
            proposal={'version':'source_candidate_extraction_v1','task_id':VERSION+':'+docid,
                'document_id':docid,'source_group_id':source['source_group_id'],
                'source_index_hash':index['index_content_hash'],'system_prompt':prompt,
                'payload':{'task_type':'source_candidate_extraction','core_text':text,'context_text':text,
                    'scope':scope,'subontology':SubOntologyRetriever(release.ontology).retrieve(group[0]['section'],text),
                    'ontology_release':release.binding(),'output_schema':RESPONSE_SCHEMA,
                    'instruction':'Natural candidates only; verbatim quotes confined to a single exact retained paragraph.'},
                'schema_hash':sha256_json(RESPONSE_SCHEMA),'max_output_tokens':12288 if streaming else 9000}
            if streaming:proposal['delivery_policy']='small_source_streaming_v2'
            proposal['proposal_digest']=sha256_json(proposal);by_source[source['source_group_id']].append(proposal)
    ordered=[]
    while any(by_source.values()):
        for identity in sorted(by_source):
            if by_source[identity]:ordered.append(by_source[identity].pop(0))
    write(output/'extraction_documents.json',documents);write(output/'extraction_proposals.json',ordered)
    return len(ordered)


def previous_extraction(previous):
    """Exclude every attempted source span, including unknown and truncated work."""
    previous=Path(previous).resolve()
    if not previous.is_relative_to(ROOT/'outputs'):raise ValueError('continuation must use an isolated prior study')
    docs={d['document_id']:d for d in read(previous/'extraction_documents.json')}
    with sqlite3.connect((previous/'extraction.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        attempts=[(state,json.loads(meta),json.loads(raw or '{}')) for state,meta,raw in
                  c.execute('SELECT state,metadata_json,result_json FROM request_attempts')]
    spans=[];sent=[]
    for state,meta,result in attempts:
        if result.get('not_sent'):continue
        doc=docs[meta['proposal']['document_id']];sent.append((state,result))
        spans.extend((doc['parent_document_version'],s['parent_start'],s['parent_end']) for s in doc['segments'])
    limits=read(previous/'extraction_started.json')['limits']
    return {'previous_directory':str(previous),'previous_physical_requests':len(sent),
            'previous_unknown_requests':sum(s in {'running','unknown'} for s,_ in sent),
            'previous_known_tokens':sum(sum(r.get(k) or 0 for k in ('input_tokens','output_tokens')) for _,r in sent),
            'original_request_cap':limits['llm_calls'],'original_known_token_stop':limits['known_tokens'],
            'max_new_requests':max(0,limits['llm_calls']-len(sent)),
            'never_resend_attempted_spans':True},spans


def prepare(output,target=300,*,streaming=False,continue_from=None,gpt_reference=False):
    from document.snapshot import read_snapshot
    from ontologyBuild.release_service import load_release
    from runs.regress_v8 import configuration,public_settings
    output=Path(output).resolve()
    if not output.is_relative_to(ROOT/'outputs') or output.exists():raise ValueError('new output directory required')
    if not 1<=target<=600:raise ValueError('target must be between 1 and 600')
    lineage,attempted_spans=previous_extraction(continue_from) if continue_from else (None,[])
    output.mkdir(parents=True)
    policy={'version':VERSION,'target':target,'source_patterns':SOURCE_PATTERNS,
            'paragraph_selection':'fixed lexical prescreen of general descriptive concepts before extraction',
            'exclusion_regex':EXCLUDE.pattern,'concept_regex':CONCEPT.pattern,
            'candidate_selection':'source/section round-robin independent of labels and review outcomes',
            'extraction_request_cap':60,'extraction_known_token_stop':1200000,
            'extraction_retries':0,'stop_on_unknown':True,'semantic_changes_during_review':False,
            'test_labels_used_for_preparation':False,'source_group_unit':'original book'}
    if streaming:policy.update(version='fixed_candidate_streaming_preparation_v2',source_batch_target_characters=900,
        paragraph_policy='preserve whole paragraphs; oversized single paragraphs explicitly reported',
        delivery_policy='small_source_streaming_v2',max_output_tokens=12288,stream_responses=True,
        progress_persisted=True,request_execution='independent_durable_provider_process')
    if lineage:
        policy.update(budget_lineage=lineage,extraction_request_cap=lineage['max_new_requests'],
            extraction_known_token_stop=max(1,lineage['original_known_token_stop']-lineage['previous_known_tokens']))
    write(output/'sampling_policy.json',policy)
    development=read(ROOT/'outputs/development_pool_20261117/source_manifest.json')
    forbidden={d['parent_document_version'] for d in development['documents']}
    old=read(ROOT/'outputs/dev30_20261031/sample_data.json')
    forbidden.update(c['document_version'] for c in old['selected_raw_candidates'])
    sources=[];paragraphs=[];excluded=[]
    for pattern in SOURCE_PATTERNS:
        matches=sorted((ROOT/'mdfiles').glob(pattern))
        if len(matches)!=1:raise ValueError('source pattern must identify exactly one original book')
        snapshot=read_snapshot(matches[0])
        if snapshot['document_version'] in forbidden:raise ValueError('proposed source was used in checked development data')
        accepted,rejected=prescreen(snapshot['canonical_text'])
        remaining=[]
        for paragraph in accepted:
            if any(version==snapshot['document_version'] and a<paragraph['end'] and paragraph['start']<b
                   for version,a,b in attempted_spans):
                rejected.append({k:v for k,v in paragraph.items() if k!='text'}|
                                {'reason':'already_attempted_source_span_no_resend'})
            else:remaining.append(paragraph)
        accepted=remaining
        identity='book_'+sha256_json([snapshot['document_id'],snapshot['document_version']])[:16]
        sources.append({'source_group_id':identity,'path':str(matches[0]),'document_id':snapshot['document_id'],
            'document_version':snapshot['document_version'],'raw_file_hash':snapshot['raw_file_hash'],
            'eligible_paragraphs':len(accepted),'eligible_characters':sum(len(p['text']) for p in accepted),
            'prior_use_status':'not_in_checked_30_and_160_development_sources; other historical use not exhaustively certified'})
        paragraphs.extend({'source_group_id':identity,**p} for p in accepted)
        excluded.extend({'source_group_id':identity,**p} for p in rejected)
    write(output/'source_manifest.json',{'sources':sources,'source_groups':len(sources),
        'status':'prescreened_sources_not_an_extracted_candidate_pool','candidate_count':0,
        'independence_check_scope':'30-row regression and 160-row development original source versions'})
    write(output/'source_paragraphs.json',paragraphs);write(output/'source_exclusions.json',excluded)
    overrides=configuration_overrides();write(output/'config_overrides.json',overrides)
    config=merge_overrides(configuration(profile='weighted_diagnostic',directory=output),overrides)
    settings=public_settings(config);write(output/'public_settings.json',settings)
    if streaming:
        from .extraction_chain_v2 import extraction_voter,extractor_binding
        write(output/'extractor_settings.json',extractor_binding(extraction_voter(config)))
    groups=[{'id':'b0_all_keep','method':'all_keep','ontology_mode':'fixed'},
            {'id':'b1_single','method':'single','ontology_mode':'fixed','single_voter_id':'judge_c'},
            {'id':'b2_majority','method':'majority','ontology_mode':'fixed'},
            {'id':'b3_fixed_workflow','method':'fixed_workflow','ontology_mode':'fixed',
             'weighting_mode':'dynamic','workflow_policy':'fixed_candidate_workflow_v1'},
            {'id':'agent_dynamic','method':'agent','ontology_mode':'fixed','weighting_mode':'dynamic'},
            {'id':'agent_static','method':'agent','ontology_mode':'fixed','weighting_mode':'static'}]
    release=load_release(read(ROOT/'outputs/ontology_freeze_20261116/live/frozen_release.json')['path'])
    scope_count=prepare_extraction_proposals(output,sources,paragraphs,release,streaming=streaming)
    protocol={'version':VERSION,'status':'prepared_pending_extraction_mapping_and_budget_freeze',
        'target_population':target,'groups':groups,'equal_weight_control':'zero-call replay on the same original opinions',
        'single_model_selection':'judge_c predeclared cold-start baseline; not development-selected best model',
        'reliability_status':'human development labels missing; nonuniform frozen reliability not yet estimable',
        'cold_start_policy':'equal common prior; explicitly report static/equal degeneracy until human history is available',
        'initial_release':release.binding(),'release_human_semantic_certification':False,
        'shared_mapping_preparation':'not_completed; do not misclassify missing IDs as false source facts',
        'review_source_index':'full original-source retrieval indexes and common candidate reanchoring are not frozen yet',
        'per_case_limits':{'llm_calls':10,'tokens':250000,'tool_calls':12,'extension_requests':1,'rebinds':1,'seconds':540},
        'proposed_review_study_limits':{'llm_calls':30*target,'known_tokens':round(220000000*target/300),'seconds':259200},
        'budget_accepted':False,'new_model_calls':0,'human_gold_labels':0,'production_written':False}
    if streaming:protocol.update(version='fixed_candidate_streaming_preparation_v2',extraction_delivery_policy='small_source_streaming_v2')
    if lineage:protocol.update(budget_lineage=lineage,dispatch_blocked_on_prior_unknown=bool(lineage['previous_unknown_requests']))
    if gpt_reference:
        from .gpt_reference import export
        reference=export(output/'gpt_development_reference.json')
        protocol.update(label_reference={'kind':'gpt_preannotation','path':str(output/'gpt_development_reference.json'),
            'data_version':reference['data_version'],'source_sha256':reference['source_sha256'],
            'development_labeled_candidates':len(reference['annotations']),'human_gold_count':0,
            'labels_exposed_to_models':False,'reference_agreement_is_independent_accuracy':False},
            reliability_status='GPT development reference available; matching model judgments still required for reliability counts',
            cold_start_policy='common prior until exact GPT-reference model-comparison history is frozen; no human-label gate for exploratory analysis')
    write(output/'protocol.json',protocol);write(output/'budget_estimate.json',budget(target,historical_usage()))
    files=[p for folder in ('evaluation','review_agent','evidence','document','ontologyBuild','prompts','specs')
           for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.txt','.json'} and '__pycache__' not in p.parts]
    write(output/'code_fingerprints.json',{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)})
    result={'status':protocol['status'],'target':target,'source_groups':len(sources),
        'eligible_paragraphs':len(paragraphs),'eligible_characters':sum(len(p['text']) for p in paragraphs),
        'prepared_extraction_scopes':scope_count,'new_model_calls':0,'candidate_count':0,'output':str(output)}
    write(output/'preparation_report.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    parser.add_argument('--target',type=int,default=300)
    parser.add_argument('--streaming-v2',action='store_true');parser.add_argument('--continue-from')
    parser.add_argument('--gpt-reference',action='store_true');args=parser.parse_args()
    print(json.dumps(prepare(args.output,args.target,streaming=args.streaming_v2,
        continue_from=args.continue_from,gpt_reference=args.gpt_reference),ensure_ascii=False))


if __name__=='__main__':main()
