"""Deterministic RDF graph encoding and source/view integrity checks.

The canonicalization algorithm is RDFLib's graph canonicalizer, not RDFC-1.0.
"""
import hashlib
from rdflib import Literal,Graph
from rdflib.compare import to_canonical_graph,isomorphic
from rdflib.plugins.serializers.nt import _quoteLiteral
from .owl_to_project_schema import convert_turtle
from .rdf_graph import parse_turtle


def canonical_turtle(graph):
    canonical=to_canonical_graph(graph)
    # Sorted canonical N-Triples is also legal Turtle and avoids serializer order.
    return ''.join(sorted(statement((s,p,o))+'\n' for s,p,o in canonical))


def statement(triple):
    return ' '.join(_quoteLiteral(term) if isinstance(term,Literal) else term.n3() for term in triple)+' .'


def graph_hash(graph):
    return hashlib.sha256(canonical_turtle(graph).encode('utf-8')).hexdigest()


def validate_owl_view(source,ontology,profile,iri_map,mapping_report,*,base_iri=None,preserve_lexical=True):
    try: converted=convert_turtle(source.lstrip('\ufeff'),iri_id_map=iri_map,base_iri=base_iri,preserve_lexical=preserve_lexical)
    except Exception as exc: raise ValueError('OWL source conversion failed: '+str(exc)) from exc
    expected_profile=dict(converted['application_profile'])
    actual_profile=dict(profile)
    flag=actual_profile.pop('requires_difference_confirmation',None)
    if flag is not None and type(flag) is not bool: raise ValueError('invalid OWL profile confirmation policy')
    if actual_profile != expected_profile: raise ValueError('OWL source/application profile mismatch')
    converted['project_schema']['application_profile']=profile
    if converted['project_schema'] != ontology or converted['iri_id_map'] != iri_map:
        raise ValueError('OWL source/project view or IRI binding mismatch')
    for field in ('errors','unsupported_axioms','example_assertion_count','example_instances_imported_as_facts','semantic_fidelity'):
        if converted['mapping_report'].get(field) != mapping_report.get(field):
            raise ValueError('OWL source/mapping report mismatch: '+field)
    return converted


def validate_patch_bindings(patch,source,iri_map,method):
    from review_agent.contracts import sha256_json
    if (not isinstance(patch,dict) or patch.get('patch_hash')!=sha256_json({k:v for k,v in patch.items() if k!='patch_hash'})
            or patch.get('canonicalization')!='rdflib_to_canonical_graph_sorted_nt_v1'
            or patch.get('source_sha256')!=hashlib.sha256(source.encode('utf-8')).hexdigest()
            or patch.get('graph_hash')!=graph_hash(parse_turtle(source))
            or patch.get('iri_id_map_hash')!=sha256_json(iri_map)
            or method.get('owl_patch_hash')!=patch['patch_hash']
            or method.get('source_graph_hash')!=patch['graph_hash']
            or method.get('source_sha256')!=patch['source_sha256']
            or method.get('base_source_sha256')!=patch.get('base_source_sha256')
            or method.get('compiler_version')!=patch.get('compiler_version')
            or method.get('compiler_hash')!=patch.get('compiler_hash')
            or patch.get('compiler_hash')!=sha256_json(patch.get('compiler_sources'))
            or method.get('delta_hash')!=sha256_json(patch.get('normalized_operations'))):
        raise ValueError('OWL patch/source/IRI/provenance binding mismatch')
    if patch.get('merged_rdf')!=source or patch.get('review_context')!='complete_base_and_result_rdf':
        raise ValueError('missing complete reviewed OWL source context')
    before=parse_turtle(patch['base_rdf'],preserve_bnode_labels=True)
    if graph_hash(before)!=patch['base_graph_hash'] or canonical_turtle(before)!=patch['base_rdf']:
        raise ValueError('OWL base graph context mismatch')
    removed=set(parse_turtle('\n'.join(patch['removed']),preserve_bnode_labels=True))
    added=set(parse_turtle('\n'.join(patch['added']),preserve_bnode_labels=True))
    if not removed<=set(before): raise ValueError('OWL patch deletes statements absent from its base')
    replay=Graph()
    for triple in (set(before)-removed)|added: replay.add(triple)
    if not isomorphic(replay,parse_turtle(source)): raise ValueError('OWL source does not equal its recorded RDF delta')
    if len(before)!=patch['before_triple_count'] or len(replay)!=patch['after_triple_count']:
        raise ValueError('OWL patch graph accounting mismatch')
