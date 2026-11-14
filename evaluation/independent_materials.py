"""Freeze user-confirmed unused source windows before extraction or model review."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from document.snapshot import read_snapshot
from review_agent.contracts import sha256_json


POLICY={'version':'independent_source_sampling_v1','window_count':12,
    'paragraph_characters':[300,1800],'minimum_chinese_character_fraction':.2,
    'sampling':'nearest distinct eligible paragraph to each equal-width canonical-text stratum midpoint',
    'tie_breaker':'earlier source offset','replacement_after_results':False,
    'candidate_limit_per_window':1,'no_extractable_fact':'retain as uncovered; never replace after seeing results',
    'duplicate_fact_screen':'required after extraction against every development candidate and other selected windows',
    'gold_label_policy':'blank independent annotations; model or assistant labels must retain their actual provenance',
    'proposed_call_ceiling':{'extraction':12,'review_including_planning_and_repairs':216,'total':228},
    'live_execution_enabled':False}


def select_windows(text,policy=POLICY):
    paragraphs=[];low,high=policy['paragraph_characters']
    for match in re.finditer(r'[^\n]+(?:\n(?!\n)[^\n]+)*',text):
        value=match.group()
        if (low<=len(value)<=high and not value.lstrip().startswith(('#','![')) and
            len(re.findall(r'[\u4e00-\u9fff]',value))/len(value)>=policy['minimum_chinese_character_fraction']):
            paragraphs.append({'start':match.start(),'end':match.end(),'quote':value})
    count=policy['window_count']
    if len(paragraphs)<count:raise ValueError('insufficient eligible paragraphs for frozen sampling rule')
    selected=[];used=set()
    for ordinal in range(count):
        target=(ordinal+.5)*len(text)/count
        paragraph=min((p for p in paragraphs if p['start'] not in used),key=lambda p:(abs(p['start']-target),p['start']))
        used.add(paragraph['start'])
        selected.append({'window_id':'window_'+str(ordinal+1).zfill(2),'stratum':ordinal,**paragraph})
    return selected


def prepare(source,development_data,output,*,confirmation):
    if not str(confirmation).strip():raise ValueError('explicit unused-for-debugging source confirmation required')
    source=Path(source).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('use a new material freeze directory')
    output.mkdir(parents=True)
    def write(name,value):
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    # Policy is saved before inspecting or selecting text. No model output can
    # affect selection, sample count or the replacement rule.
    write('sampling_policy.json',POLICY)
    snapshot=read_snapshot(source)
    development=json.loads(Path(development_data).read_text(encoding='utf-8'))
    if snapshot['document_version'] in {c['document_version'] for c in development['selected_raw_candidates']}:
        raise ValueError('independent source duplicates a development document')
    windows=select_windows(snapshot['canonical_text'])
    for window in windows:
        if window['quote'] in development['source_canonical_text']:
            raise ValueError('selected source window duplicates development text; revise sampling design before any execution')
    write('source_snapshot.json',snapshot);write('source_windows.json',windows)
    write('annotation_template.json',{'status':'pending_extraction_and_independent_annotation','annotated_by':None,
        'source_windows':[{'window_id':w['window_id'],'source_hash':snapshot['document_version'],
            'start':w['start'],'end':w['end'],'candidate':None,'four_axes':None,
            'needs_revision':None,'avoidable_manual_review':None,'reviewer':None,'note':None} for w in windows]})
    manifest={'version':'independent_material_freeze_v1','prepared_at':datetime.now(timezone.utc).isoformat(),
        'source_path':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'document_id':snapshot['document_id'],'document_version':snapshot['document_version'],
        'prior_debug_use':'user_confirmed_unused','provenance_evidence':confirmation,
        'development_data_sha256':hashlib.sha256(Path(development_data).read_bytes()).hexdigest(),
        'sampling_policy_hash':sha256_json(POLICY),'window_count':len(windows),
        'window_hashes':{w['window_id']:sha256_json(w) for w in windows},
        'fact_leakage_status':'pending_after_candidate_extraction','human_gold_labels':0,
        'new_model_calls':0,'status':'materials_prepared_not_yet_validated','live_execution_enabled':False}
    manifest['manifest_hash']=sha256_json(manifest)
    write('manifest.json',manifest)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True);parser.add_argument('--development-data',required=True)
    parser.add_argument('--output',required=True);parser.add_argument('--unused-confirmation',required=True)
    args=parser.parse_args()
    result=prepare(args.source,args.development_data,args.output,confirmation=args.unused_confirmation)
    print(json.dumps({k:result[k] for k in ('status','window_count','new_model_calls','manifest_hash')},ensure_ascii=False))


if __name__=='__main__':main()
