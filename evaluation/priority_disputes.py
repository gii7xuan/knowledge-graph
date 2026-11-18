"""Resolve seven source questions in a separate augmented-source revision cohort."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
from zipfile import ZipFile,ZIP_DEFLATED

from .freeze_shared_ontology import ROOT,read,write
from .preannotation_followup import raw_for_candidate
from .development_pool import excerpt,original_anchors
from .review_samples import ReadOnlyRepository
from review_agent.contracts import sha256_json

VERSION='priority_disputes_source_review_v1'
REVIEW_IDS=('PILOT-001','PILOT-002','PILOT-017','PILOT-020','PILOT-027')


def config_for(out):
    from runs.regress_v8 import configuration
    config=configuration(profile='weighted_diagnostic',directory=out)
    config['budget'].update(max_parallel_validators=1,max_llm_calls_per_candidate=3,total_timeout_seconds=720,
        per_call_timeout_seconds=240,max_total_tokens=120000,max_retries_per_request=0)
    config['case_budget'].update(llm_calls=3,tokens=120000)
    config['planner']['enabled']=False;config['memory']['enabled']=False
    for slot in config['validators']:slot.update(request_timeout_seconds=240,http_timeout_seconds=240,max_output_tokens=4096)
    return config


def changes_for(sid,candidate):
    """Explicit semantic proposals, never auto-normalization or human gold."""
    scope=candidate['section_id'];ctx=deepcopy(candidate['triple']['semantic_context'])
    changes={};attachments=[]
    if sid=='PILOT-001':
        changes={'head':'this passage','surface_relation':'instructs readers to treat',
            'tail':'covolume as an empirical fitting parameter without assigning it physical meaning here',
            'tail_kind':'proposition','qualifiers':{'polarity':'positive','applicability':'the interpretive instruction in this passage'},
            'qualifier_status':{'polarity':'extracted','applicability':'extracted'}}
        note='An attributed interpretive instruction; not a universal denial that the parameter can have a physical interpretation.'
        attachments=[{'qualifier_kind':'discourse','attachment':'relation','value':'Think of it as','scope_id':scope},
            {'qualifier_kind':'polarity','attachment':'tail','value':'not having physical meaning','scope_id':scope}]
        ctx['knowledge_scope']='local_definition'
    elif sid=='PILOT-002':
        changes={'head':'work and energy','surface_relation':'are identified in this text as',
            'tail':'major thermodynamic concepts introduced in earlier sections','tail_kind':'entity',
            'qualifiers':{'time_scope':None},'qualifier_status':{'time_scope':'not_stated'}}
        note='A statement about concepts and their introduction in this text; the discipline is not an agent performing introduced.'
        attachments=[{'qualifier_kind':'discourse','attachment':'tail','value':'earlier sections of this text','scope_id':scope}]
        ctx['knowledge_scope']='local_definition'
    elif sid=='PILOT-017':
        changes={'head':"the authors' analysis in the Eulerian frame",'surface_relation':'uses',
            'tail':'a chosen real or imaginary control volume','tail_kind':'entity',
            'qualifiers':{'applicability':'the Eulerian-frame thermodynamics discussion in this text'},
            'qualifier_status':{'applicability':'extracted'}}
        note='The chosen control volume belongs to the described analysis; the frame of reference is not an agent choosing it.'
        ctx['knowledge_scope']='derivation'
    elif sid=='PILOT-020':
        changes={'head':'the second term on the right-hand side of Equation 2.129 in this text',
            'surface_relation':'is described by the text as indicating','tail':'how much material has left the control volume',
            'tail_kind':'proposition','qualifiers':{'polarity':'positive','modality':'asserted',
                'applicability':'the explanation of Equation 2.129 in this text'},
            'qualifier_status':{'polarity':'extracted','modality':'extracted','applicability':'extracted'}}
        note='The contiguous original paragraph identifies the preceding Equation 2.129. Preserve the textual explanation without inventing a cumulative mass, a new transported property or a different equation.'
        ctx['knowledge_scope']='local_definition'
    elif sid=='PILOT-027':
        changes={'head':'本段伯努利方程的沿流线积分推导','surface_relation':'以……为前提之一',
            'tail':'质量力为有势力','tail_kind':'proposition',
            'qualifiers':{'condition':'流动定常（各参量对时间的偏导数为零），且（流体为正压性的，或流体不可压缩）',
                'applicability':'本段理想流体欧拉运动方程沿流线积分的推导'},
            'qualifier_status':{'condition':'extracted','applicability':'extracted'}}
        note='质量力有势是所述推导的前提，不是所有质量力的恒常性质；保留定常与（正压或不可压缩）的合取/析取范围。'
        attachments=[{'qualifier_kind':'condition','attachment':'relation',
            'value':changes['qualifiers']['condition'],'scope_id':scope}]
        ctx['knowledge_scope']='derivation'
    else:raise ValueError('no source-grounded semantic proposal for this question')
    ctx['entity_mentions']={side:{'entity_surface':changes[side],'canonical_entity':changes[side],
        'referent_scope':'this frozen source passage'} for side in ('head','tail')}
    ctx['relation_meaning']={'semantic_equivalence':'needs_review','note':note}
    ctx['qualifier_attachments']=attachments
    changes['semantic_context']=ctx
    return changes


def augmented_base(sample,source_document,supplement,out,release,extraction):
    from document.snapshot import read_snapshot,make_snapshot
    from document.hierarchy_index import split_markdown_text,build_document_index,persist_document_index,normalize_document_id
    from extract_triplets.pipeline_v4 import source_scope
    from extract_triplets.candidate_contract import normalize_candidate
    c=sample['candidate'];parent=read_snapshot(source_document['parent_path']);text=parent['canonical_text']
    if parent['document_version']!=source_document['parent_document_version']:raise ValueError('parent source changed')
    ranges={(s['parent_start'],s['parent_end']) for s in source_document['segments']}
    for group in supplement['contexts']:
        for s in group['items']:
            if s['document_version']!=parent['document_version'] or text[s['start']:s['end']]!=s['quote']:
                raise ValueError('supplement source mismatch')
            ranges.add((s['start'],s['end']))
    # Existing and added source paragraphs must not overlap or duplicate a quote.
    selected=[]
    for a,b in sorted(ranges):
        if selected and a<selected[-1]['end']:raise ValueError('ambiguous overlapping source paragraphs')
        selected.append({'start':a,'end':b,'text':text[a:b],'section':source_document['source_section']})
    sid=sample['sample_id'];docid=normalize_document_id(c['document_id']+'_'+sid.lower()+'_source_v2')
    content,segments=excerpt(selected,docid)
    path=out/'sources'/(sid+'.md');path.write_text(content,encoding='utf-8')
    snapshot=make_snapshot(content,docid)
    chunks=split_markdown_text(content,document_id=docid,chunk_size=max(18000,len(content)+1),chunk_overlap=0,snapshot=snapshot)
    index=build_document_index(chunks)
    if len(chunks)!=1:raise ValueError('one bounded source context required')
    index_path=out/'sources'/(sid+'_index.json');persist_document_index(index,index_path)
    scope=source_scope(index,chunks[0],start=0,end=len(content),neighbors=0)
    scope.update(context_start=0,context_end=len(content))
    raw,attempt=raw_for_candidate(c,extraction)
    candidate=normalize_candidate(raw,index=index,scope=scope,ontology=release.ontology,release_binding=release.binding(),
        run_id=VERSION+':'+sid,ordinal=1,prompt_hash=c['extraction_prompt_hash'],require_semantic_context=True)
    for key in ('head','surface_relation','tail','tail_kind','qualifiers','qualifier_status','value','datatype','unit','language'):
        if candidate['triple'].get(key)!=c['triple'].get(key):raise ValueError('source augmentation changed the original statement: '+key)
    candidate['source_reanchor_origin']={'candidate_id':c['candidate_id'],'revision':c['revision'],
        'candidate_content_hash':sha256_json(c),'initial_evidence_hash':sample['binding']['evidence_hash'],
        'augmentation_hash':sha256_json(supplement),'role':'same_original_expression_new_frozen_evidence_context',
        'old_labels_or_votes_reusable':False}
    doc={**source_document,'document_id':docid,'path':str(path),'index_path':str(index_path),'index_hash':index['index_content_hash'],
        'document_version':index['document_version'],'scope':scope,'segments':segments}
    candidate['original_source_anchors']=original_anchors(candidate,doc,text)
    return candidate,index,doc,attempt['attempt_id']


def prepare(out,pool,followup):
    from ontologyBuild.release_service import load_release
    from review_agent.repository import ReviewRepository
    from review_agent.revision_service import RevisionService
    from evidence.hierarchical_retriever import retrieve_initial,_evidence
    from evidence.verification import make_evidence_bundle,verify_evidence_bundle
    from runs.regress_v8 import public_settings
    if (out/'protocol.json').exists():raise ValueError('priority study already prepared')
    out.mkdir(parents=True,exist_ok=True);(out/'sources').mkdir()
    samples={s['sample_id']:s for s in read(pool/'pilot/review_samples.json')['samples']}
    documents={d['document_id']:d for d in read(pool/'source_manifest.json')['documents']}
    supplements=read(followup/'source_supplements.json')
    release=load_release(read(pool/'protocol.json')['fixed_release']['ontology_release_path'])
    repo=ReviewRepository(out/'review.sqlite3');repo.initialize();service=RevisionService(repo)
    extraction=ReadOnlyRepository(pool/'extraction.sqlite3')
    from .freeze_shared_ontology import SOURCE
    original=read(SOURCE/'contract_recovery_completed_candidate.json')
    watched=[pool/'candidate_pool.sqlite3',pool/'extraction.sqlite3',pool/'candidates.json',pool/'pilot/manifest.json',
        followup/'revision_drafts/review.sqlite3',followup/'ai_preannotations.json',
        Path(original['ontology_release_path']).parent/'release_catalog.sqlite3']
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
    write(out/'baseline.json',{'watched_hashes':hashes,'source_pool':str(pool),'ai_prelabels_are_gold':False})
    rows=[]
    for sid in REVIEW_IDS:
        sample=samples[sid];doc=documents[sample['candidate']['document_id']]
        candidate,index,doc,attempt=augmented_base(sample,doc,supplements[sid],out,release,extraction)
        repo.register_v4_run({'run_id':candidate['run_id'],'document_path':doc['index_path'],
            'source_index_hash':doc['index_hash'],'source_hash':doc['document_version'],'ontology_hash':release.manifest['ontology_version'],
            'mode':'shadow',**release.binding()},[candidate])
        initial=retrieve_initial(candidate,index,max_items=8);chunk=index['chunks'][0]
        bundle=verify_evidence_bundle(make_evidence_bundle([*initial['items'],_evidence(chunk,0,len(chunk['content']),'current_chunk')],
            ['source_augmented_original_quotes','current_chunk']),index,candidate)
        patch=changes_for(sid,candidate)
        draft=service.propose(candidate,repo.get_candidate(candidate['candidate_id'])['row_version'],patch,
            [e['evidence_id'] for e in candidate['evidence_items']],
            'Explicit source-grounded semantic revision proposed by the coding agent; old AI labels are not truth. '+sid,
            bundle,index,protected_ids=[e['evidence_id'] for e in bundle['items']])
        rows.append({'sample_id':sid,'original_binding':sample['binding'],'reanchored_candidate':candidate,
            'source_document':doc,'source_extraction_attempt_id':attempt,'draft':draft,
            'analysis_origin':'coding_agent_source_analysis','counts_as_human_gold':False,'semantic_revision_success':None})
    write(out/'prepared_cases.json',rows)
    dispositions=[{'sample_id':sid,'status':'semantic_draft_prepared','draft_id':next(r['draft']['draft_id'] for r in rows if r['sample_id']==sid),
        'counts_as_human_gold':False} for sid in REVIEW_IDS]
    dispositions.extend([
        {'sample_id':'PILOT-011','status':'blocked_original_page_required','action':'preserve original text and candidate; do not replace k_{circ} or invent literal datatype/value',
            'reason':'No matching local source page for the Hamiltonian-operator symbol was found.','counts_as_human_gold':False},
        {'sample_id':'PILOT-036','status':'handled_preserve_notation_defer_modality_encoding',
            'action':'Preserve the local δ double-prime text and the conditional normative meaning of would require; do not rewrite as asserted or claim the authors applied the convention.',
            'publisher_preview_visual_check':{'edition':'fourth','printed_page':21,'pdf_page':38,'observed_glyph':'δ″',
                'local_markdown_same_edition_verified':False,'supports_deleting_marks_as_ocr_noise':False},
            'remaining_issue':'would is outside the current modality enum; an explicit meaning-preserving representation is still required.',
            'counts_as_human_gold':False}])
    write(out/'dispositions.json',dispositions)
    config=config_for(out)
    write(out/'settings.json',public_settings(config))
    write(out/'protocol.json',{'version':VERSION,'review_cases':list(REVIEW_IDS),'max_calls':15,'known_token_stop':600000,
        'requests_per_draft':3,'retries':0,'per_call_timeout_seconds':240,'case_timeout_seconds':720,
        'max_parallel_validators':1,'stop_new_dispatch_on_unknown':True,'revisions_applied':False,
        'paper_metrics':False,'human_gold':False,'model_reliability_updated_from_ai_labels':False,
        'shared_ontology_changed':False,'old_votes_reused':False,'source_augmentation_is_new_input':True})
    files=[ROOT/'evaluation/priority_disputes.py',ROOT/'review_agent/revision_service.py',ROOT/'tests/test_priority_disputes.py']
    write(out/'code_fingerprints.json',{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    with ZipFile(out/'execution_code.zip','x',ZIP_DEFLATED) as z:
        for p in files:z.write(p,str(p.relative_to(ROOT)))
    return config


def execute(out,config):
    from review_agent.repository import ReviewRepository
    from review_agent.revision_service import RevisionService
    from review_agent.cli import _configured_validators
    from review_agent.case_budget import CaseBudget
    from review_agent.consensus import Committee
    from review_agent.execution import AuditPaused
    if (out/'started.json').exists():raise ValueError('already started; inspect durable receipts instead of relaunching')
    repo=ReviewRepository(out/'review.sqlite3');service=RevisionService(repo);voters=_configured_validators(config)
    if len(voters)!=3:raise ValueError('three configured independent seats required')
    committee=Committee([v['id'] for v in voters],config['consensus']['required_votes'],config['consensus']['minimum_distinct_families'],
        config['consensus']['version'],config['vote_weighting'])
    write(out/'started.json',{'time':time.time(),'max_calls':15,'automatic_relaunch':False})
    results=[];stop=None
    for row in read(out/'prepared_cases.json'):
        with repo.connect() as c:
            attempts=c.execute('SELECT state,result_json FROM request_attempts').fetchall()
        tokens=sum(sum(json.loads(r['result_json']or'{}').get(k)or 0 for k in ('input_tokens','output_tokens')) for r in attempts)
        if any(r['state'] in {'running','unknown'} for r in attempts) or len(attempts)+3>15 or tokens>=600000:
            stop='request_or_budget_fence';break
        candidate=row['reanchored_candidate'];case=repo.ensure_case(candidate['candidate_id'],config['case_budget'])
        budget=CaseBudget(config['budget'],repo,[case],task_type='priority_semantic_revision',task_id=row['draft']['draft_id'])
        try:result=service.review(row['draft']['draft_id'],[{**v,'case_ids':budget.case_ids} for v in voters],committee,budget)
        except AuditPaused as exc:
            write(out/(row['sample_id']+'_pause.json'),{'error':str(exc)});stop='service_paused';break
        write(out/(row['sample_id']+'_review.json'),result)
        results.append({'sample_id':row['sample_id'],'draft_id':row['draft']['draft_id'],'status':result['status'],
            'votes':[{k:v.get(k) for k in ('voter_id','response_status','decision','outcome_unknown','error')} for v in result.get('votes',[])],
            'weight_snapshot':result.get('weight_snapshot')})
        print(json.dumps(results[-1],ensure_ascii=False),flush=True)
        if any(v.get('outcome_unknown') for v in result.get('votes',[])):stop='new_request_unknown';break
    with repo.connect() as c:
        attempts=[dict(r) for r in c.execute('SELECT state,result_json FROM request_attempts')]
    receipts=[json.loads(r['result_json']or'{}') for r in attempts]
    unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in read(out/'baseline.json')['watched_hashes'].items())
    report={'rows':results,'calls':len(attempts),'known_input_tokens':sum(r.get('input_tokens')or 0 for r in receipts),
        'known_output_tokens':sum(r.get('output_tokens')or 0 for r in receipts),'unknown_requests':sum(r['state']=='unknown' for r in attempts),
        'draft_status_counts':dict(Counter(r['status'] for r in results)),'revisions_applied':0,
        'original_sources_candidates_and_databases_unchanged':unchanged,'stop_reason':stop,
        'human_gold':False,'paper_accuracy':None,'model_reliability_updated':False,'price':None,'production_written':False}
    assert unchanged and len(attempts)<=15
    write(out/'report.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--run',action='store_true')
    p.add_argument('--execute-prepared',action='store_true')
    p.add_argument('--pool',type=Path,default=ROOT/'outputs/development_pool_20261117')
    p.add_argument('--followup',type=Path,default=ROOT/'outputs/preannotation_followup_20261118')
    args=p.parse_args();out=args.output.resolve()
    if not out.is_relative_to(ROOT/'outputs'):raise ValueError('isolated outputs directory required')
    config=config_for(out) if args.execute_prepared else prepare(out,args.pool.resolve(),args.followup.resolve())
    if args.run or args.execute_prepared:execute(out,config)


if __name__=='__main__':main()
