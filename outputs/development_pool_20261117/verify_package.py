"""Verify source provenance, frozen population, blind blanks and actual request costs."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import sys
from zipfile import ZipFile
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from evaluation.freeze_shared_ontology import read,write
from evaluation.review_samples import ReadOnlyRepository
from evaluation.development_pool import original_anchors,tags
from evaluation.manifest import validate_manifest
from evaluation.pilot_annotations import convert
from document.snapshot import read_snapshot
from document.hierarchy_index import load_document_index
from review_agent.contracts import sha256_json,proposal_digest,fact_key_for
from review_agent.annotation_contract import import_annotation,training_record,DIMENSIONS
from review_agent.validator_contracts import prompt_for
from ontologyBuild.release_service import load_release

out=Path(__file__).resolve().parent
baseline=read(out/'baseline.json')
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in baseline['watched_hashes'].items())
hashes={name.replace('\\','/'):value for name,value in read(out/'execution_code_hashes.json').items()}
with ZipFile(out/'execution_code.zip') as z:
    assert all(hashlib.sha256(z.read(name)).hexdigest()==h for name,h in hashes.items())
# Extraction/worker code and actual dispatched prompt are unchanged. Packaging was corrected afterward, with no requests.
for name in ('extract_triplets/audited_requests.py','review_agent/request_worker.py','outputs/development_pool_20261117/extraction_prompt.txt'):
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==hashes[name]
with ReadOnlyRepository(out/'extraction.sqlite3').connect() as c:
    receipts=[dict(r) for r in c.execute('SELECT * FROM request_attempts')]
assert len(receipts)==17 and not any(r['state'] in {'running','unknown'} for r in receipts)
results=[json.loads(r['result_json']) for r in receipts]
assert all(v['transport_metrics']['http_status']==200 for v in results)
assert all(json.loads(r['metadata_json'])['task_type']=='source_candidate_extraction_v1' for r in receipts)
assert all(not v['counts_as_review_vote'] and not v['counts_as_human_label'] for v in results)
documents={d['document_id']:d for d in read(out/'source_manifest.json')['documents']}
parents={d['parent_path']:read_snapshot(d['parent_path'])['canonical_text'] for d in documents.values()}
originals=read(out/'all_extracted_candidates.json');by_id={c['candidate_id']:c for c in originals}
selected=read(out/'candidates.json');manifest=validate_manifest(read(out/'population_manifest.json'))
assert len(originals)==180 and len(selected)==160
assert {(c['candidate_id'],c['revision'],sha256_json(c)) for c in selected}=={
    (c['candidate_id'],c['revision'],c['candidate_content_hash']) for c in manifest['candidates']}
repo=ReadOnlyRepository(out/'candidate_pool.sqlite3');index_cache={}
for c in selected:
    old=by_id[c['extraction_source_binding']['candidate_id']]
    assert c['extraction_source_binding']['candidate_content_hash']==sha256_json(old)
    for k,v in old.items():
        if k not in {'run_id','candidate_id','fact_key'}:assert c[k]==v
    assert c['fact_key']==fact_key_for(c)
    doc=documents[c['document_id']]
    assert original_anchors(c,doc,parents[doc['parent_path']])==c['original_source_anchors']
    if c['document_id'] not in index_cache:
        index_cache[c['document_id']]=load_document_index(doc['index_path'],doc['index_hash'],require_verified=True)
    index=index_cache[c['document_id']]
    for a in c['evidence_items']:assert index['source_snapshot']['canonical_text'][a['start']:a['end']]==a['quote']
    assert repo.get_revision(c['candidate_id'])==c
    run=repo.get_run(c['run_id'])
    assert run['source_index_hash']==c['source_index_hash'] and run['document_path']==doc['index_path']
pilot=read(out/'pilot/review_samples.json');pm=validate_manifest(read(out/'pilot/manifest.json'))
assert len(pilot['samples'])==40 and pm['population_count']==40
release=load_release(read(out/'protocol.json')['fixed_release']['ontology_release_path'])
proposals={p['candidate']['candidate_id']:p for p in read(out/'method_inputs.json')}
for role in ('A','B'):
    blank=read(out/('pilot/reviewer_'+role+'_blank.json'))
    assert blank['manifest_hash']==pm['manifest_hash']
    for s,t in zip(pilot['samples'],blank['annotations']):
        assert t['binding']==s['binding'] and t['sample_id']==s['sample_id']
        assert all(value is None for value in t['raw_annotation']['labels'].values())
        assert not s['votes'] and not s['model_conclusions_visible']
        p=proposals[s['candidate']['candidate_id']]
        assert proposal_digest(p)==p['proposal_digest']
        prompt_for(p)
        assert p['evidence_bundle']==s['evidence_bundle'] and p['candidate']==s['candidate']
        assert s['source_context'] in [e['quote'] for e in p['evidence_bundle']['items']]
        annotation=import_annotation(t['raw_annotation'],s['candidate'],p,adjudication=t['adjudication'],ontology=release.ontology)
        assert annotation['adjudication_status']=='pending'
        assert all(training_record(annotation,dimension) is None for dimension in DIMENSIONS)
workbook=out/'pilot/人工试标模板.xlsx'
roundtrip=convert(workbook,out/'pilot')
assert len(roundtrip['annotations'])==40 and not roundtrip['automatic_gold_created']
assert all(v is None for t in roundtrip['annotations'] for v in t['raw_annotation']['labels'].values())
ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
with ZipFile(workbook) as z:
    main=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
    validations=main.find('s:dataValidations',ns)
    assert validations is not None and len(validations)==9
    pane=main.find('s:sheetViews/s:sheetView/s:pane',ns)
    assert pane is not None and pane.attrib.get('xSplit')=='2' and pane.attrib.get('ySplit')=='1'
with repo.connect() as c:
    counts={t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('validator_votes','audit_runs','review_events','jobs','import_outbox','graph_outbox','ontology_publish_outbox')}
assert not any(counts.values())
summary=read(out/'intake_summary.json')
summary['sampling_tag_counts']=dict(Counter(t for c in selected for t in tags(c)))
summary['contract_issue_counts']=dict(Counter(e for c in selected for e in c.get('contract_issues',[])))
summary['candidates_with_contract_issues']=sum(bool(c.get('contract_issues')) for c in selected)
summary['unmapped_is_not_a_fact_error']=True
(out/'intake_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
report={'verification_passed':True,'natural_candidates':180,'frozen_development_candidates':160,'pilot_candidates':40,
    'source_books':2,'source_fragments_prepared':len(documents),'source_runs_in_pool':len({c['run_id'] for c in selected}),
    'pilot_source_book_counts':dict(Counter(s['source_group_id'] for s in pilot['samples'])),
    'language_counts':summary['language_counts'],'human_gold_count':0,'initial_human_model_evidence_identical':True,
    'blank_workbook_roundtrip_passed':True,'native_validation_rules':9,'row_and_identity_column_panes_verified':True,
    'candidate_parent_source_and_manifest_bindings_verified':True,'independent_test_created':False,
    'extraction_calls':17,'http200':17,'schema_valid_extraction_responses':sum(v['response_status']=='ok' for v in results),
    'known_invalid_extraction_responses':sum(v['response_status']!='ok' for v in results),'new_unknown':0,'new_http504':0,
    'known_input_tokens':sum(v.get('input_tokens')or 0 for v in results),
    'known_output_tokens':sum(v.get('output_tokens')or 0 for v in results),
    'price':None,'review_model_calls':0,'packaging_calls':0,'old_unknown_reissues':0,
    'historical_databases_and_production_head_unchanged':True,'source_files_unchanged':True,
    'initial_execution_code_archived':True,'separate_packaging_derivation':'document_run_binding_only_v1',
    'candidate_semantics_changed_by_packaging':0,'unassigned_formal_relations':160,
    'contract_issue_candidates':summary['candidates_with_contract_issues'],
    'repository_counts':counts,'production_written':False,'paper_accuracy':None,
    'regression_log':'final_checks.log'}
write(out/'verification_final.json',report)
print(json.dumps(report,ensure_ascii=False))
