"""Recover only complete JSON items from one known, truncated original receipt.

This is derived data intake, never a repaired response, new vote or human truth.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from review_agent.contracts import sha256_json
from .freeze_shared_ontology import ROOT,read,write


def complete_prefix_items(raw):
    match=re.match(r'\s*\{\s*"triplets"\s*:\s*\[',raw)
    if not match:raise ValueError('only an exact leading triplets array is supported')
    decoder=json.JSONDecoder();position=match.end();items=[]
    while True:
        while position<len(raw) and raw[position].isspace():position+=1
        if position>=len(raw) or raw[position]==']':break
        start=position
        try:item,position=decoder.raw_decode(raw,position)
        except json.JSONDecodeError:break
        if not isinstance(item,dict):raise ValueError('triplets array must contain objects')
        items.append({'raw':item,'start':start,'end':position,
                      'fragment_sha256':hashlib.sha256(raw[start:position].encode()).hexdigest()})
        while position<len(raw) and raw[position].isspace():position+=1
        if position>=len(raw) or raw[position]==']':break
        if raw[position]!=',':raise ValueError('invalid separator after complete object')
        position+=1
    return items,position


def recover(directory):
    from extract_triplets.candidate_contract import RESPONSE_SCHEMA,normalize_candidate
    from document.hierarchy_index import load_document_index
    from document.snapshot import read_snapshot
    from ontologyBuild.release_service import load_release
    from review_agent.schema_validation import schema_errors
    from .development_pool import original_anchors
    from .formal_extraction import select_candidates
    from .split_integrity import leakage_groups,verify_split
    from .manifest import freeze_manifest,save_manifest
    from .annotations import annotation_template
    directory=Path(directory).resolve();output=directory/'partial_prefix_recovery'
    if not directory.is_relative_to(ROOT/'outputs') or output.exists():raise ValueError('new isolated recovery folder required')
    protocol=read(directory/'protocol.json')
    release=load_release(protocol['initial_release']['ontology_release_path'])
    documents={d['document_id']:d for d in read(directory/'extraction_documents.json')}
    candidates=[];rejections=[];sources=[];fragments=[]
    for p in read(directory/'extraction_proposals.json'):
        receipt_path=directory/'extraction_receipts'/(p['document_id']+'.json')
        if not receipt_path.exists():continue
        receipt=read(receipt_path)
        if receipt.get('finish_reason')!='length' or receipt.get('outcome_unknown') or receipt.get('response_status')!='invalid_contract':continue
        raw=receipt.get('raw_response')
        if not isinstance(raw,str):continue
        rows,stop=complete_prefix_items(raw)
        doc=documents[p['document_id']];index=load_document_index(doc['index_path'],doc['index_hash'],require_verified=True)
        parent=read_snapshot(doc['parent_path'])
        if parent['document_version']!=doc['parent_document_version']:raise ValueError('original source changed')
        for ordinal,row in enumerate(rows):
            try:
                errors=schema_errors(row['raw'],RESPONSE_SCHEMA['properties']['triplets']['items'])
                if errors:raise ValueError('item schema errors: '+str(errors))
                c=normalize_candidate(row['raw'],index=index,scope=doc['scope'],ontology=release.ontology,
                    release_binding=release.binding(),run_id='formal_extract:'+doc['document_id'],ordinal=ordinal,
                    prompt_hash=hashlib.sha256(p['system_prompt'].encode()).hexdigest(),require_semantic_context=True)
                anchors=original_anchors(c,doc,parent['canonical_text'])
                c.update(source_group_id=doc['source_group_id'],source_section=doc['source_section'],
                    original_source_anchors=anchors,extraction_model=receipt['model_id'],
                    extraction_attempt_id=receipt['attempt_id'],split_role='independent_test',independent_human_gold=False,
                    extraction_recovery={'kind':'complete_prefix_item_from_known_truncated_receipt',
                        'original_receipt_sha256':hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                        'raw_fragment_start':row['start'],'raw_fragment_end':row['end'],
                        'raw_fragment_sha256':row['fragment_sha256'],'original_response_status_unchanged':True})
                candidates.append(c);fragments.append({'candidate_id':c['candidate_id'],**row})
            except (ValueError,TypeError,KeyError) as exc:
                rejections.append({'document_id':doc['document_id'],'ordinal':ordinal,'reason':str(exc),**row})
        sources.append({'receipt':str(receipt_path),'receipt_sha256':hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            'original_status':receipt['response_status'],'complete_items':len(rows),'partial_item_or_array_stop':stop,
            'raw_characters':len(raw),'coverage_status':'unknown; truncated response does not establish scope completeness'})
    development=read(ROOT/'outputs/development_pool_20261117/candidates.json')
    development+=read(ROOT/'outputs/dev30_20261031/sample_data.json')['selected_raw_candidates']
    prior={(c['candidate_id'],c['revision']) for c in development};leaked=set()
    for group in leakage_groups(development+candidates):
        if any(item in prior for item in group):leaked.update(item for item in group if item not in prior)
    chosen=select_candidates([c for c in candidates if (c['candidate_id'],c['revision']) not in leaked],protocol['target_population'])
    roles={(c['candidate_id'],c['revision']):'development' for c in development}
    roles.update({(c['candidate_id'],c['revision']):'independent_test' for c in chosen})
    split=verify_split(development+chosen,roles)
    output.mkdir()
    write(output/'source_receipts.json',sources);write(output/'raw_complete_items.json',fragments)
    write(output/'rejected_items.json',rejections);write(output/'all_recovered_candidates.json',candidates)
    write(output/'candidates.json',chosen);write(output/'leakage_exclusions.json',[list(k) for k in sorted(leaked)])
    manifest=freeze_manifest(chosen,run_id='formal_truncated_prefix_recovery',settings={
        'derived_intake_not_a_validated_full_response':True,'new_model_calls':0,'split_integrity':split})
    save_manifest(manifest,output/'population_manifest.json')
    write(output/'human_annotations_blank.json',annotation_template(manifest))
    summary={'status':'derived_complete_items_only_not_a_completed_test_pool','new_model_calls':0,
        'complete_json_items':sum(s['complete_items'] for s in sources),'recovered_candidates':len(candidates),
        'schema_or_anchor_rejected_items':len(rejections),'leakage_excluded':len(leaked),'selected_candidates':len(chosen),
        'unassigned_formal_relation_count':sum(not c['triple'].get('relation_id') for c in chosen),
        'candidates_with_contract_issues':sum(bool(c.get('contract_issues')) for c in chosen),
        'human_gold_labels':0,'semantic_accuracy':None,'main_review_started':False,
        'original_request_statuses_changed':False,'unknown_request_reissued':False}
    write(output/'summary.json',summary)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepared',required=True)
    args=parser.parse_args();print(json.dumps(recover(args.prepared),ensure_ascii=False))


if __name__=='__main__':main()
