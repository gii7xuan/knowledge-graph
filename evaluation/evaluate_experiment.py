"""Attach independent truth only after isolated model execution has finished."""
import argparse
import hashlib
import json
from pathlib import Path
from .evaluate_review import evaluate
from .manifest import validate_manifest


def evaluate_experiment(directory,labels,*,revision_labels=None,gold_facts=None,extension_labels=None,prices=None):
    root=Path(directory)
    def read(name): return json.loads((root/name).read_text(encoding='utf-8'))
    manifest=validate_manifest(read('population_manifest.json'))
    execution=read('experiment_result.json')
    if execution['population_hash']!=manifest['manifest_hash']: raise ValueError('experiment population binding mismatch')
    report={'population_hash':manifest['manifest_hash'],'groups':[],
        'quality_claim':'metrics apply only to supplied independent annotations; missing truth remains unknown'}
    if isinstance(labels,dict): labels=labels['annotations']
    population={(r['candidate_id'],r['revision']):r for r in manifest['candidates']}
    for label in labels:
        expected=population.get((label.get('candidate_id'),label.get('revision')))
        if expected is None or label.get('candidate_content_hash')!=expected['candidate_content_hash']:
            raise ValueError('experimental truth must bind a frozen candidate revision/content')
    def group_data(value,group,default=None):
        return value.get(group,default) if isinstance(value,dict) else value
    for group in execution['groups']:
        name=group['id']
        if Path(name).name!=name: raise ValueError('invalid group directory')
        for filename in ('predictions.json','extensions.json','requests.json'):
            if hashlib.sha256((root/name/filename).read_bytes()).hexdigest()!=group.get('artifact_sha256',{}).get(filename):
                raise ValueError('experiment artifact changed: '+filename)
        predictions=read(name+'/predictions.json')
        metrics=evaluate(predictions,manifest=manifest,labels=labels,
            revision_labels=group_data(revision_labels,name),gold_facts=group_data(gold_facts,name),
            extension_records=read(name+'/extensions.json'),extension_labels=group_data(extension_labels,name),
            requests=read(name+'/requests.json'),prices=prices)
        report['groups'].append({'id':name,'method':group['method'],'ontology_mode':group['ontology_mode'],
            'ablations':group['ablations'],'configured_limits':group['limits'],'runtime_seconds':group['runtime_seconds'],'metrics':metrics})
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment',required=True);parser.add_argument('--labels',required=True);parser.add_argument('--output',required=True)
    for name in ('revision-labels','gold-facts','extension-labels','prices'): parser.add_argument('--'+name)
    args=parser.parse_args()
    def read(path): return json.loads(Path(path).read_text(encoding='utf-8')) if path else None
    report=evaluate_experiment(args.experiment,read(args.labels),revision_labels=read(args.revision_labels),
        gold_facts=read(args.gold_facts),extension_labels=read(args.extension_labels),prices=read(args.prices))
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream: json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'output':str(output),'groups':len(report['groups'])}))


if __name__=='__main__': main()
