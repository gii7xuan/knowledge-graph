"""Compile the seven reviewed operations against the complete immutable RDF graph."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
import rdflib
from rdflib import Graph, Literal, RDF, RDFS, OWL, SKOS, URIRef, XSD
from rdflib.compare import to_canonical_graph, isomorphic
from ontologyBuild.owl_to_project_schema import convert_turtle
from ontologyBuild.rdf_artifact import canonical_turtle, graph_hash, validate_owl_view, statement
from ontologyBuild.rdf_graph import parse_turtle
from ontologyBuild.iri_identity import owl_extension_identity
from ontologyBuild.release_service import load_release
from review_agent.contracts import sha256_json

VERSION='preserving_owl_delta_v1'
_ROOT=Path(__file__).resolve().parents[1]
COMPILER_SOURCES={name:hashlib.sha256((_ROOT/name).read_bytes()).hexdigest() for name in (
    'ontology_review/owl_delta.py','ontology_review/delta.py','ontologyBuild/rdf_graph.py',
    'ontologyBuild/rdf_artifact.py','ontologyBuild/owl_to_project_schema.py','ontologyBuild/iri_identity.py')}
COMPILER_HASH=sha256_json(COMPILER_SOURCES)
RETIRED_ID=URIRef('urn:kg-ontology:retiredSchemaId')
RETIRED_IRI=URIRef('urn:kg-ontology:retiredIRI')
XSD_NAMES={'string','boolean','integer','int','long','short','byte','nonNegativeInteger','positiveInteger','decimal','double','float','date','dateTime','anyURI'}


def _line(triple): return statement(triple)


def _language(value):
    if value is not None and not isinstance(value,str): raise ValueError('OWL annotation language must be a string or null')
    # RDFLib validates language tags; never infer language from script/characters.
    try: Literal('validate',lang=value or None)
    except Exception as exc: raise ValueError('invalid OWL annotation language: '+str(value)) from exc
    return value or None


def _datatype(value):
    if not isinstance(value,str): raise ValueError('OWL datatype must be a string')
    if value in XSD_NAMES: return URIRef(str(XSD)+value)
    if ':' not in value: raise ValueError('OWL datatype must be a supported alias or absolute IRI')
    result=URIRef(value); result.n3(); return result


def compile_owl_delta(base,operations):
    from .delta import catalog
    verified=load_release(base.path)
    if verified.manifest!=base.manifest or verified.ontology!=base.ontology or 'ontology.ttl' not in verified.manifest['artifacts']:
        raise ValueError('OWL source base release does not match its verified artifacts')
    raw=(base.path/'ontology.ttl').read_bytes()
    pinned=json.loads((base.path/'iri_id_map.json').read_text(encoding='utf-8'))
    before=Graph()
    method=json.loads((base.path/'method_manifest.json').read_text(encoding='utf-8'))
    for triple in to_canonical_graph(parse_turtle(raw.decode('utf-8-sig'),base_iri=method.get('source_base_iri'))): before.add(triple)
    graph=Graph()
    for triple in before: graph.add(triple)
    iri_for={identity:URIRef(iri) for iri,identity in pinned.items()}
    kinds={identity:item['schema_kind'] for identity,item in catalog(base.ontology).items()}
    for op in operations:
        if op['kind'] in {'add_class','add_object_property','add_data_property'}:
            expected_id,new_iri=owl_extension_identity(base.manifest['release_id'],op['kind'],op['changes']['label'])
            if op['target']!=expected_id: raise ValueError('OWL entity ID does not match the server-minted IRI')
            iri=URIRef(new_iri)
            if iri in graph.all_nodes() or op['target'] in iri_for: raise ValueError('new OWL entity IRI/ID already exists')
            iri_for[op['target']]=iri; pinned[str(iri)]=op['target']
            kinds[op['target']]={'add_class':'class','add_object_property':'object_property','add_data_property':'data_property'}[op['kind']]

    def iri(identity):
        if identity not in iri_for: raise ValueError('OWL operation references an unmapped or retired formal ID')
        return iri_for[identity]

    def named(subject,predicate,values):
        # Only the named constraint field is editable. Anonymous expressions remain.
        for obj in list(graph.objects(subject,predicate)):
            if isinstance(obj,URIRef): graph.remove((subject,predicate,obj))
        for obj in values: graph.add((subject,predicate,obj))

    traces=[]
    for op in operations:
        previous=set(graph); kind,target,changes=op['kind'],op['target'],op['changes']; subject=iri(target)
        if kind in {'add_class','add_object_property','add_data_property'}:
            graph.add((subject,RDF.type,{'add_class':OWL.Class,'add_object_property':OWL.ObjectProperty,'add_data_property':OWL.DatatypeProperty}[kind]))
            graph.add((subject,RDFS.label,Literal(changes['label'],lang=_language(changes.get('label_language')))))
            graph.add((subject,SKOS.definition,Literal(changes['definition'],lang=_language(changes.get('definition_language')))))
            for alias in changes.get('aliases',[]): graph.add((subject,SKOS.altLabel,Literal(alias,lang=_language(changes.get('alias_language')))))
        if kind in {'add_class','revise_constraint'} and kinds[target]=='class':
            if 'parents' in changes or 'parent' in changes:
                parents=changes.get('parents') if 'parents' in changes else ([changes['parent']] if changes.get('parent') else [])
                named(subject,RDFS.subClassOf,[iri(parent) for parent in parents])
        if kind in {'add_object_property','add_data_property','revise_constraint'} and kinds[target]!='class':
            if 'domain' in changes: named(subject,RDFS.domain,[iri(value) for value in changes['domain']])
            if kinds[target]=='object_property' and 'range' in changes: named(subject,RDFS.range,[iri(value) for value in changes['range']])
            if kinds[target]=='data_property' and ('datatype' in changes or 'datatypes' in changes):
                values=changes.get('datatypes') if 'datatypes' in changes else [changes['datatype']]
                named(subject,RDFS.range,[_datatype(value) for value in values])
        if kind=='add_alias':
            for alias in changes['aliases']: graph.add((subject,SKOS.altLabel,Literal(alias,lang=_language(changes.get('alias_language')))))
        elif kind=='revise_definition':
            predicate=SKOS.definition if any(isinstance(v,Literal) for v in graph.objects(subject,SKOS.definition)) else RDFS.comment
            existing=[value for value in graph.objects(subject,predicate) if isinstance(value,Literal)]
            languages={value.language for value in existing}
            if 'definition_language' in changes: language=_language(changes['definition_language'])
            elif len(languages)>1: raise ValueError('multilingual OWL definition revision requires explicit definition_language')
            else: language=next(iter(languages),None)
            for value in existing:
                if value.language==language: graph.remove((subject,predicate,value))
            graph.add((subject,SKOS.definition,Literal(changes['definition'],lang=language)))
        elif kind=='merge_terms':
            into=changes['into']; destination=iri(into)
            # Quotient the reviewed IRI throughout schema, anonymous axioms and ABox.
            # Both definitions/constraints survive; the actual union is re-imported.
            names=list(graph.objects(subject,RDFS.label))+list(graph.objects(subject,SKOS.prefLabel))
            for predicate in (RDFS.label,SKOS.prefLabel):
                for value in list(graph.objects(subject,predicate)):
                    graph.remove((subject,predicate,value)); graph.add((subject,SKOS.altLabel,value))
            if not names: graph.add((subject,SKOS.altLabel,Literal(catalog(base.ontology)[target]['label'])))
            for triple in list(graph):
                if subject in triple:
                    graph.remove(triple); graph.add(tuple(destination if term==subject else term for term in triple))
            for predicate in (RETIRED_ID,RETIRED_IRI): graph.add((predicate,RDF.type,OWL.AnnotationProperty))
            graph.add((destination,RETIRED_ID,Literal(target)))
            graph.add((destination,RETIRED_IRI,subject))
            del iri_for[target]; pinned.pop(str(subject),None)
        elif kind not in {'add_class','add_object_property','add_data_property','add_alias','revise_definition','revise_constraint'}:
            raise ValueError('unsupported OWL operation')
        current=set(graph)
        traces.append({'kind':kind,'target':target,'added':sorted(_line(t) for t in current-previous),'removed':sorted(_line(t) for t in previous-current)})

    source=canonical_turtle(graph)
    reparsed=parse_turtle(source)
    if not isomorphic(graph,reparsed): raise ValueError('OWL serialization changed RDF axioms')
    converted=convert_turtle(source,iri_id_map=pinned)
    profile=converted['application_profile']
    base_profile=deepcopy(base.application_profile); base_profile.pop('requires_difference_confirmation',None)
    if profile!=base_profile or base.application_profile.get('requires_difference_confirmation'):
        profile['requires_difference_confirmation']=True
    converted['project_schema']['application_profile']=profile
    validate_owl_view(source,converted['project_schema'],profile,converted['iri_id_map'],converted['mapping_report'])
    old,new=set(before),set(graph)
    # Every removal is attributable to an explicit operation, never JSON projection loss.
    authorized_removed={line for trace in traces for line in trace['removed']}
    if {_line(t) for t in old-new}-authorized_removed: raise ValueError('unreviewed RDF removal')
    patch={'compiler_version':VERSION,'compiler_hash':COMPILER_HASH,'compiler_sources':COMPILER_SOURCES,'canonicalization':'rdflib_to_canonical_graph_sorted_nt_v1',
        'rdflib_version':rdflib.__version__,'base_source_sha256':hashlib.sha256(raw).hexdigest(),
        'base_graph_hash':graph_hash(before),'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'graph_hash':graph_hash(graph),
        'before_triple_count':len(before),'after_triple_count':len(graph),'preserved_triple_count':len(old&new),
        'review_context':'complete_base_and_result_rdf','base_rdf':canonical_turtle(before),'merged_rdf':source,
        'added':sorted(_line(t) for t in new-old),'removed':sorted(_line(t) for t in old-new),'operations':traces,'normalized_operations':deepcopy(operations),
        'merge_policy':'explicit_iri_quotient_preserve_union','anonymous_constraints':'preserved_unless_explicit_merge_rewrites_reference',
        'profile_before':base.application_profile,'profile_after':profile,'iri_id_map_hash':sha256_json(converted['iri_id_map']),
        'mapping_report_hash':sha256_json(converted['mapping_report'])}
    patch['patch_hash']=sha256_json(patch)
    return {**converted,'source_ttl':source,'patch':patch}
