"""Prepare concrete expression drafts and one immutable shared ontology candidate.

This entry point has no model client, review(), apply(), catalog or publication
call. The candidate bundle cannot be loaded as an approved ontology release.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from zipfile import ZipFile, ZIP_DEFLATED

from .freeze_shared_ontology import ROOT, read, write
from .shared_coverage_review import file_hash
from .shared_expression_repairs import REPAIR_IDS, changes_for
from .shared_extension_model import build_graph, identity, CLASSES
from review_agent.contracts import sha256_json

POOL=ROOT/'outputs/development_pool_20261117'
PRE=ROOT/'outputs/preannotation_followup_20261118'
COVERAGE=ROOT/'outputs/shared_ontology_coverage_20261118'
DISPUTES=ROOT/'outputs/priority_disputes_20261118'
VERSION='shared_extension_candidate_20261118_v2'


def reanchor(sample, source_document, supplement, out, release, extraction):
    from document.snapshot import read_snapshot, make_snapshot
    from document.hierarchy_index import split_markdown_text, build_document_index, persist_document_index, normalize_document_id
    from extract_triplets.pipeline_v4 import source_scope
    from extract_triplets.candidate_contract import normalize_candidate
    from .development_pool import excerpt, original_anchors
    from .preannotation_followup import raw_for_candidate
    c=sample['candidate'];parent=read_snapshot(source_document['parent_path']);text=parent['canonical_text']
    assert parent['document_version']==source_document['parent_document_version']
    ranges={(s['parent_start'],s['parent_end']) for s in source_document['segments']}
    for group in supplement['contexts']:
        for item in group['items']:
            assert item['document_version']==parent['document_version'] and text[item['start']:item['end']]==item['quote']
            ranges.add((item['start'],item['end']))
    # The old excerpt may be a prefix of a full supplemental paragraph. Merge
    # overlapping exact source ranges instead of duplicating or dropping text.
    merged=[]
    for a,b in sorted(ranges):
        if merged and a<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],b)
        else:merged.append([a,b])
    selected=[{'start':a,'end':b,'text':text[a:b],'section':source_document['source_section']} for a,b in merged]
    sid=sample['sample_id'];docid=normalize_document_id(c['document_id']+'_'+sid.lower()+'_extension_context_v1')
    content,segments=excerpt(selected,docid)
    path=out/'sources'/(sid+'.md');path.write_text(content,encoding='utf-8')
    snapshot=make_snapshot(content,docid)
    chunks=split_markdown_text(content,document_id=docid,chunk_size=max(18000,len(content)+1),chunk_overlap=0,snapshot=snapshot)
    assert len(chunks)==1
    index=build_document_index(chunks);index_path=out/'sources'/(sid+'_index.json');persist_document_index(index,index_path)
    scope=source_scope(index,chunks[0],start=0,end=len(content),neighbors=0)
    scope.update(context_start=0,context_end=len(content))
    raw,receipt=raw_for_candidate(c,extraction)
    result=normalize_candidate(raw,index=index,scope=scope,ontology=release.ontology,release_binding=release.binding(),
        run_id=VERSION+':'+sid,ordinal=1,prompt_hash=c['extraction_prompt_hash'],require_semantic_context=True)
    for key in ('head','surface_relation','tail','tail_kind','qualifiers','qualifier_status','value','datatype','unit','language'):
        assert result['triple'].get(key)==c['triple'].get(key),(sid,key)
    result['source_reanchor_origin']={'candidate_id':c['candidate_id'],'revision':c['revision'],
        'candidate_content_hash':sha256_json(c),'initial_evidence_hash':sample['binding']['evidence_hash'],
        'supplement_hash':sha256_json(supplement),'old_labels_or_votes_reusable':False}
    doc={**source_document,'document_id':docid,'path':str(path),'index_path':str(index_path),
        'index_hash':index['index_content_hash'],'document_version':index['document_version'],'scope':scope,'segments':segments}
    result['original_source_anchors']=original_anchors(result,doc,text)
    return result,index,doc,receipt['attempt_id']


def verify_candidate_bundle(path):
    from ontologyBuild.rdf_artifact import validate_owl_view
    from review_agent.ontology_guard import validate_ontology
    manifest=read(path/'candidate_manifest.json')
    payload={k:v for k,v in manifest.items() if k!='candidate_id'}
    if manifest['candidate_id']!='ontology_candidate_'+sha256_json(payload):raise ValueError('candidate manifest hash mismatch')
    if manifest['status']!='candidate_pending_independent_semantic_review' or manifest['eligible_for_formal_experiment']:
        raise ValueError('candidate cannot silently become approved or experiment-ready')
    for name,expected in manifest['artifacts'].items():
        target=(path/name).resolve()
        if not target.is_relative_to(path.resolve()) or file_hash(target)!=expected:raise ValueError('candidate artifact changed: '+name)
    schema=read(path/'ontology_snapshot.json');profile=read(path/'application_profile.json')
    mapping=read(path/'mapping_report.json');method=read(path/'method_manifest.json')
    validate_owl_view((path/'ontology.ttl').read_text(encoding='utf-8'),schema,profile,read(path/'iri_id_map.json'),mapping,
        base_iri=method.get('source_base_iri'),preserve_lexical=True)
    result=validate_ontology(schema)
    if not result['valid']:raise ValueError('invalid candidate ontology')
    if read(path/'approval.json')['status']!='pending':raise ValueError('unreviewed approval status changed')
    return manifest,result


def run(out):
    from ontologyBuild.release_service import load_release, artifact_files, validation_report
    from ontologyBuild.providers.base import OntologyArtifact
    from ontologyBuild.ontology_adapter import iter_classes,iter_object_properties,iter_datatype_properties
    from ontologyBuild.rdf_artifact import graph_hash
    from ontologyBuild.rdf_graph import parse_turtle
    from review_agent.repository import ReviewRepository
    from review_agent.revision_service import RevisionService
    from evidence.hierarchical_retriever import retrieve_initial,_evidence
    from evidence.verification import make_evidence_bundle,verify_evidence_bundle
    from .review_samples import ReadOnlyRepository
    if out.exists() or not out.is_relative_to(ROOT/'outputs'):raise ValueError('new isolated output required')
    protocol=read(COVERAGE/'protocol.json');base=load_release(protocol['release']['ontology_release_path'])
    assert base.binding()==protocol['release']
    review=read(COVERAGE/'candidate_reviews.json')['rows'];samples=read(POOL/'pilot/review_samples.json')['samples']
    by_id={s['sample_id']:s for s in samples}
    expected={int(r['sample_id'][-3:]) for r in review if r['prerequisite_route']=='expression_scope_first'}
    assert expected==set(REPAIR_IDS)
    families=read(COVERAGE/'extension_requirements.json')['families']
    model=build_graph(base,families)
    watched=read(COVERAGE/'baseline.json')['watched_hashes']
    watched={**watched,**{str(p):file_hash(p) for p in COVERAGE.iterdir() if p.is_file()}}
    watched.update({str(DISPUTES/'source_visual_addendum.json'):file_hash(DISPUTES/'source_visual_addendum.json')})
    assert all(file_hash(p)==h for p,h in watched.items())
    out.mkdir(parents=True);(out/'sources').mkdir();bundle_dir=out/'ontology_candidate';bundle_dir.mkdir()
    write(out/'baseline.json',{'watched_hashes':watched})
    write(out/'protocol.json',{'version':VERSION,'base_release':base.binding(),'expression_cohort':list(REPAIR_IDS),
        'additional_literal_representation_case':11,'new_api_requests':0,'no_reuse_of_prior_votes':True,
        'approval_status':'not_requested_or_fabricated','all_methods_share_one_candidate':True,
        'original_experiment_inputs_not_rebound':True,'production_activation':False})
    repo=ReviewRepository(out/'review.sqlite3');repo.initialize();service=RevisionService(repo)
    extraction=ReadOnlyRepository(POOL/'extraction.sqlite3')
    documents={d['document_id']:d for d in read(POOL/'source_manifest.json')['documents']}
    supplements=read(PRE/'source_supplements.json');drafts=[]
    visual=read(DISPUTES/'source_visual_addendum.json')
    for n in (*REPAIR_IDS,11):
        sid=f'PILOT-{n:03d}';sample=by_id[sid]
        candidate,index,doc,receipt=reanchor(sample,documents[sample['candidate']['document_id']],supplements[sid],out,base,extraction)
        repo.register_v4_run({'run_id':candidate['run_id'],'document_path':doc['index_path'],'source_index_hash':doc['index_hash'],
            'source_hash':doc['document_version'],'ontology_hash':base.manifest['ontology_version'],'mode':'shadow',**base.binding()},[candidate])
        initial=retrieve_initial(candidate,index,max_items=8);chunk=index['chunks'][0]
        evidence=verify_evidence_bundle(make_evidence_bundle([*initial['items'],_evidence(chunk,0,len(chunk['content']),'current_chunk')],
            ['frozen_initial_quotes_and_exact_parent_supplements','current_chunk']),index,candidate)
        ids=[e['evidence_id'] for e in evidence['items']]
        patch=changes_for(n,candidate)
        draft=service.propose(candidate,repo.get_candidate(candidate['candidate_id'])['row_version'],patch,ids,
            'Concrete source-bound engineering revision following the 40-case coverage audit; not human gold. '+sid,
            evidence,index,protected_ids=ids)
        assert draft['status']=='proposed' and draft['after']['triple']['relation_id'] is None
        proof=None
        if n in (11,36):
            proof=visual[sid]
            assert proof['original_binding']==sample['binding'] and file_hash(proof['path'])==proof['sha256']
            assert all(file_hash(image['path'])==image['sha256'] for image in proof['images'])
        drafts.append({'sample_id':sid,'original_binding':sample['binding'],'source_document':doc,
            'original_extraction_attempt_id':receipt,'reanchored_candidate':candidate,'draft':draft,
            'pdf_visual_evidence_sidecar':proof,'pdf_visual_review_required':proof is not None,
            'pdf_sidecar_is_not_registered_text_evidence':True,'approved':False,'applied':False})
    write(out/'expression_drafts.json',drafts)
    class_identity=[]
    for sid,key,aliases in (
        ('PILOT-035','StateEquation',['通用物质状态方程','equation of state']),
        ('PILOT-037','ContinuityEquation',['连续方程','质量守恒方程','continuity equation','equation of conservation of mass'])):
        class_identity.append({'sample_id':sid,'original_binding':by_id[sid]['binding'],'canonical_candidate_class_id':identity(key)[1],
            'canonical_label':CLASSES[key][0],'definition':CLASSES[key][1],'aliases':aliases,
            'source_anchors':by_id[sid]['original_source_anchors'],
            'decision':'distinct_generic_source_concept_preserve_old_application_nodes' if key=='StateEquation' else 'one_source_concept_with_attested_aliases',
            'old_nodes_not_merged':['C_eaa767de9c851cd73be7','C_b944bb2feba8240d5c86'] if key=='StateEquation' else [],
            'existing_definition_or_hierarchy_changed':False,'owl_same_as_or_equivalent_class_added':False,
            'instance_class_distinction_preserved':True,'semantic_approval':'pending','human_gold':False})
    write(out/'class_identity_decisions.json',class_identity)
    requirements={'base_release':base.binding(),'coverage_audit_version':protocol['version'],
        'source_requirement_families':families,'candidate_class_definitions':model['new_classes'],
        'candidate_property_definitions':model['new_properties'],
        'changed_modeling_decisions':[{'sample_id':'PILOT-015','before':'causesInContext provisional signature',
            'after':'hasComparativeClaim','reason':'Retain the source comparison whole without presuming independently distributable causal assertions.'}],
        'class_identity_decisions':class_identity,'approved':False}
    competent=[]
    for row in review:
        sid=row['sample_id'];n=int(sid[-3:]);props=[p for p in model['new_properties'] if sid in p['source_sample_ids']]
        if n==15:props=[p for p in props if p['key']=='hasComparativeClaim']
        if n==34:props=[p for p in props if p['key']=='hasCauseInContext']
        competent.append({'sample_id':sid,'original_binding':row['binding'],'question':row['needed_meaning'],
            'candidate_property_ids':[p['id'] for p in props],
            'route':'class_identity_and_aliases' if n in (35,37) else 'scoped_statement_representation',
            'preservation_obligations':row['reason_and_preservation_requirements'],
            'source_quote':row['extraction_quote'],'source_anchors':row['source_anchors'],
            'structural_route_present':bool(props) or n in (35,37),'semantic_competency_verdict':'pending',
            'formal_mapping_assigned':False})
    assert len(competent)==40 and all(c['structural_route_present'] for c in competent)
    converted=model['converted'];schema=converted['project_schema']
    method={'method':VERSION,'base_release_id':base.manifest['release_id'],'rdf_parse_version':'lexical_turtle_v1',
        'source_sha256':hashlib.sha256(model['turtle'].encode('utf-8')).hexdigest(),
        'source_graph_hash':graph_hash(parse_turtle(model['turtle'])),
        'source_base_iri':read(base.path/'method_manifest.json').get('source_base_iri'),
        'review_stage':'candidate_pending_independent_semantic_review','human_confirmed':False,
        'source_modeling_definition_is_not_empirical_fact_approval':True,'all_methods_use_one_candidate':True}
    artifact=OntologyArtifact(project_schema=schema,method_manifest=method,
        validation_report={'domain_semantic_review':{'status':'pending'},'competency_checks':{'status':'questions_prepared_not_semantically_approved'}},
        mapping_report=converted['mapping_report'],application_profile=converted['application_profile'],
        iri_id_map=converted['iri_id_map'],source_ttl=model['turtle'],requirements=requirements,competency_questions=competent)
    validation=validation_report(artifact)
    assert validation['project_validation']['valid'] and not converted['application_profile']['auto_review_blocked']
    approval={'status':'pending','approved_by':None,'reason':'Prepared shared candidate, no semantic approval or release publication has occurred.'}
    for name,content in artifact_files(artifact,approval).items():(bundle_dir/name).write_bytes(content)
    write(bundle_dir/'class_identity_decisions.json',class_identity)
    write(bundle_dir/'schema_delta.json',{'base_release':base.binding(),'new_classes':model['new_classes'],
        'new_properties':model['new_properties'],'added_triple_count':model['added_triple_count'],
        'removed_triple_count':0,'all_base_schema_objects_identical':True,'all_base_rdf_triples_retained':True})
    write(bundle_dir/'model_source_proofs.json',{'base_release':base.binding(),
        'definitions_are_engineering_modeling_proposals_not_empirical_law_certifications':True,
        'nodes':[{'schema_id':node['id'],'key':node['key'],'definition':node['definition'],
            'semantic_approval':'pending','source_proofs':[{'sample_id':sid,'binding':by_id[sid]['binding'],
                'quote':by_id[sid]['candidate']['extraction_quote'],'anchors':by_id[sid]['original_source_anchors']}
                for sid in node['source_sample_ids']]} for node in [*model['new_classes'],*model['new_properties']]]})
    payload={'candidate_bundle_version':'shared_ontology_candidate_v1','status':'candidate_pending_independent_semantic_review',
        'base_release_id':base.manifest['release_id'],'ontology_version':sha256_json(schema),
        'profile_hash':sha256_json(converted['application_profile']),'eligible_for_formal_experiment':False,
        'artifacts':{p.name:file_hash(p) for p in sorted(bundle_dir.iterdir()) if p.is_file()}}
    manifest={**payload,'candidate_id':'ontology_candidate_'+sha256_json(payload)}
    write(bundle_dir/'candidate_manifest.json',manifest)
    verify_candidate_bundle(bundle_dir)
    with ZipFile(out/'shared_ontology_candidate.zip','x',ZIP_DEFLATED) as archive:
        for p in sorted(bundle_dir.iterdir()):archive.write(p,p.name)
    write(out/'handoff.json',{'candidate_id':manifest['candidate_id'],'candidate_manifest':str(bundle_dir/'candidate_manifest.json'),
        'base_release':base.binding(),'all_methods_must_bind_the_same_candidate_hash':True,
        'candidate_is_not_a_published_release':True,'old_votes_reusable':False,
        'expression_after_hashes':{r['sample_id']:sha256_json(r['draft']['after']) for r in drafts},
        'formal_mapping_and_type_assignment_required_after_semantic_approval':True,
        'unknown_qualifier_fields_remain_blocking_not_inferred':True})
    with repo.connect() as con:
        counts={name:con.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in
            ('request_attempts','revision_votes','validator_votes','jobs','import_outbox','graph_outbox','ontology_publish_outbox')}
        statuses=dict(con.execute('SELECT status,COUNT(*) FROM revision_drafts GROUP BY status'))
        assert con.execute('SELECT MAX(current_revision) FROM candidates').fetchone()[0]==1
    assert all(v==0 for v in counts.values()) and statuses=={'proposed':21}
    unchanged={p:file_hash(p)==h for p,h in watched.items()};assert all(unchanged.values())
    with sqlite3.connect((DISPUTES/'review.sqlite3').as_uri()+'?mode=ro',uri=True) as con:
        assert con.execute('SELECT COUNT(*) FROM request_attempts').fetchone()[0]==2
        assert con.execute("SELECT COUNT(*) FROM request_attempts WHERE state='unknown'").fetchone()[0]==1
    summary={'version':VERSION,'expression_scope_cases':20,'additional_literal_draft':1,'revision_draft_status_counts':statuses,
        'class_identity_decisions':len(class_identity),'new_classes':len(model['new_classes']),
        'new_object_properties':sum(p['kind']=='object_property' for p in model['new_properties']),
        'new_datatype_properties':sum(p['kind']=='datatype_property' for p in model['new_properties']),
        'total_classes':len(list(iter_classes(schema))),'total_object_properties':len(list(iter_object_properties(schema))),
        'total_datatype_properties':len(list(iter_datatype_properties(schema))),
        'candidate_id':manifest['candidate_id'],'candidate_bundle_validation_passed':True,
        'base_classes_properties_and_rdf_preserved':True,'competency_routes_prepared':len(competent),
        'competency_semantic_reviews_approved':0,'new_api_requests':0,'repository_counts':counts,
        'revisions_applied':0,'formal_mappings_assigned':0,'human_gold_count':0,'production_written':False,
        'ontology_published':False,'old_unknown_requests_resent':False,'all_input_hashes_unchanged':True,
        'input_hash_checks':unchanged,'readiness':'candidate_prepared_semantic_review_pending'}
    write(out/'verification.json',summary)
    fingerprints={str(p.relative_to(ROOT)):file_hash(p) for p in (ROOT/'evaluation/shared_expression_repairs.py',
        ROOT/'evaluation/shared_extension_model.py',ROOT/'evaluation/shared_extension_version.py')}
    write(out/'code_fingerprints.json',fingerprints)
    print(json.dumps({k:v for k,v in summary.items() if k!='input_hash_checks'},ensure_ascii=False))


if __name__=='__main__':
    run(ROOT/'outputs/shared_extension_20261118')
