"""Bounded, durable extraction from an already prepared descriptive source plan."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import time

from .freeze_shared_ontology import ROOT,read,write
from .formal_preparation import VERSION,merge_overrides
from .request_meter import RequestMeter,StudyBudget
from review_agent.contracts import sha256_json


def select_candidates(rows,count):
    groups=defaultdict(list)
    for row in rows:groups[(row['source_group_id'],row['source_section'])].append(row)
    for group in groups.values():group.sort(key=lambda c:sha256_json([VERSION,c['candidate_id']]))
    ordered=[]
    while any(groups.values()) and len(ordered)<count:
        for key in sorted(groups,key=lambda k:sha256_json([VERSION,k])):
            if groups[key] and len(ordered)<count:ordered.append(groups[key].pop(0))
    return ordered


def run(directory):
    from runs.regress_v8 import configuration
    from review_agent.cli import _configured_validators
    from review_agent.repository import ReviewRepository
    from review_agent.request_execution import judge_typed
    from review_agent.validators import _parse_content
    from extract_triplets.audited_requests import VERSION as TASK,binding,judge_once
    from extract_triplets.candidate_contract import normalize_candidate
    from document.hierarchy_index import load_document_index
    from document.snapshot import read_snapshot
    from ontologyBuild.release_service import load_release
    from .development_pool import original_anchors
    from .manifest import freeze_manifest,save_manifest
    from .annotations import annotation_template
    from .split_integrity import leakage_groups,verify_split
    directory=Path(directory).resolve()
    if not directory.is_relative_to(ROOT/'outputs'):raise ValueError('isolated outputs directory required')
    if (directory/'extraction_started.json').exists():raise ValueError('already started; inspect original receipts, never relaunch')
    protocol=read(directory/'protocol.json');policy=read(directory/'sampling_policy.json')
    frozen=read(directory/'code_fingerprints.json')
    if any(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest for name,digest in frozen.items()):
        raise ValueError('prepared code changed; create a new preparation')
    proposals=read(directory/'extraction_proposals.json')
    for proposal in proposals:
        if sha256_json({k:v for k,v in proposal.items() if k!='proposal_digest'})!=proposal['proposal_digest']:
            raise ValueError('extraction proposal hash mismatch')
    request_cap=min(len(proposals),policy['extraction_request_cap'])
    limits={'llm_calls':request_cap,'known_tokens':policy['extraction_known_token_stop'],'seconds':3600}
    study=StudyBudget(limits);meter=RequestMeter(directory/'extraction_request_journal.jsonl',
        {'llm_calls':1,'tokens':120000,'seconds':180},study)
    config=merge_overrides(configuration(profile='weighted_diagnostic',directory=directory),read(directory/'config_overrides.json'))
    voter=next(v for v in _configured_validators(config) if v['model_family']=='deepseek')
    voter.update(id='extractor',max_output_tokens=9000,http_timeout_seconds=180,request_timeout_seconds=180)
    release=load_release(protocol['initial_release']['ontology_release_path'])
    docs={d['document_id']:d for d in read(directory/'extraction_documents.json')}
    parent_cache={};indexes={}
    # Verify original source identity and every source paragraph before dispatch.
    for doc in docs.values():
        parent=parent_cache.setdefault(doc['parent_path'],read_snapshot(doc['parent_path']))
        if parent['document_version']!=doc['parent_document_version']:raise ValueError('original source changed')
        index=load_document_index(doc['index_path'],doc['index_hash'],require_verified=True)
        for segment in doc['segments']:
            if parent['canonical_text'][segment['parent_start']:segment['parent_end']]!=index['source_snapshot']['canonical_text'][segment['excerpt_start']:segment['excerpt_end']]:
                raise ValueError('retained paragraph differs from original book')
        indexes[doc['document_id']]=index
    repository=ReviewRepository(directory/'extraction.sqlite3');repository.initialize()
    write(directory/'extraction_execution_code.json',{'prepared_code_hash':sha256_json(frozen),
        'launcher_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'source_plan_hash':sha256_json(proposals)})
    write(directory/'extraction_started.json',{'time':time.time(),'limits':limits,'retries':0,
        'stop_on_unknown':True,'main_review_started':False,'automatic_relaunch':False})
    (directory/'extraction_receipts').mkdir();all_rows=[];rejected=[];duplicates=[];seen=set();stop=None
    for proposal in proposals[:request_cap]:
        if study.snapshot()['blocked_reason']:stop=study.snapshot()['blocked_reason'];break
        doc=docs[proposal['document_id']];index=indexes[doc['document_id']]
        current={**voter,'client':meter.wrap(voter['client'],'candidate_extraction',[doc['document_id']])}
        result=judge_typed(current,proposal,repository,task_type=TASK,binding=binding(current,proposal),
            invoke=lambda feedback:judge_once(current,proposal,feedback),
            validate_cached=lambda v:[] if v.get('response_status')=='ok' else ['invalid extraction response'],
            prompt_hash=sha256_json(proposal['system_prompt']),retries=0,timeout=180,deadline_epoch=time.time()+180)
        write(directory/'extraction_receipts'/(doc['document_id']+'.json'),result)
        meter.snapshot()
        if result.get('outcome_unknown') or result.get('pause_job') or study.snapshot()['blocked_reason']:
            stop='request_unknown_service_pause_or_budget_stop';break
        if result.get('response_status')!='ok':
            rejected.append({'task_id':proposal['task_id'],'response_status':result.get('response_status')})
        else:
            for ordinal,raw in enumerate(_parse_content(result['raw_response'])['triplets']):
                try:
                    c=normalize_candidate(raw,index=index,scope=doc['scope'],ontology=release.ontology,
                        release_binding=release.binding(),run_id='formal_extract:'+doc['document_id'],ordinal=ordinal,
                        prompt_hash=hashlib.sha256(proposal['system_prompt'].encode()).hexdigest(),require_semantic_context=True)
                    anchors=original_anchors(c,doc,parent_cache[doc['parent_path']]['canonical_text'])
                    signature=sha256_json([doc['source_group_id'],c['triple'],c['evidence_quotes']])
                    if signature in seen:duplicates.append({'document_id':doc['document_id'],'raw':raw});continue
                    seen.add(signature)
                    c.update(source_group_id=doc['source_group_id'],source_section=doc['source_section'],
                        original_source_anchors=anchors,extraction_model=voter['model_id'],
                        extraction_attempt_id=result.get('attempt_id'),split_role='independent_test',independent_human_gold=False)
                    all_rows.append(c)
                except (ValueError,TypeError,KeyError) as exc:
                    rejected.append({'document_id':doc['document_id'],'raw':raw,'reason':str(exc)})
        print(json.dumps({'document':doc['document_id'],'response_status':result.get('response_status'),
            'natural_candidates':len(all_rows),'physical_requests':study.snapshot()['physical_calls'],
            'known_tokens':study.snapshot()['known_tokens']},ensure_ascii=False),flush=True)
    development=read(ROOT/'outputs/development_pool_20261117/candidates.json')
    development+=read(ROOT/'outputs/dev30_20261031/sample_data.json')['selected_raw_candidates']
    prior_ids={(c['candidate_id'],c['revision']) for c in development}
    leaked=set()
    for group in leakage_groups(development+all_rows):
        if any(item in prior_ids for item in group):leaked.update(item for item in group if item not in prior_ids)
    eligible=[c for c in all_rows if (c['candidate_id'],c['revision']) not in leaked]
    chosen=select_candidates(eligible,protocol['target_population'])
    roles={(c['candidate_id'],c['revision']):'development' for c in development}
    roles.update({(c['candidate_id'],c['revision']):'independent_test' for c in chosen})
    split=verify_split(development+chosen,roles)
    write(directory/'all_extracted_candidates.json',all_rows);write(directory/'intake_rejections.json',rejected)
    write(directory/'exact_duplicates.json',duplicates);write(directory/'leakage_exclusions.json',[list(k) for k in sorted(leaked)])
    write(directory/'candidates.json',chosen);write(directory/'extraction_requests.json',meter.snapshot())
    manifest=freeze_manifest(chosen,run_id=VERSION,settings={'target':protocol['target_population'],
        'selection_uses_labels':False,'source_plan_hash':sha256_json(proposals),'split_integrity':split})
    save_manifest(manifest,directory/'population_manifest.json')
    write(directory/'human_annotations_blank.json',annotation_template(manifest))
    summary={'status':'source_candidates_extracted_review_inputs_not_yet_frozen','target':protocol['target_population'],
        'natural_candidates':len(all_rows),'selected_candidates':len(chosen),'leakage_excluded':len(leaked),
        'source_group_counts':dict(Counter(c['source_group_id'] for c in chosen)),
        'unassigned_formal_relation_count':sum(not c['triple'].get('relation_id') for c in chosen),
        'candidates_with_contract_issues':sum(bool(c.get('contract_issues')) for c in chosen),
        'requests':study.snapshot(),'stop_reason':stop or 'prepared_scopes_exhausted',
        'currency_cost':None,'human_gold_labels':0,'main_review_started':False,'production_written':False}
    write(directory/'extraction_summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepared',required=True)
    parser.add_argument('--run',action='store_true');args=parser.parse_args()
    if not args.run:raise ValueError('explicit --run is required for provider requests')
    if read(Path(args.prepared)/'protocol.json').get('extraction_delivery_policy')=='small_source_streaming_v2':
        from .extraction_chain_v2 import run as run_streaming
        run_streaming(args.prepared)
    else:run(args.prepared)


if __name__=='__main__':main()
