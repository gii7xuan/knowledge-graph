"""Small-batch streaming intake with durable provider workers and no automatic resend."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from review_agent.contracts import sha256_json
from .freeze_shared_ontology import ROOT,read,write


def request_usage(repository):
    with repository.connect() as c:
        rows=[(r['state'],json.loads(r['result_json'] or '{}')) for r in c.execute('SELECT state,result_json FROM request_attempts')]
    return {'physical_requests':sum(not r.get('not_sent') for _,r in rows),
        'known_tokens':sum(sum(r.get(k) or 0 for k in ('input_tokens','output_tokens')) for _,r in rows),
        'unknown_requests':sum(state in {'running','unknown'} for state,_ in rows),
        'unknown_usage_requests':sum(not r.get('not_sent') and any(r.get(k) is None for k in ('input_tokens','output_tokens')) for _,r in rows),
        'states':dict(Counter(s for s,_ in rows))}


def intake_items(result,repository):
    """Whole valid responses or explicit visible-prefix derivations; never repair JSON."""
    from review_agent.validators import _parse_content
    from .extraction_prefix_recovery import complete_prefix_items
    if result.get('response_status')=='ok':return _parse_content(result['raw_response'])['triplets'],'complete_response'
    text=result.get('raw_response')
    if not text and result.get('attempt_id'):
        with repository.connect() as c:
            row=c.execute('SELECT detail_json FROM request_attempt_events WHERE attempt_id=? AND event=? ORDER BY at DESC LIMIT 1',
                (result['attempt_id'],'visible_extraction_checkpoint')).fetchone()
        if row:text=json.loads(row[0]).get('visible_content')
    if not text:return [],'no_visible_output'
    try:items,_=complete_prefix_items(text)
    except ValueError:return [],'unsupported_partial_prefix'
    return [r['raw'] for r in items],'complete_visible_prefix_only'


def extraction_voter(config):
    from review_agent.cli import _configured_validators
    from review_agent.model_settings import validator_settings
    config=deepcopy(config)
    for slot in config['validators']:
        if validator_settings(slot)['family']=='deepseek':
            slot.update(stream_responses=True,http_timeout_seconds=180,request_timeout_seconds=180,max_output_tokens=12288)
    voter=next(v for v in _configured_validators(config) if v['model_family']=='deepseek')
    voter.update(id='extractor_v2',max_output_tokens=12288,http_timeout_seconds=180,request_timeout_seconds=180)
    return voter


def extractor_binding(voter):
    client=voter['client']
    return {'model_id':voter['model_id'],'model_family':voter['model_family'],
        'model_identity':voter.get('model_identity') or voter['model_id'],
        'base_url':client.base_url.rstrip('/'),'stream_responses':client.stream_responses,
        'http_timeout_seconds':client.timeout_seconds,'max_output_tokens':voter['max_output_tokens']}


def run(directory):
    from runs.regress_v8 import configuration
    from .formal_preparation import merge_overrides,previous_extraction
    from review_agent.repository import ReviewRepository
    from review_agent.llm_clients import OpenAICompatibleClient
    from review_agent.request_execution import judge_typed
    from extract_triplets.streaming_extraction import VERSION,POLICY,binding,judge_once
    from extract_triplets.candidate_contract import RESPONSE_SCHEMA,normalize_candidate
    from document.hierarchy_index import load_document_index
    from document.snapshot import read_snapshot
    from ontologyBuild.release_service import load_release
    from review_agent.schema_validation import schema_errors
    from .development_pool import original_anchors
    from .formal_extraction import select_candidates
    from .manifest import freeze_manifest,save_manifest
    from .split_integrity import leakage_groups,verify_split
    directory=Path(directory).resolve()
    if not directory.is_relative_to(ROOT/'outputs') or (directory/'extraction_started.json').exists():
        raise ValueError('new isolated execution required; never automatically relaunch')
    protocol=read(directory/'protocol.json');policy=read(directory/'sampling_policy.json')
    if protocol.get('extraction_delivery_policy')!=POLICY:raise ValueError('streaming preparation required')
    lineage=protocol.get('budget_lineage')
    if lineage:
        current,_=previous_extraction(lineage['previous_directory'])
        if current['previous_unknown_requests']:raise ValueError('prior unknown request remains fenced; no dispatch performed')
        if current['previous_physical_requests']!=lineage['previous_physical_requests']:
            raise ValueError('parent request ledger changed; freeze continuation again')
    frozen=read(directory/'code_fingerprints.json')
    if any(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h for p,h in frozen.items()):raise ValueError('prepared code changed')
    proposals=read(directory/'extraction_proposals.json')
    if any(sha256_json({k:v for k,v in p.items() if k!='proposal_digest'})!=p['proposal_digest'] for p in proposals):
        raise ValueError('proposal hash mismatch')
    docs={d['document_id']:d for d in read(directory/'extraction_documents.json')}
    parents={};indexes={}
    for doc in docs.values():
        if doc['parent_path'] not in parents:parents[doc['parent_path']]=read_snapshot(doc['parent_path'])
        parent=parents[doc['parent_path']]
        if parent['document_version']!=doc['parent_document_version']:raise ValueError('original source changed')
        index=load_document_index(doc['index_path'],doc['index_hash'],require_verified=True)
        for s in doc['segments']:
            if parent['canonical_text'][s['parent_start']:s['parent_end']]!=index['source_snapshot']['canonical_text'][s['excerpt_start']:s['excerpt_end']]:
                raise ValueError('source paragraph changed')
        indexes[doc['document_id']]=index
    config=merge_overrides(configuration(profile='weighted_diagnostic',directory=directory),read(directory/'config_overrides.json'))
    voter=extraction_voter(config)
    if not isinstance(voter['client'],OpenAICompatibleClient):raise ValueError('plain client required for durable provider process')
    if extractor_binding(voter)!=read(directory/'extractor_settings.json'):
        raise ValueError('extractor model, destination or transport changed since preparation')
    release=load_release(protocol['initial_release']['ontology_release_path'])
    repository=ReviewRepository(directory/'extraction.sqlite3');repository.initialize()
    cap=min(len(proposals),policy['extraction_request_cap']);stop=None;all_rows=[];rejected=[];receipts=[];seen=set()
    write(directory/'extraction_started.json',{'time':time.time(),'request_cap':cap,
        'known_token_stop':policy['extraction_known_token_stop'],'retries':0,'stream':True,
        'durable_process':True,'automatic_relaunch':False,'budget_lineage':lineage})
    (directory/'extraction_receipts').mkdir()
    for proposal in proposals[:cap]:
        usage=request_usage(repository)
        if usage['unknown_requests'] or usage['physical_requests']>=cap or usage['known_tokens']>=policy['extraction_known_token_stop']:
            stop='unknown_request_or_budget_fence';break
        result=judge_typed(voter,proposal,repository,task_type=VERSION,binding=binding(voter,proposal),
            invoke=lambda feedback:judge_once(voter,proposal,feedback),
            validate_cached=lambda v:[] if v.get('response_status')=='ok' else ['invalid extraction'],
            prompt_hash=sha256_json(proposal['system_prompt']),retries=0,timeout=180,deadline_epoch=time.time()+180)
        write(directory/'extraction_receipts'/(proposal['document_id']+'.json'),result);receipts.append(result)
        items,kind=intake_items(result,repository);doc=docs[proposal['document_id']]
        for ordinal,raw in enumerate(items):
            try:
                errors=schema_errors(raw,RESPONSE_SCHEMA['properties']['triplets']['items'])
                if errors:raise ValueError(str(errors))
                c=normalize_candidate(raw,index=indexes[doc['document_id']],scope=doc['scope'],ontology=release.ontology,
                    release_binding=release.binding(),run_id='stream_extract:'+doc['document_id'],ordinal=ordinal,
                    prompt_hash=hashlib.sha256(proposal['system_prompt'].encode()).hexdigest(),require_semantic_context=True)
                anchors=original_anchors(c,doc,parents[doc['parent_path']]['canonical_text'])
                signature=sha256_json([doc['source_group_id'],c['triple'],c['evidence_quotes']])
                if signature in seen:continue
                seen.add(signature);c.update(source_group_id=doc['source_group_id'],source_section=doc['source_section'],
                    original_source_anchors=anchors,extraction_model=voter['model_id'],extraction_attempt_id=result.get('attempt_id'),
                    extraction_intake_kind=kind,split_role='independent_test',independent_human_gold=False)
                all_rows.append(c)
            except (ValueError,TypeError,KeyError) as exc:rejected.append({'raw':raw,'reason':str(exc),'document_id':doc['document_id']})
        # Save accepted prefixes before returning on a failed or unknown original request.
        write(directory/('intake_checkpoint_'+str(len(receipts))+'.json'),{'candidates':all_rows,'rejections':rejected})
        print(json.dumps({'completed_dispatches':len(receipts),'candidates':len(all_rows),'status':result.get('response_status')},ensure_ascii=False),flush=True)
        if result.get('outcome_unknown') or result.get('pause_job'):stop='unknown_request_or_service_pause';break
    development=read(ROOT/'outputs/development_pool_20261117/candidates.json')+read(ROOT/'outputs/dev30_20261031/sample_data.json')['selected_raw_candidates']
    prior={(c['candidate_id'],c['revision']) for c in development};leaked=set()
    for group in leakage_groups(development+all_rows):
        if any(k in prior for k in group):leaked.update(k for k in group if k not in prior)
    selected=select_candidates([c for c in all_rows if (c['candidate_id'],c['revision']) not in leaked],protocol['target_population'])
    roles={(c['candidate_id'],c['revision']):'development' for c in development}
    roles.update({(c['candidate_id'],c['revision']):'independent_test' for c in selected})
    split=verify_split(development+selected,roles)
    write(directory/'all_extracted_candidates.json',all_rows);write(directory/'candidates.json',selected)
    write(directory/'intake_rejections.json',rejected);write(directory/'leakage_exclusions.json',[list(k) for k in sorted(leaked)])
    save_manifest(freeze_manifest(selected,run_id=VERSION,settings={'split_integrity':split,'label_source':None}),directory/'population_manifest.json')
    summary={'status':'streaming_extraction_stopped' if stop else 'prepared_sources_exhausted',
        'stop_reason':stop,'target':protocol['target_population'],'selected_candidates':len(selected),
        'request_usage':request_usage(repository),'human_gold_labels':0,'gpt_labels_assigned_to_new_candidates':0,
        'main_review_started':False,'production_written':False}
    write(directory/'extraction_summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)
    return summary
