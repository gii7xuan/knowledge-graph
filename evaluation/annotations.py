"""Export revision-bound partial annotations independently of calibration rows."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from review_agent.contracts import sha256_json
from review_agent.labels import validate_labels
from .manifest import key,validate_manifest


def annotation_template(manifest):
    validate_manifest(manifest)
    return {'annotations':[{**{k:c.get(k) for k in ('candidate_id','revision','candidate_content_hash','document_id','document_version','source_index_hash','ontology_release_id','ontology_version','profile_hash')},
        'label':None,'source_support':None,'schema_valid':None,'task_relevance':None,'valid_for_kg':None,
        'ontology_gap':None,'evidence_labels':[],'label_source':'unannotated'} for c in manifest['candidates']],
        'manifest_hash':manifest['manifest_hash'],'truth_is_independent_of_system_predictions':True}


def export_annotations(database,manifest=None):
    if manifest is not None: validate_manifest(manifest)
    allowed={key(c):c for c in manifest['candidates']} if manifest is not None else None
    latest={}; history=[]
    # Export is read-only and does not initialize/migrate the source database.
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        rows=conn.execute('SELECT e.event_json,r.candidate_json FROM review_events e JOIN candidate_revisions r ON r.candidate_id=e.candidate_id AND r.revision=e.revision WHERE e.active=1 ORDER BY e.created_at,e.rowid')
        for event_json,candidate_json in rows:
            event,candidate=json.loads(event_json),json.loads(candidate_json)
            identity=key(event)
            if allowed is not None and identity not in allowed: continue
            content_hash=sha256_json(candidate)
            if allowed is not None and content_hash!=allowed[identity]['candidate_content_hash']: raise ValueError('annotation source differs from frozen candidate')
            labels=validate_labels(event.get('labels') or {})
            row={**{k:candidate.get(k) for k in ('candidate_id','revision','run_id','document_id','document_version','source_index_hash','ontology_release_id','ontology_version','profile_hash','fact_key','fact_key_state','fact_identity_version')},
                **{k:labels.get(k) for k in ('source_support','schema_valid','task_relevance','valid_for_kg','ontology_gap')},
                'label':labels.get('valid_for_kg'),'candidate_content_hash':content_hash,'event_id':event['event_id'],
                'label_source':'human_confirmed','reviewer':event.get('reviewer'),'evidence_ids':event.get('evidence_ids',[]),
                'revision_truth':{'candidate_id':identity[0],'revision':identity[1],'valid_for_kg':labels.get('valid_for_kg')},
                'new_revision':event.get('new_revision')}
            history.append(row); latest[identity]=row
    return {'annotations':[latest[k] for k in sorted(latest)],'history':history,
        'selection_policy':'latest_active_event_per_candidate_revision','manifest_hash':manifest.get('manifest_hash') if manifest else None}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database');parser.add_argument('--manifest');parser.add_argument('--output',required=True)
    parser.add_argument('--template',action='store_true')
    args=parser.parse_args()
    manifest=json.loads(Path(args.manifest).read_text(encoding='utf-8')) if args.manifest else None
    if args.template:
        if manifest is None: parser.error('--template requires --manifest')
        result=annotation_template(manifest)
    else:
        if not args.database: parser.error('export requires --database')
        result=export_annotations(args.database,manifest)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as out: json.dump(result,out,ensure_ascii=False,indent=2)
    print(json.dumps({'output':str(path),'annotation_count':len(result['annotations']),'history_count':len(result.get('history',[]))}))


if __name__=='__main__': main()
