"""Generate a source-grounded, non-operational concept ontology draft."""
import hashlib
import json
from pathlib import Path
import re
import time
import threading
from types import SimpleNamespace

from ontologyBuild import pipeline
from ontologyBuild.config import OntologyPipelineConfig
from review_agent.service import load_config
from review_agent.model_settings import configured_planner
from ontologyBuild.rdf_artifact import canonical_turtle
from ontologyBuild.import_service import create_draft
from ontologyBuild.providers.base import OntologyImportRequest
from ontologyBuild.ontology_adapter import normalize_name
from rdflib import Graph,URIRef,Literal,RDF,RDFS,OWL,SKOS

ROOT=Path(__file__).resolve().parents[1]
INPUT=ROOT/'runs/ontology_chapter1_inputs'
SCOPE='''本次只构建非操作性的教学概念本体：概念名称、抽象定义、明确的分类层级及描述性关系。
不输出数学公式、数值、数值数据属性、计算或求解步骤、射击/瞄准/风偏修正规则、装药或结构设计参数、性能优化建议。
涉及这些内容的段落只抽取其中明确命名的抽象概念；不能把计算依赖关系改造成操作规则。
candidate_datatype_properties 必须为空。每个候选仍需当前片段逐字证据；章节标题只表示检索范围，不产生父类。
不要将示例弹丸、数值表格行、符号和单条实例泛化为类别或通用约束。不能依据外部常识补全。
输出严格沿用上面的六个数组及候选格式，不另加文字。'''


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


class ScopedCompletions:
    def __init__(self,client):
        self.client=client;self.chat=SimpleNamespace(completions=self)
        self.journal=INPUT/'model_requests.jsonl';self.count=0;self.blocked=False
        self.journal_lock=threading.Lock()
    def append_record(self, record):
        with self.journal_lock:
            with self.journal.open('a',encoding='utf-8') as out: out.write(json.dumps(record,ensure_ascii=False)+'\n')
    def create(self,**kwargs):
        if self.blocked: raise RuntimeError('Model unavailable; subsequent remote calls not attempted')
        kwargs['messages']=[dict(m) for m in kwargs['messages']]
        kwargs['messages'][0]['content']+='\n\n【本次任务范围，优先遵守】\n'+SCOPE
        self.count+=1;started=time.monotonic()
        record={'request_no':self.count,'model_requested':kwargs['model'],'request_hash':hashlib.sha256(json.dumps(kwargs,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
            'scope_hash':hashlib.sha256(SCOPE.encode()).hexdigest(),'status':'started'}
        self.append_record(record)
        try:
            response=self.client.chat.completions.create(**kwargs)
            usage=getattr(response,'usage',None)
            record.update(model_returned=getattr(response,'model',None),response_id=getattr(response,'id',None),
                input_tokens=getattr(usage,'prompt_tokens',None),output_tokens=getattr(usage,'completion_tokens',None),
                finish_reason=response.choices[0].finish_reason)
            response_dir=INPUT/'model_responses';response_dir.mkdir(exist_ok=True)
            response_path=response_dir/(record['request_hash']+'.json')
            write(response_path,response.model_dump(mode='json'))
            record['response_path']=str(response_path)
            content=(response.choices[0].message.content or '').strip()
            if '</think>' in content: content=content.rsplit('</think>',1)[1].strip()
            if content.startswith('```'):
                content=re.sub(r'^```(?:json)?\s*|\s*```$','',content,flags=re.IGNORECASE).strip()
            if response.choices[0].finish_reason=='length': raise ValueError('truncated_model_output')
            payload=json.loads(content)
            # Retain only conceptual content in generated artifacts. Quantitative
            # source material remains available in the original source snapshot.
            omitted=len(payload.get('candidate_datatype_properties') or [])
            payload['candidate_datatype_properties']=[]
            for section in ('candidate_classes','candidate_object_properties','candidate_constraints','uncertain_candidates'):
                kept=[]
                for item in payload.get(section,[]) or []:
                    if not isinstance(item,dict): continue
                    description=' '.join(str(item.get(k) or '') for k in ('name','normalized_name','definition_candidate','definition','reason','note'))
                    if re.search(r'\$|\\(?:frac|begin|dot|theta)|[=≈]|计算步骤|修正量|装药量',description): omitted+=1
                    else: kept.append(item)
                payload[section]=kept
            response.choices[0].message.content=json.dumps(payload,ensure_ascii=False)
            usage=getattr(response,'usage',None)
            record.update(status='completed',model_returned=getattr(response,'model',None),response_id=getattr(response,'id',None),
                input_tokens=getattr(usage,'prompt_tokens',None),output_tokens=getattr(usage,'completion_tokens',None),scope_omitted=omitted)
            return response
        except Exception as exc:
            status=getattr(exc,'status_code',None)
            if status in {401,402,403,404}: self.blocked=True
            record.update(status='failed',error_type=type(exc).__name__,http_status=status)
            raise
        finally:
            record['latency_ms']=int((time.monotonic()-started)*1000)
            self.append_record(record)


def build_draft(manifest):
    run=Path(manifest['outputs']['run_dir']);data=json.loads((run/'normalized_candidates.json').read_text(encoding='utf-8'))
    graph=Graph();provenance=[];pending=[];nodes={}
    for item in data.get('candidate_classes',[]):
        label=item.get('normalized_name') or item.get('name')
        if not label or item.get('validation_status') not in {'passed','dependency_pending'}: pending.append(item);continue
        node=URIRef('urn:kg:chapter1:class:'+hashlib.sha256(normalize_name(label).encode()).hexdigest()[:24])
        nodes[normalize_name(label)]=node
        graph.add((node,RDF.type,OWL.Class));graph.add((node,RDFS.label,Literal(label,lang='zh')))
        definition=item.get('definition_candidate') or item.get('definition')
        if definition and not re.search(r'\$|[=≈]|\\frac',str(definition)): graph.add((node,SKOS.definition,Literal(str(definition),lang='zh')))
        provenance.append({'iri':str(node),'candidate_id':item['candidate_id'],'evidence':item.get('evidence',[]),
            'parent_candidate':item.get('parent_candidate'),'status':'pending_semantic_review'})
    for item in data.get('candidate_classes',[]):
        parent=item.get('parent_candidate');child=nodes.get(normalize_name(item.get('normalized_name') or item.get('name') or ''))
        parent_node=nodes.get(normalize_name(parent or ''))
        if parent and (not parent_node or child==parent_node): pending.append({'candidate_id':item.get('candidate_id'),'reason':'unresolved_or_self_parent','parent':parent})
        elif child and parent_node: graph.add((child,RDFS.subClassOf,parent_node))
    # A model-generated hierarchy cycle remains pending rather than becoming an
    # executable assertion. Never derive hierarchy from headings.
    edges=list(graph.subject_objects(RDFS.subClassOf))
    def reachable(start,target,seen):
        if start==target:return True
        if start in seen:return False
        return any(reachable(v,target,seen|{start}) for v in graph.objects(start,RDFS.subClassOf))
    for child,parent in edges:
        graph.remove((child,RDFS.subClassOf,parent))
        if reachable(parent,child,set()): pending.append({'reason':'hierarchy_cycle','child':str(child),'parent':str(parent)})
        else: graph.add((child,RDFS.subClassOf,parent))
    for item in data.get('candidate_object_properties',[]):
        name=item.get('name');domain=nodes.get(normalize_name(str(item.get('domain_candidate') or '')));range_node=nodes.get(normalize_name(str(item.get('range_candidate') or '')))
        if not name or not domain or not range_node: pending.append(item);continue
        node=URIRef('urn:kg:chapter1:property:'+hashlib.sha256(json.dumps([name,str(domain),str(range_node)],ensure_ascii=False).encode()).hexdigest()[:24])
        graph.add((node,RDF.type,OWL.ObjectProperty));graph.add((node,RDFS.label,Literal(name,lang='zh')))
        graph.add((node,RDFS.domain,domain));graph.add((node,RDFS.range,range_node))
        provenance.append({'iri':str(node),'candidate_id':item['candidate_id'],'evidence':item.get('evidence',[]),'status':'pending_semantic_review'})
    ttl=run/'chapter1_ontology_draft.ttl';ttl.write_text(canonical_turtle(graph),encoding='utf-8')
    origin=json.loads((INPUT/'source_manifest.json').read_text(encoding='utf-8'))
    method={'method':'legacy_source_grounded_llm_concept_draft','pipeline_run_id':manifest['run_id'],
        'source':origin,'modeling_scope':SCOPE,'scope_hash':hashlib.sha256(SCOPE.encode()).hexdigest(),
        'approval':'pending','reasoner':'not_run','author_neon_scripts_executed':False}
    if manifest.get('source_generation_methods'):
        method['source_generation_methods']=manifest['source_generation_methods']
        method['parent_pipeline_run_id']=manifest.get('parent_pipeline_run_id')
    imported=create_draft(OntologyImportRequest(ttl,method_manifest=method,
        requirements=[{'id':'chapter1_concepts','text':'Represent source-grounded abstract concepts, definitions, hierarchy and descriptive relationships in chapter 1.'}],
        competency_questions=[{'id':'chapter1_source_coverage','text':'Are the named concepts grounded in the chapter and do declared relationships retain their original meaning?','status':'requires_semantic_review'}]),
        provider='neon',draft_root=ROOT/'runs/ontology_imports',release_root=ROOT/'runs/ontology_releases')
    write(run/'chapter1_provenance.json',provenance);write(run/'chapter1_pending_modeling.json',pending)
    write(run/'chapter1_import_preview.json',imported)
    draft_path=Path(imported['draft_path'])
    write(run/'chapter1_generation_report.json',{'pipeline_status':manifest['status'],'chapter_source':origin,
        'extraction_stats':manifest['extraction_stats'],'candidate_counts':manifest['candidate_counts'],
        'draft_path':str(draft_path),'turtle_path':str(ttl),'class_count':len(set(graph.subjects(RDF.type,OWL.Class))),
        'object_property_count':len(set(graph.subjects(RDF.type,OWL.ObjectProperty))),
        'subclass_edge_count':len(list(graph.subject_objects(RDFS.subClassOf))),
        'pending_modeling_count':len(pending),'structure_valid':imported['validation']['project_validation']['valid'],
        'semantic_review':'pending','reasoner':'not_run','published':False,'triple_candidates_created':0,
        'source_generation_methods':manifest.get('source_generation_methods',{})})
    print(json.dumps({'generation_report':str(run/'chapter1_generation_report.json'),'import_draft':str(draft_path),
        'class_count':len(set(graph.subjects(RDF.type,OWL.Class))),'object_property_count':len(set(graph.subjects(RDF.type,OWL.ObjectProperty))),
        'structure_valid':imported['validation']['project_validation']['valid']},ensure_ascii=False,indent=2),flush=True)


def main():
    from openai import OpenAI
    import httpx
    settings=configured_planner(load_config())
    config=OntologyPipelineConfig(extraction_engine='llm',review_mode='manual',corpus_role='build',base_policy='draft',
        model=settings['model'],api_key=settings['api_key'],openai_base_url=settings['base_url'],timeout_seconds=90,max_retries=1,
        chunk_size=5000,chunk_overlap=180,max_output_tokens=3072,context_max_chars=1500)
    client=OpenAI(api_key=settings['api_key'],base_url=settings['base_url'],timeout=90,max_retries=0,
        http_client=httpx.Client(trust_env=False,verify=settings.get('verify_ssl',True)))
    try:
        manifest=pipeline.run_ontology_pipeline(INPUT/'火炮弹道学_钱林方_第一章.md',config,llm_client=ScopedCompletions(client))
        build_draft(manifest)
    finally: client.close()


if __name__=='__main__': main()
