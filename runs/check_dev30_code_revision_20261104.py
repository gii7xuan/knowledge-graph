"""Offline display/source-unit replay against the frozen development population."""
from collections import Counter
from importlib.util import spec_from_file_location,module_from_spec
import hashlib
import json
from pathlib import Path
from document.hierarchy_index import load_document_index
from evidence.units import enrich_bundle,classify_quote
from review_agent.evidence_view import evidence_view
from review_agent.contracts import sha256_json
from review_agent.validator_contracts import versions_for
from evaluation.review_samples import ReadOnlyRepository
from ontologyBuild.release_service import load_release
from ontologyBuild.ontology_adapter import iter_classes
from schema.relation_registry import RelationRegistry

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/dev30_code_revision_20261104'
OUT.mkdir(parents=True,exist_ok=True)
data=json.loads((ROOT/'outputs/dev30_20261031/sample_data.json').read_text(encoding='utf-8'))
spec=spec_from_file_location('frozen_pilot',ROOT/'runs/prepare_dev30_revision_pilot_20261103.py')
pilot=module_from_spec(spec);spec.loader.exec_module(pilot);integrity=pilot.verify_inputs(data)
repo=ReadOnlyRepository(data['source_database'])
run=repo.get_run(data['selected_raw_candidates'][0]['run_id'])
index=load_document_index(run['document_path'],data['selected_raw_candidates'][0]['source_index_hash'],require_verified=True)
cases=[]
for binding,candidate,model in zip(data['bindings'],data['selected_raw_candidates'],data['model_rows']):
    audit=repo.get_audit(candidate['candidate_id'],binding['audit_id'])
    p=next(p for p in audit['rounds'] if p['proposal_digest']==binding['source_proposal_digest'])
    original_hash=sha256_json(p['evidence_bundle'])
    votes=[v for v in audit['votes'] if v['round_id']==p['round_id']]
    view=evidence_view(p['evidence_bundle'],votes,authoritative=model[3]=='supported')
    enriched=enrich_bundle(p['evidence_bundle'],index,candidate)
    assert sha256_json(p['evidence_bundle'])==original_hash
    assert view['evidence_hash']==p['evidence_bundle']['evidence_hash']
    by_id={x['evidence_id']:x for x in enriched['items']}
    assert all(by_id[x['evidence_id']]['quote']==x['quote'] for x in p['evidence_bundle']['items'])
    primary=view['primary_evidence']
    assert primary is None or classify_quote(primary['quote'])['substantive']
    cases.append({'sample_id':binding['sample_id'],'binding':binding,'legacy_evidence_hash':view['evidence_hash'],
        'enriched_evidence_hash':enriched['evidence_hash'],'main_evidence_status':view['main_evidence_status'],
        'primary_quote':primary['quote'] if primary else None,'counter_evidence_ids':[x['evidence_id'] for x in view['counter_evidence']],
        'visible_body_fragments':[{'evidence_id':x['evidence_id'],'quote':x['quote']} for x in view['body_fragments']],
        'enriched_unit_counts':dict(Counter(x['block_type'] for x in enriched['items'])),
        'raw_bundle_unchanged':True,'original_ids_keep_exact_quotes':True})
affected=[c for c in cases if c['sample_id'] in {'DEV-004','DEV-011','DEV-012','DEV-014','DEV-025'}]
assert len(affected)==5 and all(c['primary_quote']!='$$' and c['visible_body_fragments'] for c in affected)
summary={'samples':len(cases),'main_quote_counts':dict(Counter(c['main_evidence_status'] for c in cases)),
    'five_boundary_quote_cases':affected,'integrity':integrity,'actual_model_calls':0,'production_written':False,
    'new_review_versions':versions_for({'validator_contract_version':'validator_response_v5'}),
    'scope':'development_regression_not_independent_accuracy','new_semantic_reviews_performed':False}
release=load_release(data['selected_raw_candidates'][0]['ontology_release_path'])
catalog=RelationRegistry(release.ontology).reference_catalog()
summary['frozen_ontology_reference']={'release_id':release.manifest['release_id'],
    'classes':len(list(iter_classes(release.ontology))), 'property_counts':catalog['counts'],
    'properties_without_definition':[p['id'] for p in catalog['properties'] if not (p.get('definition') or p.get('description') or p.get('definitions'))],
    'old_release_modified':False}
from rdflib import Graph,RDF,OWL,RDFS
graph=Graph().parse(str(Path(data['selected_raw_candidates'][0]['ontology_release_path'])/'ontology.ttl'),format='turtle')
properties=set(graph.subjects(RDF.type,OWL.ObjectProperty))|set(graph.subjects(RDF.type,OWL.DatatypeProperty))
defined={s for s in properties if any(str(p).rsplit('#',1)[-1].rsplit('/',1)[-1].lower() in {'definition','description','comment','iao_0000115'}
            for p,_ in graph.predicate_objects(s))}
summary['frozen_ontology_reference']['rdf_property_count']=len(properties)
summary['frozen_ontology_reference']['rdf_properties_with_definition_or_description']=len(defined)
(OUT/'offline_replay.json').write_text(json.dumps({'summary':summary,'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'samples':len(cases),'main_quote_counts':summary['main_quote_counts'],
    'five_boundary_quotes_handled':True,'source_workbook_and_production_unchanged':True,'model_calls':0},ensure_ascii=False))
