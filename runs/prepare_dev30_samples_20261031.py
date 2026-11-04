"""Read-only, revision-bound development sample export; no model calls or labels."""
import json,sqlite3,hashlib,collections,math
from pathlib import Path
from datetime import datetime,timezone,timedelta
from review_agent.repository import ReviewRepository,_ClosingConnection
from review_agent.proposal_resolver import selected_proposal
from review_agent.contracts import sha256_json
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from ontologyBuild.release_service import load_release
from ontology_review.delta import catalog
from document.hierarchy_index import load_document_index
from evidence.verification import verify_evidence_bundle,make_evidence_bundle
from evaluation.manifest import freeze_manifest

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/dev30_20261031'
OUT.mkdir(parents=True,exist_ok=True)
summary=json.loads((ROOT/'runs/ontology_publication_20261031/summary.json').read_text(encoding='utf-8'))
class ReadOnly(ReviewRepository):
    def connect(self):
        c=sqlite3.connect(self.database_path.resolve().as_uri()+'?mode=ro',uri=True,factory=_ClosingConnection)
        c.row_factory=sqlite3.Row;return c
repo=ReadOnly(summary['after']);run_id='run_chapter1_20261028_resume1'
base=load_release(ROOT/'runs/ontology_releases'/summary['release_head'])
guard=OntologyGuard(load_snapshot(base.ontology));terms=catalog(base.ontology)
run=repo.get_run(run_id)
current=repo.list_candidates(run_id,limit=1000000)
candidates=[repo.get_revision(x['candidate_id']) for x in current]
assert len(candidates)==320
doc=load_document_index(run['document_path'],candidates[0]['source_index_hash'],require_verified=True)
text=doc['source_snapshot']['canonical_text']
sections={x['section_id']:x for x in doc.get('sections',[])}
records=[];excluded=[]
for c in candidates:
    d=repo.latest_decision(c['candidate_id']) or {}
    try:p,v=selected_proposal(repo,c,d)
    except ValueError as e:
        excluded.append({'candidate_id':c['candidate_id'],'reason':str(e)});continue
    if len(v)!=3:continue
    bundle=verify_evidence_bundle(make_evidence_bundle(p['evidence_bundle']['items'],[]),doc,c)
    if bundle.get('rejected_items') or not bundle.get('items'):
        excluded.append({'candidate_id':c['candidate_id'],'reason':'source anchor failed'});continue
    g=guard.check(c);supported=d.get('source_support_result')=='supported'
    group='来源支持且本体检查通过' if supported and g['status']=='pass' else '分歧或依据待补充' if not supported else '来源支持但本体待处理'
    t=c['triple'];q=t.get('qualifiers',{});features=[]
    if c['revision']>1:features.append('已修订')
    if q.get('condition'):features.append('有条件')
    if q.get('polarity')=='negative':features.append('否定')
    if q.get('polarity')=='unknown':features.append('极性未明')
    if q.get('modality') not in (None,'asserted'):features.append('模态')
    if q.get('quantifier'):features.append('量词')
    if t.get('tail_kind')=='literal':features.append('字面量')
    if g['status']=='fail':features.append('本体约束不相容')
    if not t.get('head_type') or (t.get('tail_kind')!='literal' and not t.get('tail_type')):features.append('类型缺失')
    records.append({'candidate':c,'decision':d,'proposal':p,'votes':v,'guard':g,'group':group,'features':features,'evidence':bundle['items']})

seed='20261031-dev30-v1'
selected=[];seen_sections=collections.Counter();seen_features=collections.Counter();seen_relations=collections.Counter()
group_order=['来源支持且本体检查通过','分歧或依据待补充','来源支持但本体待处理']
pool_counts=collections.Counter(r['group'] for r in records)
for group in group_order:
    pool=[r for r in records if r['group']==group];assert len(pool)>=10,(group,len(pool))
    for _ in range(10):
        def rank(r):
            c=r['candidate'];rel=c['triple']['surface_relation'];section=c['section_id']
            score=6/(1+seen_sections[section])+3/(1+seen_relations[rel])+sum(2/(1+seen_features[f]) for f in r['features'])
            tie=hashlib.sha256((seed+c['candidate_id']).encode()).hexdigest()
            return (-score,tie)
        item=min(pool,key=rank);pool.remove(item);selected.append(item)
        seen_sections[item['candidate']['section_id']]+=1;seen_features.update(item['features']);seen_relations[item['candidate']['triple']['surface_relation']]+=1
selected.sort(key=lambda r:hashlib.sha256((seed+'order'+r['candidate']['candidate_id']).encode()).hexdigest())
assert len({r['candidate']['candidate_id'] for r in selected})==30

def type_label(identity):
    if not identity:return '候选未指定'
    return (terms.get(identity) or {}).get('label',identity)+' ['+identity+']'
qnames={'polarity':'极性','condition':'条件','modality':'模态','quantifier':'量词','time_scope':'时间范围','applicability':'适用范围'}
human=[];evidence_rows=[];model_rows=[];bindings=[]
for i,r in enumerate(selected,1):
    sid=f'DEV-{i:03}';c=r['candidate'];t=c['triple'];p=r['proposal'];d=r['decision']
    c['candidate_id'];binding={k:c.get(k) for k in ('candidate_id','revision','run_id','document_id','document_version','source_index_hash','ontology_release_id','ontology_version','profile_hash')}
    binding.update(sample_id=sid,candidate_content_hash=sha256_json(c),source_proposal_digest=p['proposal_digest'],audit_id=p['audit_id'],round_id=p['round_id'],stratum=r['group'],features=r['features'])
    bindings.append(binding)
    midpoint=(c.get('source_scope',{}).get('core_start',0)+c.get('source_scope',{}).get('core_end',0))/2
    items=sorted(r['evidence'],key=lambda e:(abs((e['start']+e['end'])/2-midpoint),e['evidence_id']))
    first=items[0];quote=first.get('quote') or first['text'];direct=quote if len(quote)<=500 else quote[:500]+'\n（定位节选，完整引文见原文证据表）'
    statement='主语：'+str(t['head'])+'\n关系：'+str(t['surface_relation'])+'\n宾语：'+str(t['tail'])
    statement+='\n主语类型：'+type_label(t.get('head_type'))
    if t.get('tail_kind')=='literal':statement+='\n字面量：'+json.dumps({k:t.get(k) for k in ('value','datatype','unit','language')},ensure_ascii=False)
    else:statement+='\n宾语类型：'+type_label(t.get('tail_type'))
    statement+='\n正式关系：'+str(t.get('relation_id') or '候选未指定')
    qualifiers='\n'.join(name+'：'+(str(t.get('qualifiers',{}).get(key)) if t.get('qualifiers',{}).get(key) is not None else '候选未记录')+' ['+str(t.get('qualifier_status',{}).get(key,'未记录状态'))+']' for key,name in qnames.items())
    human.append([sid,statement,qualifiers,direct]+[None]*12)
    for j,e in enumerate(items,1):
        quote=e.get('quote') or e['text'];assert text[e['start']:e['end']]==quote
        chunks=[quote[x:x+400] for x in range(0,len(quote),400)]
        for part,fragment in enumerate(chunks,1):
            left=e['start']+(part-1)*400;right=left+len(fragment);assert text[left:right]==fragment
            context=text[max(0,left-100):min(len(text),right+100)]
            sec=sections.get(e.get('section_id'),{});title=sec.get('title') or sec.get('heading') or e.get('section_id')
            evidence_rows.append([sid,j,e['evidence_id'],f'{part}/{len(chunks)}',title,left,right,fragment,context])
    votes={v['voter_id']:v for v in r['votes']}
    summaries=[]
    for member in p['committee_members']:
        v=votes.get(member['id'],{})
        summaries.append(member['model_id']+'：'+json.dumps({k:v.get(k) for k in ('response_status','source_support','verdict','schema_issue','reason_codes','requested_context','suggested_revision')},ensure_ascii=False))
    model_rows.append([sid,r['group'],'、'.join(r['features']),d.get('source_support_result'),r['guard']['status'],'\n'.join(summaries),c['candidate_id'],c['revision'],binding['candidate_content_hash'],p['audit_id'],p['round_id'],p['proposal_digest'],c['document_version'],c['source_index_hash'],c['ontology_release_id'],c['profile_hash']])

# Confirm every frozen selected version is still the live current version.
live=ReadOnly(ROOT/'runs/v3/review.sqlite3')
for r in selected:
    c=r['candidate'];now=live.get_revision(c['candidate_id']);assert sha256_json(now)==sha256_json(c),'live candidate changed'
manifest=freeze_manifest([r['candidate'] for r in selected],run_id=run_id,settings={'purpose':'development_manual_review','seed':seed,'strata':{g:10 for g in group_order},'selection':'deterministic diversity sampling within disjoint machine-status strata; not population-random sampling','blind_order':'hash-shuffled; predictions separate'})
ontology_rows=[]
for identity,item in sorted(terms.items()):
    definitions=item.get('definitions') or []
    definition='\n'.join(x.get('value','') for x in definitions) or item.get('definition') or item.get('description') or ''
    ontology_rows.append([identity,item['schema_kind'],item.get('label'),'; '.join(item.get('aliases',[])),definition,'; '.join(item.get('parents') or ([item['parent']] if item.get('parent') else [])),'; '.join(item.get('domain',[])),'; '.join(item.get('range',[]) or item.get('datatypes',[]) or ([item['datatype']] if item.get('datatype') else []))])
result={'created_at_beijing':datetime.now(timezone(timedelta(hours=8))).isoformat(),'human_rows':human,'evidence_rows':evidence_rows,'model_rows':model_rows,'ontology_rows':ontology_rows,'bindings':bindings,'manifest':manifest,'selection_pool_counts':dict(pool_counts),'source_document':{k:doc[k] for k in ('document_id','document_version')},'source_document_path':run['document_path'],'source_canonical_text':text,'selected_raw_candidates':[r['candidate'] for r in selected],'source_database':str(repo.database_path),'human_columns_blank':True}
(OUT/'sample_data.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'frozen_sample_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'samples':len(human),'strata':dict(collections.Counter(x['stratum'] for x in bindings)),'evidence_fragments':len(evidence_rows),'ontology_terms':len(ontology_rows),'live_revision_hash_checks':30,'sections':len(seen_sections),'features':dict(seen_features),'human_cells_blank':all(v is None for row in human for v in row[4:])},ensure_ascii=False))
