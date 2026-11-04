"""Lossless RDF delivery with a small derived-schema index, not duplicate views."""
from copy import deepcopy
from ontologyBuild.rdf_graph import parse_turtle
from ontologyBuild.rdf_artifact import graph_hash
from .delta import catalog

PAYLOAD_VERSION='ontology_validator_payload_v2'
PATCH_PAYLOAD_VERSION='ontology_validator_payload_v3'


def compact_rdf(source,expected_hash):
    graph=parse_turtle(source)
    if graph_hash(graph)!=expected_hash: raise ValueError('frozen RDF graph hash differs from review source')
    compact=graph.serialize(format='turtle')
    if graph_hash(parse_turtle(compact))!=expected_hash:
        return source
    return compact if len(compact)<len(source) else source


def schema_index(schema):
    keys=('id','label','iri','source_iri','schema_kind','domain','range','parent','parents','datatype','datatypes','aliases')
    return {'domain':schema.get('domain'),'application_profile':schema.get('application_profile'),
        'complete_schema_identity_index':True,
        'items':[{k:item[k] for k in keys if k in item} for item in catalog(schema).values()],
        'definitions_annotations_and_all_axioms':'See the complete base_rdf / merged_rdf graphs; this is a derived identity index.'}


def validator_payload(proposal, *, delivery_version=None):
    payload=deepcopy({k:v for k,v in proposal.items() if k not in {'audit_id','round_id'}})
    delivery=delivery_version or proposal.get('versions',{}).get('payload_version')
    if delivery not in {PAYLOAD_VERSION,PATCH_PAYLOAD_VERSION}:
        return payload  # Frozen legacy requests keep their original complete delivery.
    payload['validator_payload_version']=delivery
    patch=payload.get('source_patch')
    if patch:
        patch['base_rdf']=compact_rdf(patch['base_rdf'],patch['base_graph_hash'])
        patch['merged_rdf']=compact_rdf(patch['merged_rdf'],patch['graph_hash'])
        patch['rdf_delivery']='complete_graphs_losslessly_serialized_as_turtle'
        patch['graph_hashes_verified_before_delivery']=True
        payload['base_schema']=schema_index(proposal['base_schema'])
        merged=schema_index(proposal['merged_schema'])
        before={v['id']:v for v in payload['base_schema']['items']}
        after={v['id']:v for v in merged['items']}
        payload['merged_schema']={'complete_identity_index_from':'base_schema.items + changed_items - removed_ids',
            'changed_items':[v for identity,v in after.items() if before.get(identity)!=v],
            'removed_ids':sorted(set(before)-set(after)),'item_count':len(after),
            'application_profile':merged['application_profile'],
            'definitions_annotations_and_all_axioms':merged['definitions_annotations_and_all_axioms']}
        if delivery==PATCH_PAYLOAD_VERSION:
            from rdflib import BNode
            base=parse_turtle(patch['base_rdf'])
            added=parse_turtle('\n'.join(patch.get('added',[])),preserve_bnode_labels=True)
            removed=parse_turtle('\n'.join(patch.get('removed',[])),preserve_bnode_labels=True)
            # Anonymous-node identity across serialized graphs is not inferred.
            if any(isinstance(term,BNode) for graph in (added,removed) for triple in graph for term in triple):
                patch['patch_delivery_fallback']='anonymous_nodes_in_patch_require_both_complete_graphs'
            else:
                if any(triple not in base for triple in removed):raise ValueError('RDF patch removes absent base triples')
                reconstructed=base-removed+added
                if graph_hash(reconstructed)!=patch['graph_hash']:raise ValueError('lossless RDF patch does not reproduce frozen merged graph')
                patch['merged_rdf']={'encoding':'complete_base_minus_removed_plus_added_v1',
                    'base':'source_patch.base_rdf','remove':'source_patch.removed','add':'source_patch.added',
                    'rdf_statement_format':'Turtle-compatible N-Triples; literals preserve language and lexical datatype',
                    'verified_graph_hash':patch['graph_hash'],'verified_triple_count':len(reconstructed),
                    'instruction':'This represents the entire merged RDF graph, not a summary. Read every base axiom plus the exact additions/removals. All unmodified anonymous axioms and annotations remain in base_rdf.'}
                patch['rdf_delivery']='complete_base_with_graph_hash_verified_lossless_named_patch'
    return payload
