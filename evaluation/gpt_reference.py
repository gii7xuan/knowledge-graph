"""Version-bound GPT development references, explicitly distinct from human gold."""
from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path

from review_agent.contracts import sha256_json
from review_agent.annotation_contract import DIMENSIONS
from .freeze_shared_ontology import ROOT,read,write

SOURCE=ROOT/'outputs/preannotation_followup_20261118/ai_preannotations.json'


def build_reference(source=SOURCE):
    source=Path(source);data=read(source)
    manifest=read(ROOT/'outputs/development_pool_20261117/pilot/manifest.json')
    samples=read(ROOT/'outputs/development_pool_20261117/pilot/review_samples.json')['samples']
    indexed={r['sample_id']:r for r in samples}
    if data.get('label_source')!='ai_preannotation' or data['manifest_hash']!=manifest['manifest_hash']:
        raise ValueError('GPT reference source or pilot manifest changed')
    rows=[];seen=set()
    for row in data['annotations']:
        sample=indexed.get(row['sample_id'])
        if not sample or row['sample_id'] in seen or row['binding']!=sample['binding']:
            raise ValueError('GPT labels require an exact, unique original packet binding')
        if sha256_json(sample['candidate'])!=row['binding']['candidate_content_hash']:
            raise ValueError('GPT reference candidate changed')
        labels=deepcopy(row['raw_annotation']['labels'])
        if set(labels)!=set(DIMENSIONS) or any(v not in DIMENSIONS[k] for k,v in labels.items()):
            raise ValueError('invalid or incomplete GPT reference labels')
        seen.add(row['sample_id'])
        rows.append({'sample_id':row['sample_id'],'binding':deepcopy(row['binding']),'labels':labels,
            'label_source':'gpt_preannotation','human_confirmed':False,
            'note':row['raw_annotation'].get('note'),
            'supporting_evidence_ids':deepcopy(row['raw_annotation'].get('supporting_evidence_ids',[])),
            'counter_evidence_ids':deepcopy(row['raw_annotation'].get('counter_evidence_ids',[]))})
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    return {'version':'gpt_development_reference_v1','partition':'development',
        'data_version':'gpt_development_reference_'+source_hash[:16],
        'authorization':'user explicitly requested reuse of previous GPT annotations',
        'source_path':str(source),'source_sha256':source_hash,
        'source_manifest_hash':data['manifest_hash'],'label_source':'gpt_preannotation',
        'human_gold_count':0,'independent_truth_established':False,
        'allowed_uses':['development_reference_comparison','exploratory_reliability_estimation','development_threshold_selection'],
        'test_labels_inferred':False,'admission_preference_is_admission_truth':False,
        'annotations':rows,'label_distributions':{axis:dict(Counter(r['labels'][axis] for r in rows)) for axis in DIMENSIONS},
        'model_reliability_statistics':'not computed without matching model judgments'}


def compare(predictions,reference):
    """Compare explicit axis labels only, with exact candidate/material bindings."""
    indexed={r['binding']['candidate_id']:r for r in reference['annotations']};result=[]
    for p in predictions:
        ref=indexed.get(p['candidate_id'])
        if not ref:raise ValueError('no GPT reference for this candidate; new candidates cannot inherit labels')
        if any(p.get(k)!=ref['binding'].get(k) for k in ('revision','candidate_content_hash')):
            raise ValueError('GPT reference version/content binding mismatch')
        if p.get('binding')!=ref['binding']:
            raise ValueError('GPT reference evidence/ontology packet mismatch')
        result.append((p,ref))
    metrics={}
    for axis in DIMENSIONS:
        known=[(p,r) for p,r in result if r['labels'][axis]!='unresolved' and
               p.get('axis_labels',{}).get(axis) in DIMENSIONS[axis] and p['axis_labels'][axis]!='unresolved']
        n=sum(p['axis_labels'][axis]==r['labels'][axis] for p,r in known)
        metrics[axis]={'agreement_count':n,'comparable_count':len(known),'population':len(result),
                       'value':n/len(known) if known else None}
    return {'label_source':'gpt_preannotation','interpretation':'agreement with GPT reference; not independent human accuracy',
            'human_gold_count':0,'metrics':metrics}


def export(output,source=SOURCE):
    reference=build_reference(source);write(Path(output),reference);return reference


def reliability(comparisons,reference):
    """Versioned exploratory counts; never invent history when model votes are absent."""
    models={};seen=set();axes={'source':'source_support','expression':'expression_status',
                              'mapping':'mapping_status','scope':'knowledge_scope'}
    indexed={r['binding']['candidate_id']:r for r in reference['annotations']}
    for row in comparisons:
        compare([row],reference)  # Exact packet and candidate binding, including evidence.
        key=(row['voter_id'],row['candidate_id'])
        if key in seen:raise ValueError('duplicate model/candidate reliability observation')
        seen.add(key)
        identity={k:row[k] for k in ('model_identity','model_family')}
        if any(not isinstance(v,str) or not v for v in identity.values()):raise ValueError('model identity and family required')
        record=models.setdefault(row['voter_id'],{**identity,'axes':{}})
        if any(record[k]!=v for k,v in identity.items()):raise ValueError('model identity changed within reliability data')
        ref=indexed[row['candidate_id']]
        for axis,label in axes.items():
            expected=ref['labels'][label];actual=row.get('axis_labels',{}).get(label)
            if expected=='unresolved' or actual not in DIMENSIONS[label] or actual=='unresolved':continue
            stats=record['axes'].setdefault(axis,{'correct':0,'evaluated':0})
            stats['evaluated']+=1;stats['correct']+=int(actual==expected)
    return {'label_source':'gpt_preannotation','human_gold_count':0,
        'interpretation':'exploratory agreement statistics against GPT development labels',
        'policy_reliability':{'data_version':reference['data_version'],'partition':'development','models':models},
        'exact_model_candidate_comparisons':len(seen)}
