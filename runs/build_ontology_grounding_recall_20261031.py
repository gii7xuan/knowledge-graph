"""Prepare term-by-term source recall; textual presence is never semantic approval."""
from collections import Counter
from pathlib import Path
import json
from rdflib import URIRef,RDFS
from rdflib.namespace import SKOS
from review_agent.repository import ReviewRepository
from review_agent.admission import load_admission_artifacts
from ontology_review.delta import catalog
from ontologyBuild.rdf_graph import parse_turtle
from evidence.hierarchical_retriever import document_sentence_evidence
from evidence.verification import make_evidence_bundle,verify_evidence_bundle

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/ontology_publication_20261031'
repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');owner=repo.get_revision('cand_f796e51209e35106f7c1')
base,index=load_admission_artifacts(repo,owner);terms=catalog(base.ontology)
graph=parse_turtle((base.path/'ontology.ttl').read_text(encoding='utf-8')) if (base.path/'ontology.ttl').exists() else None
texts=index['sentences'];by_id={t['id']:t for t in terms.values()};rows=[]
for identity,item in sorted(terms.items()):
    names=[n for n in [item.get('label'),*item.get('aliases',[])] if n]
    exact=[s for s in texts if any(n in s['text'] for n in names)]
    endpoints=[by_id[ref].get('label') for side in ('domain','range') for ref in item.get(side,[]) if ref in by_id]
    contextual=[s for s in texts if endpoints and all(label in s['text'] for label in endpoints if label)]
    found={s['sentence_id']:s for s in [*exact,*contextual]}
    ordered=sorted(found.values(),key=lambda s:(-sum(3 for n in names if n in s['text'])-sum(5 for label in endpoints if label and label in s['text']),len(s['text'])))[:3]
    bundle=verify_evidence_bundle(make_evidence_bundle([document_sentence_evidence(index,s) for s in ordered],['term_grounding_recall']),index,owner)
    if bundle.get('rejected_items'):raise ValueError('recall contains an unverified source anchor')
    iri=item.get('iri') or item.get('source_iri');labels=[]
    if graph is not None and iri:
        labels=[str(v) for p in (RDFS.label,SKOS.prefLabel,SKOS.altLabel) for v in graph.objects(URIRef(iri),p)]
    rows.append({'schema_id':identity,'iri':iri,'label':item.get('label'),'kind':item['schema_kind'],
        'definition':item.get('description') or item.get('definition'), 'domain':item.get('domain'),'range':item.get('range'),
        'rdf_labels':labels,'rdf_label_present':bool(labels),'exact_declared_name_occurrences':len(exact),
        'endpoint_context_occurrences':len(contextual),'source_contexts':bundle['items'],
        'semantic_coverage_status':'not_adjudicated','text_presence_is_not_semantic_approval':True})
summary={'base_release_id':base.manifest['release_id'],'ontology_version':base.manifest['ontology_version'],
    'document_id':index['document_id'],'document_version':index['document_version'],'source_index_hash':owner['source_index_hash'],
    'term_count':len(rows),'kinds':dict(Counter(r['kind'] for r in rows)),
    'terms_with_rdf_labels':sum(r['rdf_label_present'] for r in rows),'terms_with_exact_source_name':sum(bool(r['exact_declared_name_occurrences']) for r in rows),
    'terms_with_contextual_source_recall':sum(bool(r['source_contexts']) for r in rows),
    'terms_without_exact_source_name':[{'schema_id':r['schema_id'],'label':r['label'],'endpoint_context_occurrences':r['endpoint_context_occurrences']} for r in rows if not r['exact_declared_name_occurrences']],
    'model_calls':0,'production_database_modified':False,'semantic_questions_marked_pass':False}
(OUT/'grounding_recall.json').write_text(json.dumps({'summary':summary,'items':rows},ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'grounding_recall_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
