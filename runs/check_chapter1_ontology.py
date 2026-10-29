"""Verify the delivered draft, frozen preview and original-book evidence."""
import hashlib
import json
from pathlib import Path
import sys
from rdflib import Graph, RDF, OWL, RDFS
from rdflib.compare import isomorphic
from ontologyBuild.providers.base import OntologyArtifact
from ontologyBuild.release_service import digest, artifact_files
from ontologyBuild.release_catalog import ReleaseCatalog

ROOT=Path(__file__).resolve().parents[1]
RUN=Path(sys.argv[1]).resolve()


def read(path): return json.loads(path.read_text(encoding='utf-8'))


def main():
    report=read(RUN/'chapter1_generation_report.json')
    preview=read(RUN/'chapter1_import_preview.json')
    frozen=Path(report['draft_path'])
    receipt=read(frozen/'draft_receipt.json')
    payload=read(frozen/'import_artifact.json')
    artifact=OntologyArtifact(**payload)
    assert digest(payload)==receipt['artifact_digest']==preview['artifact_digest']
    assert digest(artifact.project_schema)==receipt['ontology_version']
    for name,expected in artifact_files(artifact,{'status':'pending'}).items():
        assert (frozen/name).read_bytes()==expected, name
    graph=Graph().parse(RUN/'chapter1_ontology_draft.ttl',format='turtle')
    imported=Graph().parse(frozen/'ontology.ttl',format='turtle')
    assert isomorphic(graph,imported)
    classes=set(graph.subjects(RDF.type,OWL.Class))
    properties=set(graph.subjects(RDF.type,OWL.ObjectProperty))
    assert len(classes)==report['class_count']
    assert len(properties)==report['object_property_count']
    assert not list(graph.subjects(RDF.type,OWL.DatatypeProperty))
    assert len(list(graph.subject_objects(RDFS.subClassOf)))==report['subclass_edge_count']
    for child,parent in graph.subject_objects(RDFS.subClassOf):
        assert child in classes and parent in classes
    for prop in properties:
        assert all(c in classes for c in graph.objects(prop,RDFS.domain))
        assert all(c in classes for c in graph.objects(prop,RDFS.range))
    def visit(node,path):
        assert node not in path,'hierarchy_cycle'
        for parent in graph.objects(node,RDFS.subClassOf): visit(parent,path|{node})
    for node in classes: visit(node,set())
    provenance=read(RUN/'chapter1_provenance.json')
    assert {r['iri'] for r in provenance}=={str(n) for n in classes|properties}
    full=read(ROOT/'runs/ontology_chapter1_inputs/full_source_index.json')['source_snapshot']['canonical_text']
    for row in provenance:
        for a in row['book_source_anchors']:
            if a['anchor_valid']: assert full[a['start']:a['end']]==a['quote']
    source=report['chapter_source']
    assert hashlib.sha256(Path(source['source_path']).read_bytes()).hexdigest()==source['source_raw_sha256']
    assert read(frozen/'approval.json')['status']=='pending'
    assert report['semantic_review']=='pending' and report['reasoner']=='not_run'
    assert report['published'] is False and ReleaseCatalog(ROOT/'runs/ontology_releases').current() is None
    statuses=read(RUN/'raw_responses/chunk_status.json')
    index=read(RUN/'document_index.json')
    assert len(statuses)==report['extraction_stats']['total_chunks']
    covered=set()
    successful={r['chunk_id'] for r in statuses if r['status'] in {'succeeded','succeeded_empty'}}
    for chunk in index['chunks']:
        if chunk['chunk_id'] in successful:
            covered.update(range(chunk['document_start'],chunk['document_end']))
    total=len(index['source_snapshot']['canonical_text'])
    assert all(0<=i<total for i in covered)
    result={'status':'passed','frozen_artifact_integrity':True,'turtle_matches_import':True,
        'hierarchy_acyclic':True,'book_evidence_slices_match':True,'approval':'pending','published':False,
        'successful_chunks':len(successful),'total_chunks':len(statuses),
        'successful_source_characters':len(covered),'total_chapter_characters':total,
        'uncovered_characters':total-len(covered),'class_count':len(classes),'object_property_count':len(properties)}
    (RUN/'chapter1_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
