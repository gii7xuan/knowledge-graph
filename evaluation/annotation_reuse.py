"""Carry only named human judgments whose actual axis dependencies are unchanged."""
from copy import deepcopy
from review_agent.contracts import sha256_json

VERSION='axis_material_equivalence_v1'


def axis_material(packet,axis):
    candidate=packet['candidate'];triple=deepcopy(candidate.get('triple',{}))
    if axis!='mapping':
        for key in ('relation_id','relation_label','relation_semantic_hash','mapping_status','head_type','tail_type'):
            triple.pop(key,None)
    result={'triple':triple,'source':{k:candidate.get(k) for k in ('document_id','document_version','source_index_hash')},
        'evidence':packet['evidence_bundle'].get('items',[])}
    if axis=='expression':result.update(field_spec=packet.get('field_spec'),
        unavailable_field_spec_hash=packet['binding'].get('field_spec_hash') if packet.get('field_spec') is None else None)
    if axis=='mapping':result.update(ontology_context=packet.get('ontology_context'),
        ontology_version=candidate.get('ontology_version'),ontology_release_id=candidate.get('ontology_release_id'),profile_hash=candidate.get('profile_hash'),
        unavailable_context_hash=packet['binding'].get('ontology_context_hash') if packet.get('ontology_context') is None else None)
    if axis=='scope':result['scope_policy']=packet.get('scope_policy')
    return result


def reuse(previous_packet,previous_annotation,current_packet):
    from .review_quality import validate_annotation,annotation_template,quality_report
    quality_report([previous_packet]);quality_report([current_packet])
    if previous_annotation.get('status')!='resolved':raise ValueError('reuse starts from a direct, resolved named-human annotation')
    truth=validate_annotation(previous_annotation,previous_packet)
    equal={a:axis_material(previous_packet,a)==axis_material(current_packet,a) for a in truth['four_axes']}
    result=annotation_template(current_packet)
    if not any(equal.values()):return result
    result.update(status='inherited',reviewer=truth['reviewer'],annotated_at=truth['annotated_at'],
        full_packet_reviewed=False,saw_model_conclusions=truth['saw_model_conclusions'],
        four_axes={a:truth['four_axes'][a] if unchanged else None for a,unchanged in equal.items()},
        admission_eligible=truth.get('admission_eligible') if all(equal.values()) else None,
        needs_revision=truth.get('needs_revision') if equal['expression'] and equal['source'] else None,
        mapping_correct=truth.get('mapping_correct') if equal['mapping'] else None,
        evidence_ids=deepcopy(truth['evidence_ids']),note=truth['note'],
        reuse_proof={'version':VERSION,'previous_packet':deepcopy(previous_packet),
            'previous_annotation':deepcopy(previous_annotation),'equivalent_axes':equal,
            'current_material_hashes':{a:sha256_json(axis_material(current_packet,a)) for a in equal},
            'new_human_review_performed':False})
    return result


def validate(annotation,current_packet):
    proof=annotation.get('reuse_proof') or {}
    if proof.get('version')!=VERSION or proof.get('new_human_review_performed') is not False:
        raise ValueError('exact axis-equivalence proof required')
    expected=reuse(proof['previous_packet'],proof['previous_annotation'],current_packet)
    if expected!=annotation:raise ValueError('reused human judgment or equivalent material was altered')
    return deepcopy(annotation)


def main():
    import argparse,json
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('previous_packets','previous_annotations','current_packets','output'):
        parser.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    args=parser.parse_args()
    def read(path,key):
        value=json.loads(path.read_text(encoding='utf-8'));return value.get(key,[]) if isinstance(value,dict) else value
    previous={p['packet_id']:p for p in read(args.previous_packets,'samples')}
    labels={a['packet_id']:a for a in read(args.previous_annotations,'annotations')}
    current=read(args.current_packets,'samples');result=[]
    from .review_quality import annotation_template
    for packet in current:
        matches=[p for p in previous.values() if p['candidate']['candidate_id']==packet['candidate']['candidate_id'] and p['packet_id'] in labels]
        if len(matches)>1:raise ValueError('select one exact previous annotated version per candidate')
        result.append(reuse(matches[0],labels[matches[0]['packet_id']],packet) if matches else annotation_template(packet))
    with args.output.open('x',encoding='utf-8') as stream:json.dump({'annotations':result},stream,ensure_ascii=False,indent=2)


if __name__=='__main__':main()
