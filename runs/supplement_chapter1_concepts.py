"""Explicitly attributed assistant extraction after repeated remote timeouts."""
import json
from pathlib import Path
from ontologyBuild.ontology_candidate import normalize_extraction_result

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'runs/ontology/20261027_chapter1_recovery'
CHUNK='chunk_87da7da6c9ea'


def main():
    index=json.loads((RUN/'document_index.json').read_text(encoding='utf-8'))
    chunk=next(c for c in index['chunks'] if c['chunk_id']==CHUNK)
    # Deliberately no equations, quantities, inferred definitions or relations.
    names=['量纲分析','空气阻力','迎面阻力','切向阻力','空气密度','马赫数','雷诺数','空气动力学']
    result={k:[] for k in ('candidate_classes','candidate_object_properties','candidate_datatype_properties',
        'candidate_constraints','ontology_mapping','uncertain_candidates')}
    for name in names:
        assert name in chunk['content'], name
        result['candidate_classes'].append({'name':name,'normalized_name':name,'parent_candidate':None,
            'definition_candidate':'','status':'new_candidate','matched_existing_class':None,
            'evidence':[{'chunk_id':CHUNK,'text':name}],'confidence':0.7,
            'reason':'本次助手从原文明示术语补录，仅作为概念候选；类别建模与同义词归并待审核。'})
    normalize_extraction_result(result,chunk)
    path=RUN/'recovery_checkpoint.json'
    checkpoint=json.loads(path.read_text(encoding='utf-8'))
    prior=checkpoint[CHUNK]
    assert prior['status']=='failed', 'do not overwrite an existing successful model extraction'
    checkpoint[CHUNK]={'status':'succeeded','candidate_count':len(names),'result':result,
        'extraction_method':'current_session_assistant_concept_extraction','remote_failure':prior,
        'semantic_review':'pending','definition_and_relation_inference_performed':False}
    path.write_text(json.dumps(checkpoint,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'assistant_supplement_chunk':CHUNK,'concept_candidates':len(names),'semantic_review':'pending'},ensure_ascii=False))


if __name__=='__main__': main()
