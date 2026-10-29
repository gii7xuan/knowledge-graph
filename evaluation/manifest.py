"""Freeze the entire candidate population; missing results never shrink it."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from review_agent.contracts import sha256_json

VERSION='evaluation_population_v1'


def key(row):
    if not isinstance(row,dict) or not isinstance(row.get('candidate_id'),str) or not row['candidate_id'] or type(row.get('revision')) is not int or row['revision'] < 1:
        raise ValueError('candidate_id and a positive integer revision are required')
    return row['candidate_id'],row['revision']


def freeze_manifest(candidates, *, run_id, settings=None):
    items,seen=[],set()
    for c in candidates:
        identity=key(c)
        if identity in seen: raise ValueError('duplicate candidate revision in population')
        seen.add(identity)
        items.append({**{k:c.get(k) for k in ('candidate_id','revision','document_id','document_version','source_index_hash',
            'ontology_release_id','ontology_version','profile_hash','fact_identity_version','fact_key','fact_key_state')},
            'candidate_content_hash':sha256_json(c)})
    items.sort(key=key)
    manifest={'version':VERSION,'run_id':run_id,'settings':settings or {},'population_count':len(items),'candidates':items}
    manifest['manifest_hash']=sha256_json(manifest)
    return manifest


def validate_manifest(manifest):
    if (not isinstance(manifest,dict) or manifest.get('version') != VERSION
            or sha256_json({k:v for k,v in manifest.items() if k != 'manifest_hash'}) != manifest.get('manifest_hash')):
        raise ValueError('frozen population manifest content/hash mismatch')
    rows=manifest.get('candidates')
    if not isinstance(rows,list) or len(rows) != manifest.get('population_count') or len({key(r) for r in rows}) != len(rows):
        raise ValueError('frozen population is incomplete or duplicated')
    return manifest


def align_results(manifest,results,labels=None):
    validate_manifest(manifest)
    population={key(c):c for c in manifest['candidates']}
    def indexed(rows,description):
        result={}
        for row in rows:
            identity=key(row)
            if identity not in population: raise ValueError(description+' references a foreign/stale candidate revision')
            if identity in result: raise ValueError(description+' duplicates a candidate revision')
            if row.get('candidate_content_hash') and row['candidate_content_hash'] != population[identity]['candidate_content_hash']:
                raise ValueError(description+' candidate content binding mismatch')
            result[identity]=row
        return result
    predictions=indexed(results,'prediction'); annotations=indexed(labels or [],'label')
    rows=[]
    for identity,candidate in population.items():
        prediction=predictions.get(identity,{})
        # Revisions and source/version bindings come from the frozen table.
        for field in ('document_version','source_index_hash','ontology_release_id','ontology_version','profile_hash'):
            if field in prediction and prediction[field] != candidate[field]: raise ValueError('prediction '+field+' binding mismatch')
        row={**prediction,**candidate,'prediction_missing':identity not in predictions}
        if labels is not None:
            from review_agent.labels import validate_labels
            annotation=validate_labels(annotations.get(identity,{}))
            if annotation.get('label') is not None and (type(annotation['label']) is not int or annotation['label'] not in (0,1)): raise ValueError('gold label must be 0, 1 or null')
            if annotation.get('label') is not None and annotation.get('valid_for_kg') is not None and annotation['label']!=annotation['valid_for_kg']: raise ValueError('gold label conflicts with valid_for_kg')
            for field in ('label','source_support','schema_valid','task_relevance','valid_for_kg','evidence_ids','revision_truth','ontology_gap','evidence_labels'):
                row.pop(field,None)
            row.update({k:v for k,v in annotations.get(identity,{}).items() if k in
                {'label','source_support','schema_valid','task_relevance','valid_for_kg','evidence_ids','revision_truth','ontology_gap','evidence_labels'}})
            if row.get('label') is None: row['label']=row.get('valid_for_kg')
        rows.append(row)
    return rows


def save_manifest(manifest,path):
    validate_manifest(manifest)
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    text=json.dumps(manifest,ensure_ascii=False,indent=2)
    # Exclusive creation prevents a later experiment from silently replacing its denominator.
    with path.open('x',encoding='utf-8') as output: output.write(text+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True); parser.add_argument('--run-id',required=True,action='append'); parser.add_argument('--output',required=True)
    parser.add_argument('--candidates-output');parser.add_argument('--documents-output')
    args=parser.parse_args()
    with closing(sqlite3.connect(Path(args.database).resolve().as_uri()+'?mode=ro',uri=True)) as connection:
        connection.execute('BEGIN')
        candidates=[];documents=[]
        for run_id in dict.fromkeys(args.run_id):
            run=connection.execute('SELECT manifest_json FROM runs WHERE run_id=?',(run_id,)).fetchone()
            if not run: raise ValueError('unknown run')
            document=json.loads(run[0]).get('document_path')
            if document: documents.append(document)
            elif args.documents_output: raise ValueError('source document path missing from run')
            candidates.extend(json.loads(row[0]) for row in connection.execute('SELECT r.candidate_json FROM candidates c JOIN candidate_revisions r ON r.candidate_id=c.candidate_id AND r.revision=c.current_revision WHERE c.run_id=? ORDER BY c.candidate_id',(run_id,)))
    manifest=freeze_manifest(candidates,run_id=','.join(args.run_id),settings={'source_run_ids':list(dict.fromkeys(args.run_id))})
    save_manifest(manifest,args.output)
    for path,value in ((args.candidates_output,candidates),(args.documents_output,sorted(set(documents)))):
        if path:
            target=Path(path);target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('x',encoding='utf-8') as stream: json.dump(value,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'output':args.output,'population_count':manifest['population_count'],'manifest_hash':manifest['manifest_hash']}))


if __name__=='__main__': main()
