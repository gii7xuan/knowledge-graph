"""Recover lost source annotations into review materials, never an approved release."""
from copy import deepcopy
import json
import re

from review_agent.contracts import sha256_json


def candidate_definition(item):
    value=item.get('definition_candidate') or item.get('definition')
    return value.strip() if isinstance(value,str) and value.strip() and not re.search(r'\$|[=≈]|\\frac',value) else None


def add_definition(graph,node,item):
    from rdflib import Literal, SKOS
    value=candidate_definition(item)
    if value:graph.add((node,SKOS.definition,Literal(value,lang='zh')))


def restoration_materials(ontology,candidates,provenance,canonical_text):
    from .ontology_adapter import iter_classes,iter_object_properties,normalize_name
    classes={c['id']:c for c in iter_classes(ontology)}
    by_id={c['candidate_id']:c for c in candidates.get('candidate_object_properties',[])}
    links={}
    for record in provenance:links.setdefault(record.get('iri'),[]).append(record.get('candidate_id'))
    proposals=[];unresolved=[]
    for prop in iter_object_properties(ontology):
        if prop.get('definition') or prop.get('description') or prop.get('definitions'):continue
        matches=[]
        for identity in sorted(set(links.get(prop.get('iri'),[]))):
            source=by_id.get(identity)
            if not source:continue
            if normalize_name(source.get('name')or'')!=normalize_name(prop.get('label')or''):continue
            compatible=True
            for field in ('domain','range'):
                labels={normalize_name(classes.get(i,{}).get('label')or'') for i in prop.get(field,[])}
                if normalize_name(str(source.get(field+'_candidate')or'')) not in labels:compatible=False
            if not compatible:continue
            definition=candidate_definition(source)
            anchors=[];missing=[]
            for evidence in source.get('evidence',[]):
                quote=str(evidence.get('text')or'').strip()
                starts=[];pos=canonical_text.find(quote) if quote else -1
                while pos>=0:
                    starts.append(pos);pos=canonical_text.find(quote,pos+1)
                if len(starts)==1:
                    anchors.append({'quote':quote,'start':starts[0],'end':starts[0]+len(quote),
                        'offset_unit':'unicode_code_point','source_chunk_id':evidence.get('chunk_id')})
                else:missing.append({'quote':quote,'reason':'not_exactly_located' if not starts else 'ambiguous_occurrences'})
            if definition:
                matches.append({'candidate_id':identity,'definition':definition,'anchors':anchors,'unlocated_evidence':missing,
                    'source_candidate_hash':sha256_json(source),'semantic_support_status':source.get('semantic_support_status','not_run')})
        texts={r['definition'] for r in matches}
        grounded=[r for r in matches if r['anchors']]
        if len(texts)==1 and grounded:
            proposals.append({'relation_id':prop['id'],'iri':prop['iri'],'label':prop.get('label'),
                'definition':next(iter(texts)),'domain':deepcopy(prop.get('domain')),'range':deepcopy(prop.get('range')),
                'sources':matches,'status':'restored_candidate_annotation_pending_semantic_review'})
        else:
            unresolved.append({'relation_id':prop['id'],'label':prop.get('label'),'source_candidates':matches,
                'reason':'conflicting_source_definitions' if len(texts)>1 else 'no_uniquely_sourced_grounded_definition'})
    return {'version':'source_definition_restoration_v1','status':'review_materials_not_approved_release',
        'ontology_hash':sha256_json(ontology),'candidate_input_hash':sha256_json(candidates),'provenance_hash':sha256_json(provenance),
        'source_text_hash':sha256_json(canonical_text),'definitions':proposals,'unresolved':unresolved,
        'existing_ids_constraints_unchanged':True,'automatic_semantic_approval':False}


def restored_graph(turtle,materials):
    from rdflib import Graph, URIRef, Literal, SKOS
    from .rdf_graph import parse_turtle
    graph=parse_turtle(turtle,preserve_lexical=True)
    for row in materials['definitions']:
        graph.add((URIRef(row['iri']),SKOS.definition,Literal(row['definition'],lang='zh')))
    return graph
