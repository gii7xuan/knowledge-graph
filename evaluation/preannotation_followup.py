"""AI-preannotation intake, source supplements and isolated unapproved revision drafts."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .freeze_shared_ontology import ROOT,read,write
from .pilot_annotations import convert
from .development_pool import EXCLUDED
from review_agent.contracts import sha256_json

VERSION='ai_preannotation_followup_v1'


def ingest(workbook,pilot):
    imported=convert(workbook,pilot)
    rows=[]
    for item in imported['annotations']:
        row=deepcopy(item)
        row.update(label_source='ai_preannotation',counts_as_human_gold=False,
            eligible_for_reliability_training=False,eligible_for_paper_scoring=False,
            annotation_origin={'kind':'ai','claimed_annotator':row['raw_annotation'].get('reviewer'),
                'source_workbook_hash':imported['source_workbook_hash']})
        row['adjudication'].update(adjudication_status='pending',adjudicated_label={},adjudicated_by=None)
        rows.append(row)
    return {'version':VERSION,'manifest_hash':imported['manifest_hash'],'source_workbook_hash':imported['source_workbook_hash'],
        'label_source':'ai_preannotation','annotation_status':'pending_independent_review','human_gold_count':0,
        'reviewer_role':'ai_preannotator','annotations':rows}


def triage(row,sample):
    raw=row['raw_annotation'];labels=raw['labels'];questions=[]
    if labels['source_support'] in {'unresolved','insufficient'}:
        questions.append('original_proposition_source_support')
    if labels['expression_status'] in {'needs_revision','unresolved'}:
        questions.append('expression_or_referent_review')
    if labels['knowledge_scope'] in {'local_definition','derivation','example'}:
        questions.append('source_bound_scope')
    if labels['mapping_status']=='unassigned':questions.append('formal_mapping_not_assigned')
    if labels['schema_coverage'] in {'not_covered','unresolved'}:questions.append('full_ontology_coverage_evidence')
    if labels['admission_preference']=='include' and labels['mapping_status']!='correct':
        questions.append('include_preference_is_not_admission')
    if labels['source_support']!='supported' and raw.get('supporting_evidence_ids'):
        questions.append('positive_citations_may_only_support_context')
    if sample['sample_id'] in {'PILOT-011','PILOT-036'}:questions.append('ocr_symbol_requires_original_page')
    if sample['sample_id']=='PILOT-020':questions.append('formula_term_identity_missing')
    if raw.get('duration_seconds') is None:questions.append('human_time_not_measured')
    priority='source_identity_or_conflict' if any(q in questions for q in (
        'original_proposition_source_support','ocr_symbol_requires_original_page','formula_term_identity_missing')) else 'expression_review' if labels['expression_status']=='needs_revision' else 'mapping_and_scope'
    return {'sample_id':sample['sample_id'],'binding':deepcopy(sample['binding']),'ai_labels':deepcopy(labels),
        'priority':priority,'questions':questions,'ai_note':raw.get('note'),'ai_suggestion':raw.get('expected_revision_text'),
        'status':'pending_independent_review','automatic_research_operation':None,
        'gold_created':False,'candidate_changed':False}


def source_context(candidate,max_neighbors=2):
    """Exact original paragraphs; omissions and unsafe/out-of-scope paragraphs stay explicit."""
    from document.snapshot import read_snapshot
    result=[];seen=set()
    for anchor in candidate['original_source_anchors']:
        snapshot=read_snapshot(anchor['parent_path']);text=snapshot['canonical_text']
        if snapshot['document_version']!=anchor['parent_document_version'] or text[anchor['start']:anchor['end']]!=anchor['quote']:
            raise ValueError('original source binding changed')
        headings=list(re.finditer(r'^#+\s+(.+)$',text,re.M))
        previous=[h for h in headings if h.start()<=anchor['start']]
        heading=previous[-1];end=next((h.start() for h in headings if h.start()>anchor['end']),len(text))
        start=heading.end()
        # Never expand beyond the same source subsection.
        paragraphs=[m for m in re.finditer(r'\S[\s\S]*?(?=\n\s*\n|\Z)',text[start:end])]
        overlapping=[i for i,m in enumerate(paragraphs) if start+m.start()<anchor['end'] and start+m.end()>anchor['start']]
        if not overlapping:continue
        selected=paragraphs[max(0,overlapping[0]-max_neighbors):overlapping[-1]+max_neighbors+1]
        items=[];excluded=[]
        for p in selected:
            a=start+p.start();b=start+p.end();quote=text[a:b].rstrip();b=a+len(quote)
            if len(quote)>5000 or EXCLUDED.search(quote):
                excluded.append({'start':a,'end':b,'reason':'outside_bounded_descriptive_context'});continue
            key=(str(anchor['parent_path']),a,b)
            if key in seen:continue
            seen.add(key)
            items.append({'evidence_id':'parent_'+sha256_json([anchor['parent_document_version'],a,b])[:20],
                'document_version':anchor['parent_document_version'],'parent_path':anchor['parent_path'],
                'start':a,'end':b,'quote':quote,'source_role':'supplementary_original_source','semantic_status':'unassessed',
                'formula_tags':re.findall(r'\\tag\s*\{([^}]+)\}',quote)})
        result.append({'section':heading.group(1),'anchor_evidence_id':anchor['evidence_id'],'items':items,
            'excluded_spans':excluded,'contiguous_original_text_not_guaranteed':bool(excluded)})
    return {'original_initial_bundle_changed':False,'new_round_required_for_use':True,
        'counts_as_support_vote':False,'contexts':result}


def ontology_material(row,classes,relations):
    raw=row['raw_annotation'];text=str(raw.get('note',''))+' '+str(raw.get('expected_revision_text',''))
    references=sorted(set(re.findall(r'\b[RC]_[a-f0-9]{20}\b',text)))
    material=[];missing=[]
    for key in references:
        if key.startswith('R_'):
            relation=relations.get(key)
            if relation is None:missing.append(key);continue
            endpoints={k:deepcopy(classes[k]) for k in set(relation.get('domain',[])+relation.get('range',[])) if k in classes}
            material.append({'relation':deepcopy(relation),'endpoint_classes':endpoints,
                'interpretation':'exact frozen domain/range apply; a generic label or definition does not widen them'})
        elif key in classes:material.append({'class':deepcopy(classes[key])})
        else:missing.append(key)
    observations=[]
    if row['sample_id'] in {'PILOT-015','PILOT-034'}:
        relation=relations['R_5d7591372f1859dc86e0']
        observations.append({'code':'NARROW_FORMAL_CAUSAL_RELATION',
            'domain_labels':[classes[k]['label'] for k in relation['domain']],
            'range_labels':[classes[k]['label'] for k in relation['range']],
            'conclusion':'The cited relation is formally crosswind to lateral deviation; it is not a general molecular-collision/pressure relation.',
            'relation_assigned':False,'other_relations_proven_absent':False})
    return {'sample_id':row['sample_id'],'referenced_ids':references,'missing_referenced_ids':missing,
        'materials':material,'observations':observations,'coverage_label_status':'ai_claim_pending_review',
        'formal_mapping_assigned':False}


def raw_for_candidate(candidate,extraction_repo):
    from review_agent.validators import _parse_content
    attempt=extraction_repo.request_attempt(candidate['extraction_attempt_id'])
    if attempt['state']!='completed' or attempt['result']['response_status']!='ok':raise ValueError('source extraction is not complete and valid')
    raw=_parse_content(attempt['result']['raw_response'])
    matches=[r for r in raw['triplets'] if all(r.get(k)==candidate['triple'].get(k) for k in ('head','surface_relation','tail'))
        and r.get('evidence_quotes')==candidate['evidence_quotes']]
    if len(matches)!=1:raise ValueError('original raw candidate not uniquely bound')
    return matches[0],attempt


def wire_plan(candidate,raw):
    from extract_triplets.qualifier_wire import project,policy,validate
    from extract_triplets.candidate_contract import normalize_qualifiers
    projected,proof=project(raw,policy())
    if proof is None:return None
    if validate(raw,projected,proof):raise ValueError('wire projection failed integrity validation')
    before_values,before_states,_=normalize_qualifiers(raw)
    if candidate['triple']['qualifiers']!=before_values or candidate['triple']['qualifier_status']!=before_states:
        raise ValueError('raw qualifier view differs from frozen candidate')
    values,states,_=normalize_qualifiers(projected)
    changed={m['path'].rsplit('/',1)[1] for m in proof['moves']}
    changes={'qualifiers':{},'qualifier_status':{}}
    for field in values:
        if before_values[field]!=values[field] or before_states[field]!=states[field]:
            if field not in changed:raise ValueError('wire plan changed an unrequested judgment')
            changes['qualifiers'][field]=values[field];changes['qualifier_status'][field]=states[field]
    if not changes['qualifiers']:return None
    return {'candidate_id':candidate['candidate_id'],'base_revision':candidate['revision'],'candidate_hash':sha256_json(candidate),
        'proof':proof,'original_raw':deepcopy(raw),'projected_raw':projected,'changes':changes,
        'label_source':'source_wire_record','ai_labels_used_to_choose_values':False,
        'requires_independent_revision_review':True,'automatically_applied':False}


def run(workbook,pool,out):
    from .review_samples import ReadOnlyRepository
    from .manifest import validate_manifest
    from ontologyBuild.release_service import load_release
    from ontologyBuild.ontology_adapter import iter_classes,iter_object_properties
    from review_agent.shadow_revision import clone
    from review_agent.revision_service import RevisionService
    from document.hierarchy_index import load_document_index
    from evidence.hierarchical_retriever import retrieve_initial
    workbook=workbook.resolve();pool=pool.resolve();out=out.resolve()
    if not out.is_relative_to(ROOT/'outputs') or out.exists():raise ValueError('new isolated output directory required')
    imported=ingest(workbook,pool/'pilot')
    frozen=validate_manifest(read(pool/'population_manifest.json'))
    samples=read(pool/'pilot/review_samples.json')['samples'];sample_by_id={s['sample_id']:s for s in samples}
    candidates=read(pool/'candidates.json')
    watched=[workbook,pool/'candidate_pool.sqlite3',pool/'extraction.sqlite3',pool/'population_manifest.json',pool/'candidates.json',pool/'pilot/manifest.json']
    release=load_release(read(pool/'protocol.json')['fixed_release']['ontology_release_path'])
    from .freeze_shared_ontology import SOURCE
    old=read(SOURCE/'contract_recovery_completed_candidate.json')
    watched.append(Path(old['ontology_release_path']).parent/'release_catalog.sqlite3')
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
    out.mkdir(parents=True)
    write(out/'baseline.json',{'watched_hashes':hashes,'original_workbook':str(workbook),'population_manifest_hash':frozen['manifest_hash']})
    write(out/'ai_preannotations.json',imported)
    queue=[triage(r,sample_by_id[r['sample_id']]) for r in imported['annotations']]
    write(out/'review_queue.json',queue)
    supplements={s['sample_id']:source_context(s['candidate']) for s in samples}
    write(out/'source_supplements.json',supplements)
    classes={c['id']:c for c in iter_classes(release.ontology)};relations={r['id']:r for r in iter_object_properties(release.ontology)}
    mapping=[ontology_material(r,classes,relations) for r in imported['annotations']]
    write(out/'mapping_materials.json',{'release':release.binding(),'full_relation_catalog':list(relations.values()),
        'full_class_catalog':list(classes.values()),'samples':mapping,'ontology_changed':False})
    extraction=ReadOnlyRepository(pool/'extraction.sqlite3')
    plans=[];blocked=[]
    for candidate in candidates:
        try:
            raw,receipt=raw_for_candidate(candidate,extraction);plan=wire_plan(candidate,raw)
            if plan:
                plan.update(extraction_attempt_id=receipt['attempt_id'],source_index_hash=candidate['source_index_hash'])
                plans.append(plan)
        except (ValueError,KeyError,TypeError) as exc:blocked.append({'candidate_id':candidate['candidate_id'],'reason':str(exc)})
    write(out/'wire_repair_plans.json',plans);write(out/'wire_plan_blockers.json',blocked)
    repo=clone(pool/'candidate_pool.sqlite3',out/'revision_drafts',allowed_root=ROOT/'outputs')
    service=RevisionService(repo);drafts=[];draft_failures=[];documents={d['document_id']:d for d in read(pool/'source_manifest.json')['documents']}
    by_id={c['candidate_id']:c for c in candidates};cache={}
    for plan in plans:
        candidate=by_id[plan['candidate_id']];doc=documents[candidate['document_id']]
        if doc['document_id'] not in cache:cache[doc['document_id']]=load_document_index(doc['index_path'],doc['index_hash'],require_verified=True)
        index=cache[doc['document_id']];bundle=retrieve_initial(candidate,index,max_items=8)
        try:
            draft=service.propose(candidate,repo.get_candidate(candidate['candidate_id'])['row_version'],plan['changes'],
                [e['evidence_id'] for e in candidate['evidence_items']],
                'Versioned wire repair of an explicit original not_stated sentinel; no new factual or modality inference. '+sha256_json(plan['proof']),
                bundle,index,protected_ids=[e['evidence_id'] for e in bundle['items']])
            drafts.append(draft)
        except Exception as exc:draft_failures.append({'candidate_id':candidate['candidate_id'],'reason':str(exc)})
    write(out/'revision_drafts.json',drafts);write(out/'draft_blockers.json',draft_failures)
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items())
    assert all(repo.get_revision(c['candidate_id'])==c for c in candidates)
    labels={d:dict(Counter(r['raw_annotation']['labels'][d] for r in imported['annotations'])) for d in imported['annotations'][0]['raw_annotation']['labels']}
    summary={'version':VERSION,'binding_checks_passed':True,'preannotations':len(imported['annotations']),
        'label_source':'ai_preannotation','human_gold_count':0,'paper_metrics_calculated':False,
        'label_counts':labels,'priority_counts':dict(Counter(q['priority'] for q in queue)),
        'include_preferences_not_admissions':sum('include_preference_is_not_admission' in q['questions'] for q in queue),
        'source_supplement_packages':len(supplements),'source_supplement_items':sum(len(c['items']) for s in supplements.values() for c in s['contexts']),
        'formal_mapping_assigned':0,'ontology_changed':False,'wire_repair_plans':len(plans),'wire_plan_blockers':len(blocked),
        'independent_revision_drafts':len(drafts),'draft_blockers':len(draft_failures),'revisions_applied':0,
        'original_candidates_unchanged':True,'original_workbook_and_databases_unchanged':True,
        'new_model_calls':0,'human_time_measured':False,'production_written':False}
    write(out/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workbook',type=Path,required=True)
    p.add_argument('--pool',type=Path,default=ROOT/'outputs/development_pool_20261117');p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();run(args.workbook,args.pool,args.output)


if __name__=='__main__':main()
