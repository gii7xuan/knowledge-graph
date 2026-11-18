"""Freeze fresh descriptive source candidates and independent blank pilot annotations."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
import hashlib
import html
import json
from pathlib import Path
import re
import time

from review_agent.contracts import sha256_json
from .freeze_shared_ontology import ROOT,read,write

VERSION='development_pool_20261117_v1'
SEED='development-pilot-20261117-v1'
TARGET=160
PILOT=40
MAX_CALLS=24
TOKEN_STOP=400000
EXCLUDED=re.compile(r'\b(guns?|projectiles?|propellants?|combustion|explosives?|detonation|muzzle|barrel|weapons?|penetration|warhead|ballistics?|launch|ignition|firing|charge|tnt|nob[el]+[ -]abel)\b|火炮|火药|弹丸|弹道|射击|炮管|装药|燃烧|爆炸|复进|后坐|后效|枪|弹药',re.I)
SOURCE_PLAN=[('MinerU_markdown_Ballistics.md',('2.1 ','2.2 ','2.4 ','2.7 '),'en'),
             ('火炮设计理论*.md',('3.2.1 ','3.2.2 ','3.2.3 '),'zh')]


def curated_paragraphs(text,prefixes,language):
    """Pre-extraction descriptive-text selection; exact text and rejected spans retained."""
    headings=list(re.finditer(r'^#+\s+(.+)$',text,re.M));accepted=[];excluded=[]
    for i,heading in enumerate(headings):
        if not heading.group(1).startswith(prefixes):continue
        if language=='en':
            end=next((h.start() for h in headings[i+1:] if re.match(r'2\.[1-9] |3\.',h.group(1))),len(text))
        else:end=headings[i+1].start() if i+1<len(headings) else len(text)
        section=text[heading.end():end]
        stop=re.search(r'^#+\s+(?:Problem|References|Analytic)',section,re.M)
        if stop:section=section[:stop.start()]
        for match in re.finditer(r'\S[\s\S]*?(?=\n\s*\n|\Z)',section):
            value=match.group().rstrip();start=heading.end()+match.start();reason=None
            if len(value)<45:reason='short_fragment'
            elif value.startswith(('#','$$','!','<','|')):reason='non_prose_block'
            elif EXCLUDED.search(value):reason='outside_descriptive_concept_scope'
            elif value.count('$')>4 or re.match(r'^(?:Equation|TABLE|FIGURE|Figure|where\b|In Equation|Substitut|式中)',value):reason='formula_or_symbol_fragment'
            row={'section':heading.group(1),'start':start,'end':start+len(value),'text':value}
            assert text[row['start']:row['end']]==value
            (excluded if reason else accepted).append({**row,**({'reason':reason} if reason else {})})
    return accepted,excluded


def batches(paragraphs,limit=2600):
    groups=[];current=[];length=0
    for row in paragraphs:
        if current and (length+len(row['text'])>limit or row['section']!=current[-1]['section']):
            groups.append(current);current=[];length=0
        current.append(row);length+=len(row['text'])
    if current:groups.append(current)
    return groups


def excerpt(group,document_id):
    text='# Descriptive concept source excerpt\n\n';segments=[]
    for i,p in enumerate(group,1):
        text+=f'[Source paragraph {i}; {p["section"]}; intervening text may be omitted]\n\n'
        start=len(text);text+=p['text'];end=len(text);text+='\n\n'
        segments.append({'excerpt_start':start,'excerpt_end':end,'parent_start':p['start'],'parent_end':p['end'],
            'section':p['section'],'paragraph_hash':sha256_json(p['text'])})
    return text,segments


def original_anchors(candidate,document,parent_text):
    """Every accepted quotation must be wholly inside one exact parent paragraph."""
    anchors=[]
    for a in candidate.get('evidence_items',[]):
        segment=next((s for s in document['segments'] if s['excerpt_start']<=a['start'] and a['end']<=s['excerpt_end']),None)
        if segment is None:raise ValueError('quote crosses omitted material or synthetic source markers')
        start=segment['parent_start']+a['start']-segment['excerpt_start'];end=start+len(a['quote'])
        if parent_text[start:end]!=a['quote']:raise ValueError('original-source quotation mismatch')
        anchors.append({'evidence_id':a['evidence_id'],'source_group_id':document['source_group_id'],
            'parent_path':document['parent_path'],'parent_document_version':document['parent_document_version'],
            'start':start,'end':end,'quote':a['quote'],'offset_basis':'canonical_parent_document'})
    if not anchors:raise ValueError('candidate has no verified parent-source quotation')
    return anchors


def tags(candidate):
    t=candidate['triple'];text=' '.join(candidate.get('evidence_quotes',[]));result=[]
    patterns={'negation':r'\b(?:not|no|never|neither|without)\b|不能|不随|没有|并非|忽略',
        'condition_modality':r'\b(?:if|when|under|assuming|assume|can|may|must|only|constant)\b|如果|当|对于|在.*情况|只有|可|假设',
        'reference_context':r'\b(?:this|these|those|it|they|such)\b|这种|这时|其|上述',
        'definition_taxonomy':r'\b(?:defined|definition|known as|called|types|classified|consists)\b|称为|定义|组成|分为',
        'direction_relation':r'\b(?:increase|decrease|depends|cause|result|proportional)\b|增大|减小|取决于|引起|导致'}
    for name,pattern in patterns.items():
        if re.search(pattern,text,re.I):result.append(name)
    if not t.get('relation_id'):result.append('formal_mapping_unassigned')
    return result or ['direct_statement']


def ordered_sample(candidates,count):
    """Round-robin source section/tag strata, never labels, scores or review outcomes."""
    groups=defaultdict(list)
    for c in candidates:
        primary=next((x for x in ('negation','condition_modality','reference_context','definition_taxonomy','direction_relation') if x in tags(c)),'direct_statement')
        groups[(c['source_group_id'],c['source_section'],primary)].append(c)
    for group in groups.values():group.sort(key=lambda c:sha256_json([SEED,c['candidate_id']]))
    keys=sorted(groups,key=lambda k:sha256_json([SEED,k]));result=[]
    while len(result)<min(count,len(candidates)):
        for k in keys:
            if groups[k] and len(result)<count:result.append(groups[k].pop(0))
    return result


def bind_source_runs(candidates):
    """A dataset packaging derivation; source expressions and quotations are immutable."""
    result=[]
    for ordinal,original in enumerate(candidates):
        c=deepcopy(original)
        c['extraction_source_binding']={'candidate_id':original['candidate_id'],'run_id':original['run_id'],
            'candidate_content_hash':sha256_json(original),'derivation':'document_run_binding_only_v1'}
        c['run_id']='run_'+VERSION+':'+c['document_id']
        from review_agent.contracts import candidate_id_for,fact_key_for
        c['candidate_id']=candidate_id_for(c['run_id'],c['document_id'],c['source_chunk_id'],ordinal)
        c['fact_key']=fact_key_for(c)
        if c['triple']!=original['triple'] or c['evidence_items']!=original['evidence_items']:
            raise ValueError('dataset binding changed the source candidate')
        result.append(c)
    return result


def prepare(out):
    from document.snapshot import read_snapshot,make_snapshot
    from document.hierarchy_index import split_markdown_text,build_document_index,persist_document_index
    from ontologyBuild.release_service import load_release
    from extract_triplets.candidate_contract import RESPONSE_SCHEMA
    from extract_triplets.pipeline_v4 import prompt_template,source_scope
    from schema.subontology_retriever import SubOntologyRetriever
    if out.exists() or not out.is_relative_to(ROOT/'outputs'):raise ValueError('new isolated outputs directory required')
    out.mkdir(parents=True);(out/'sources').mkdir();(out/'requests').mkdir()
    frozen=read(ROOT/'outputs/ontology_freeze_20261116/live/frozen_release.json');release=load_release(frozen['path'])
    base_prompt,_=prompt_template()
    prompt=base_prompt+'\n本次仅提取一般气体、热力学、流体概念的非操作性描述关系，不提取公式、数值数据、计算步骤或应用设计。来源段落可能不连续，段落标记不是事实；不跨省略内容补出关系。主谓宾保持原文语言，引文逐字保留。完整保留条件和否定；保持真实未知，不为凑数生成候选。不输出任何审核票或人工标签。\n'
    (out/'extraction_prompt.txt').write_text(prompt,encoding='utf-8')
    documents=[];rejected=[];proposals=[]
    for pattern,prefixes,language in SOURCE_PLAN:
        paths=sorted((ROOT/'mdfiles').glob(pattern))
        if len(paths)!=1:raise ValueError('source plan does not identify one document')
        parent=read_snapshot(paths[0]);accepted,excluded=curated_paragraphs(parent['canonical_text'],prefixes,language)
        group_id='book_'+sha256_json([parent['document_id'],parent['document_version']])[:16]
        rejected.extend({'source_group_id':group_id,**x} for x in excluded)
        for number,group in enumerate(batches(accepted),1):
            docid=group_id+'_excerpt_'+str(number).zfill(2);text,segments=excerpt(group,docid)
            source=out/'sources'/(docid+'.md');source.write_text(text,encoding='utf-8')
            snapshot=make_snapshot(text,docid)
            chunks=split_markdown_text(text,document_id=docid,chunk_size=max(12000,len(text)+1),chunk_overlap=0,snapshot=snapshot)
            index=build_document_index(chunks);index_path=out/'sources'/(docid+'_index.json');persist_document_index(index,index_path)
            if len(chunks)!=1:raise ValueError('one extraction scope required per source excerpt')
            scope=source_scope(index,chunks[0],start=0,end=len(text),neighbors=0)
            scope.update(context_start=0,context_end=len(text))
            doc={'document_id':docid,'document_version':index['document_version'],'index_path':str(index_path),'path':str(source),
                'index_hash':index['index_content_hash'],'scope':scope,'segments':segments,'source_group_id':group_id,
                'parent_path':str(paths[0]),'parent_document_version':parent['document_version'],'parent_raw_file_hash':parent['raw_file_hash'],
                'language':language,'source_section':group[0]['section']}
            documents.append(doc)
            payload={'task_type':'source_candidate_extraction','core_text':text,'context_text':text,'scope':scope,
                'subontology':SubOntologyRetriever(release.ontology).retrieve(group[0]['section'],text),
                'ontology_release':release.binding(),'output_schema':RESPONSE_SCHEMA,
                'instruction':'Extract natural source candidates only. No target count. Evidence quotes must be verbatim within one source paragraph.'}
            p={'version':'source_candidate_extraction_v1','task_id':VERSION+':'+docid,'document_id':docid,
                'source_group_id':group_id,'source_index_hash':index['index_content_hash'],'system_prompt':prompt,
                'payload':payload,'schema_hash':sha256_json(RESPONSE_SCHEMA),'max_output_tokens':9000}
            p['proposal_digest']=sha256_json(p);proposals.append(p)
    # Cover the smaller Chinese source before the larger source in a bounded run.
    proposals.sort(key=lambda p:(next(d['language'] for d in documents if d['document_id']==p['document_id'])!='zh',p['document_id']))
    write(out/'source_manifest.json',{'documents':documents,'source_group_count':len(SOURCE_PLAN),'split_role':'development',
        'independent_test':False,'source_selection':'predeclared concept sections; deterministic paragraph exclusions before extraction',
        'group_unit':'original book; excerpts/paragraphs are not independent documents for splitting or statistical claims',
        'source_preparation_uses_test_labels':False,'retained_paragraphs':sum(len(d['segments']) for d in documents)})
    write(out/'source_exclusions.json',rejected);write(out/'extraction_proposals.json',proposals)
    write(out/'protocol.json',{'version':VERSION,'seed':SEED,'target_pool_size':TARGET,'target_pilot_size':PILOT,
        'max_extraction_calls':MAX_CALLS,'known_token_stop':TOKEN_STOP,'per_call_timeout_seconds':180,
        'retries':0,'stop_on_unknown':True,'no_target_count_in_model_payload':True,'human_labels_created':0,
        'fixed_release':release.binding(),'new_source_groups':len(SOURCE_PLAN),'split_role':'development_only',
        'candidate_selection':'source-section and lexical-feature round-robin, independent of review outcomes',
        'actual_semantic_revision':False,'ontology_publishing':False,'memory_learning':False,'review_model_calls':0,
        'sampling_features_are_gold':False,'paper_accuracy':None,'historical_unknown_reissues':0,
        'retained_source_characters':sum(s['excerpt_end']-s['excerpt_start'] for d in documents for s in d['segments'])})
    print(json.dumps({'prepared_scopes':len(proposals),'source_books':len(SOURCE_PLAN),'max_calls':MAX_CALLS},ensure_ascii=False),flush=True)


def execute(out):
    from extract_triplets.audited_requests import VERSION as TASK,binding,judge_once
    from extract_triplets.candidate_contract import normalize_candidate
    from review_agent.repository import ReviewRepository
    from review_agent.request_execution import judge_typed
    from review_agent.cli import _configured_validators
    from review_agent.validators import _parse_content
    from runs.regress_v8 import configuration,public_settings
    from document.hierarchy_index import load_document_index
    from document.snapshot import read_snapshot
    from ontologyBuild.release_service import load_release
    if (out/'started.json').exists():raise ValueError('already started; inspect durable receipts instead of relaunching')
    documents={d['document_id']:d for d in read(out/'source_manifest.json')['documents']}
    release=load_release(read(out/'protocol.json')['fixed_release']['ontology_release_path'])
    config=configuration(profile='weighted_diagnostic',directory=out)
    voters=_configured_validators(config);voter=next(v for v in voters if v['model_family']=='deepseek')
    voter.update(id='extractor',max_output_tokens=9000,http_timeout_seconds=180,request_timeout_seconds=180)
    write(out/'extractor_settings.json',{'settings':public_settings(config),'selected_model':voter['model_id'],
        'selected_family':voter['model_family'],'role':'candidate_generation_only','max_output_tokens':9000})
    repo=ReviewRepository(out/'extraction.sqlite3');repo.initialize()
    write(out/'started.json',{'automatic_relaunch':False,'max_extraction_calls':MAX_CALLS,'time':time.time()})
    candidates=[];rejected=[];duplicates=[];seen=set();known_tokens=0;calls=0;stopped=None
    parent_cache={};indexes={}
    for p in read(out/'extraction_proposals.json'):
        if calls>=MAX_CALLS or known_tokens>=TOKEN_STOP:stopped='predeclared_budget';break
        if len(candidates)>=TARGET:stopped='target_pool_reached';break
        doc=documents[p['document_id']]
        index=load_document_index(doc['index_path'],doc['index_hash'],require_verified=True);indexes[doc['document_id']]=index
        if doc['parent_path'] not in parent_cache:parent_cache[doc['parent_path']]=read_snapshot(doc['parent_path'])
        parent=parent_cache[doc['parent_path']]
        if parent['document_version']!=doc['parent_document_version']:raise ValueError('original source changed')
        result=judge_typed(voter,p,repo,task_type=TASK,binding=binding(voter,p),invoke=lambda feedback:judge_once(voter,p,feedback),
            validate_cached=lambda v:[] if v.get('response_status')=='ok' else ['not valid extraction'],
            prompt_hash=sha256_json(p['system_prompt']),retries=0,timeout=180,deadline_epoch=time.time()+180)
        calls+=result.get('actual_calls',0)
        known_tokens+=sum((result.get(k)or 0) for k in ('input_tokens','output_tokens'))
        write(out/'requests'/(p['document_id']+'.json'),result)
        if result.get('outcome_unknown') or result.get('pause_job'):stopped='request_unknown_or_service_pause';break
        if result.get('response_status')!='ok':
            rejected.append({'task_id':p['task_id'],'reason':'extraction_response_not_valid','response_status':result.get('response_status')});continue
        for raw in _parse_content(result['raw_response'])['triplets']:
            try:
                c=normalize_candidate(raw,index=index,scope=doc['scope'],ontology=release.ontology,release_binding=release.binding(),
                    run_id='run_'+VERSION,ordinal=len(candidates)+len(duplicates)+len(rejected),prompt_hash=hashlib.sha256(p['system_prompt'].encode()).hexdigest(),
                    require_semantic_context=True)
                anchors=original_anchors(c,doc,parent['canonical_text'])
                fingerprint=sha256_json([doc['source_group_id'],c['triple'],c.get('source_scope'),c['evidence_quotes']])
                if fingerprint in seen:duplicates.append({'raw':raw,'document_id':doc['document_id']});continue
                seen.add(fingerprint)
                c.update(source_group_id=doc['source_group_id'],source_section=doc['source_section'],original_source_anchors=anchors,
                    extraction_model=voter['model_id'],extraction_response_id=result.get('response_id'),
                    extraction_attempt_id=result.get('attempt_id'),independent_human_gold=False,split_role='development')
                candidates.append(c)
            except (ValueError,TypeError,KeyError) as exc:rejected.append({'raw':raw,'document_id':doc['document_id'],'reason':str(exc)})
        write(out/'requests'/(p['document_id']+'_intake.json'),{'candidate_count_so_far':len(candidates),'known_tokens_so_far':known_tokens})
        print(json.dumps({'scope':doc['document_id'],'status':result['response_status'],'candidates':len(candidates),'calls':calls,'known_tokens':known_tokens},ensure_ascii=False),flush=True)
    write(out/'all_extracted_candidates.json',candidates);write(out/'intake_rejections.json',rejected);write(out/'exact_duplicates.json',duplicates)
    selected=ordered_sample(bind_source_runs(candidates),TARGET)
    write(out/'candidates.json',selected)
    summary={'natural_candidates':len(candidates),'selected_candidates':len(selected),'requested_target':TARGET,
        'calls':calls,'known_tokens':known_tokens,'stop_reason':stopped or 'planned_sources_exhausted',
        'schema_or_source_rejections':len(rejected),'exact_duplicates':len(duplicates),'human_labels':0,'review_calls':0,
        'unassigned_formal_relation_count':sum(not c['triple']['relation_id'] for c in selected),
        'source_book_counts':dict(Counter(c['source_group_id'] for c in selected)),
        'language_counts':dict(Counter(documents[c['document_id']]['language'] for c in selected)),
        'sampling_tag_counts':dict(Counter(t for c in selected for t in tags(c))),
        'accuracy':None,'price':None,'historical_unknown_resent':0,'production_written':False}
    write(out/'intake_summary.json',summary)
    build_pilot(out,selected,documents,release,voters,config)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


def build_pilot(out,candidates,documents,release,voters,config):
    from .manifest import freeze_manifest,save_manifest
    from review_agent.orchestrator import ReviewOrchestrator
    from review_agent.ontology_guard import OntologyGuard,load_snapshot
    from review_agent.annotation_contract import DIMENSIONS
    from evidence.hierarchical_retriever import retrieve_initial,_evidence
    from evidence.verification import make_evidence_bundle,verify_evidence_bundle
    from document.hierarchy_index import load_document_index
    from review_agent.repository import ReviewRepository
    pool=freeze_manifest(candidates,run_id='run_'+VERSION,settings={'split_role':'development','seed':SEED,'source_manifest_hash':sha256_json(read(out/'source_manifest.json'))})
    save_manifest(pool,out/'population_manifest.json')
    chosen=ordered_sample(candidates,PILOT);pilot_dir=out/'pilot';pilot_dir.mkdir()
    manifest=freeze_manifest(chosen,run_id='pilot_'+VERSION,settings={'split_role':'development_trial_annotation','parent_manifest_hash':pool['manifest_hash']})
    save_manifest(manifest,pilot_dir/'manifest.json')
    repo=ReviewRepository(out/'candidate_pool.sqlite3');repo.initialize()
    groups=defaultdict(list)
    for c in candidates:groups[c['run_id']].append(c)
    for run_id,rows in groups.items():
        doc=documents[rows[0]['document_id']]
        if any(c['source_index_hash']!=doc['index_hash'] for c in rows):raise ValueError('one run cannot bind multiple documents')
        repo.register_v4_run({'run_id':run_id,'document_path':doc['index_path'],'source_index_hash':doc['index_hash'],
            'source_hash':doc['document_version'],'ontology_hash':release.manifest['ontology_version'],
            'mode':'shadow',**release.binding()},rows)
    config=deepcopy(config);config['memory']['enabled']=False;config['planner']['enabled']=False
    owner=ReviewOrchestrator(repo,OntologyGuard(load_snapshot(release.snapshot_path)),voters,config=config)
    samples=[];templates=[];method_inputs=[]
    for n,c in enumerate(chosen,1):
        doc=documents[c['document_id']];index=load_document_index(doc['index_path'],doc['index_hash'],require_verified=True)
        bundle=retrieve_initial(c,index,max_items=8)
        chunk=index['chunks'][0]
        bundle=verify_evidence_bundle(make_evidence_bundle([*bundle['items'],
            _evidence(chunk,0,len(chunk['content']),'current_chunk')],['extraction_quotes','adjacent_sentence','current_chunk']),index,c)
        p=owner._proposal('annotation_input_'+c['candidate_id'],c,owner.ontology_guard.check(c),bundle,1,
            {'admission_policy':owner.admission_policy(),'request_cache_namespace':VERSION,'review_memory':{}})
        bind={k:c.get(k) for k in ('candidate_id','revision','document_id','document_version','source_index_hash','ontology_release_id','ontology_version','profile_hash')}
        bind.update(candidate_content_hash=sha256_json(c),proposal_digest=p['proposal_digest'],evidence_hash=bundle['evidence_hash'])
        sample={'sample_id':f'PILOT-{n:03d}','candidate':c,'binding':bind,'evidence_bundle':bundle,
            'source_context':chunk['content'],'original_source_anchors':c['original_source_anchors'],
            'source_group_id':c['source_group_id'],'source_section':c['source_section'],
            'votes':[],'model_conclusions_visible':False,'sampling_tags_are_labels':False}
        samples.append(sample);method_inputs.append(p)
        templates.append({'sample_id':sample['sample_id'],'binding':bind,
            'raw_annotation':{'labels':{d:None for d in DIMENSIONS},'note':'','supporting_evidence_ids':[],
                'counter_evidence_ids':[],'semantic_error':None,'expected_revision':None,'reviewer':None,'annotated_at':None,
                'duration_seconds':None,'saw_model_conclusions':False,'full_bundle_reviewed':False},
            'adjudication':{'adjudication_status':'pending','adjudicated_by':None,'adjudicated_label':{},
                'supporting_evidence_ids':[],'counter_evidence_ids':[],'mapping_evidence':{},'coverage_evidence':{},
                'reviewer':None,'annotated_at':None,'saw_model_conclusions':False,'full_bundle_reviewed':False,'note':''}})
    write(pilot_dir/'review_samples.json',{'version':VERSION,'split_role':'development','manifest_hash':manifest['manifest_hash'],
        'human_and_model_share_initial_evidence':True,'model_conclusions_visible':False,'samples':samples})
    write(out/'method_inputs.json',method_inputs)
    for name in ('reviewer_A','reviewer_B'):
        write(pilot_dir/(name+'_blank.json'),{'reviewer_role':name,'manifest_hash':manifest['manifest_hash'],'annotations':templates})
    from schema.relation_registry import RelationRegistry
    write(pilot_dir/'ontology_reference.json',RelationRegistry(release.ontology).reference_catalog())
    render_cards(pilot_dir,samples)
    (pilot_dir/'README.md').write_text(annotation_guide(len(chosen)),encoding='utf-8')


def annotation_guide(count):
    return f'''# 开发试标说明

本包 {count} 条，均为新来源模型抽取候选，尚无人工作答。先阅读 `cards.html` 与共享本体 `ontology_reference.json`；A、B 分别填写自己的 `reviewer_A_blank.json` / `reviewer_B_blank.json`，互不查看结果。标注对象是原候选，修改建议另存，不能用修改后的事实替代原标签。所有标签留空不是 unknown；unknown 必须由人明确选择。

六项原始标签：来源 source_support（supported/contradicted/insufficient/unresolved）；表达 expression_status（faithful/needs_revision/unresolved）；已有映射 mapping_status（correct/incorrect/unassigned/ambiguous/unresolved）；本体覆盖 schema_coverage（covered/not_covered/unresolved）；范围 knowledge_scope（general/conditional/local_definition/example/derivation/unresolved）；操作偏好 admission_preference（include/revise/exclude/unresolved）。操作偏好不能倒推其他标签。

有来源支持也可能表达有误；正文自相矛盾可保留 unresolved。否定、方向、条件、主体指代、局部定义及近似含义逐项核对。关系 ID 为空标 unassigned，不把映射缺失当作事实错误；covered/correct 须列正式 ID、定义、域值域依据。正文相邻段落可能经过省略，不能凭空连接指代；需要额外上下文时记录缺口，不猜答案。初始材料与后续方法输入相同，新增核证材料须独立归档。

模态试标规则 V1：区分认知可能性、能力/许可、假设、近似限定和定义性陈述。“可近似看成”中的近似意义不能丢失，也不因有“可”就机械改成能力；候选 modality 与谓词/附着关系一起判断。确认含义保持可标 faithful；确认错位标 needs_revision 并写依据；来源仍不足才标 unresolved。程序不自动将 can 改成 asserted。该规则是待试标检验的开发规范，不是本包预填答案。

为每条记录支持/反驳 evidence_id、理由、审核人、时间、实际 duration_seconds 和 full_bundle_reviewed；模型结论未展示，saw_model_conclusions 应如实填写。两人原标保留，分歧由人工在 adjudication 中填写 adjudicated_label、证据及最终 reviewer/annotated_at，并将 adjudicated_by 设 human；没有人类裁决不得设 resolved。金标准建设工时与今后 Agent 辅助审核工时分别记录。本轮不生成论文准确率或模型可靠性统计。

仅属开发池。两本书的切片不算独立文档，不能拆分同书片段冒充独立测试；英文候选保留英文原文，语言因素单列。当前 48 个关系来自此前开发材料，应如实报告本体对这些新概念的覆盖限制。
'''


def render_cards(directory,samples):
    esc=lambda v:html.escape(str(v))
    cards=[]
    for s in samples:
        t=s['candidate']['triple'];binding=s['binding']
        facts=esc(json.dumps({'qualifiers':t['qualifiers'],'qualifier_status':t['qualifier_status'],
            'semantic_context':t.get('semantic_context'),'relation_id':t.get('relation_id'),
            'head_type':t.get('head_type'),'tail_type':t.get('tail_type')},ensure_ascii=False,indent=2))
        evidence=''.join('<li><code>'+esc(e['evidence_id'])+'</code><p>'+esc(e.get('quote',''))+'</p></li>' for e in s['evidence_bundle']['items'])
        cards.append(f'<article><h2>{esc(s["sample_id"])}</h2><h3>{esc(t["head"])} → {esc(t["surface_relation"])} → {esc(t["tail"])}</h3>'
            f'<p>{esc(s["source_section"])} · {esc(s["source_group_id"])}</p><details><summary>限定信息与现有映射（须核对）</summary><pre>{facts}</pre></details>'
            f'<h4>来源证据</h4><ul>{evidence}</ul><details><summary>完整冻结初始上下文</summary><pre>{esc(s["source_context"])}</pre></details>'
            f'<details><summary>绑定信息</summary><pre>{esc(json.dumps(binding,ensure_ascii=False,indent=2))}</pre></details>'
            '<p>填写对应 JSON 的 raw_annotation；此页未展示模型审核意见，未填入任何金标签。</p></article>')
    page='<!doctype html><html lang="zh"><meta charset="utf-8"><title>开发候选人工试标</title><style>body{max-width:1000px;margin:32px auto;padding:0 20px;font:17px/1.7 system-ui;color:#202630;background:#f5f7fa}article{padding:24px;margin:24px 0;background:white;border:1px solid #cfd7e1;border-radius:10px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px}h3{color:#143f68}code{overflow-wrap:anywhere}summary{cursor:pointer}@media print{article{break-before:page}}</style><h1>开发候选人工试标</h1><p>标注原候选的六项判断；完整说明见 README.md。标签尚未填写，来源与本体资料共同核对。</p>'+''.join(cards)+'</html>'
    (directory/'cards.html').write_text(page,encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--run',action='store_true');parser.add_argument('--execute-prepared',action='store_true')
    args=parser.parse_args();out=args.output.resolve()
    if not out.is_relative_to(ROOT/'outputs'):raise ValueError('isolated outputs directory required')
    if not args.execute_prepared:prepare(out)
    if args.run or args.execute_prepared:execute(out)


if __name__=='__main__':main()
