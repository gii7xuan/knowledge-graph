"""Explicit, source-grounded patches for the twenty coverage prerequisites.

Only creates patch data. No labels, approvals, votes or candidate writes.
"""
from copy import deepcopy

REPAIR_IDS = (1,2,4,8,12,15,16,17,20,21,23,24,27,28,29,30,31,32,36,38)


def changes_for(number, candidate):
    t = candidate['triple']
    c = deepcopy(t['semantic_context'])
    sid = candidate['section_id']
    patch = {}
    attachments = deepcopy(c['qualifier_attachments'])
    note = ''
    def attach(kind, target, value):
        attachments.append({'qualifier_kind':kind,'attachment':target,'value':value,'scope_id':sid})
    def remove(kind, value=None):
        attachments[:] = [a for a in attachments if not (a['qualifier_kind']==kind and (value is None or a['value']==value))]
    if number == 1:
        c['knowledge_scope'] = 'local_definition'
        note = "Keep covolume / does not have / physical meaning and negative polarity. The assertion is inside the author's 'Think of it as' interpretive instruction. The preceding 'has been described as' is an attributed account; do not manufacture a direct factual contradiction or a universal denial."
    elif number == 2:
        patch = {'head':'work and energy','surface_relation':'were introduced in',
            'tail':'earlier sections of this text','tail_kind':'entity',
            'qualifiers':{'time_scope':None},'qualifier_status':{'time_scope':'not_stated'}}
        c['knowledge_scope'] = 'local_definition'
        remove('time','earlier sections')
        attach('discourse','tail','earlier sections of this text')
        note = 'Concept group to a location in the text; thermodynamics is not an agent. Preserve that work and energy are the major concepts referred to in the source.'
    elif number == 4:
        patch = {'qualifiers':{'applicability':'本节式(3.9)的机械能守恒表述'},
            'qualifier_status':{'applicability':'extracted'}}
        c['knowledge_scope'] = 'conditional'
        note = '补证定位式(3.9)及黏性机械能损失说明。否定仅指该局部方程形式；不否定所有伯努利方程，也不否定总能量守恒。'
    elif number == 8:
        patch = {'head':"the authors' control-volume analysis in this passage",'surface_relation':'must account for',
            'tail':'the energy associated with the described pressure and density changes of entering or leaving material',
            'qualifiers':{'condition':'material entering or leaving the fixed control volume can have a different pressure and density'},
            'qualifier_status':{'condition':'extracted'}}
        c['knowledge_scope'] = 'derivation'
        remove('condition','Because of this')
        attach('condition','relation',patch['qualifiers']['condition'])
        attach('discourse','relation.reason','Because of this')
        note = 'Methodological accounting requirement. Resolve we and these changes through the contiguous control-volume discussion; retain must, without inventing a physical causal relation.'
    elif number == 12:
        patch = {'head':'the pressure ratio across the shock in this passage',
            'surface_relation':'is used in this passage to characterize','tail':'the strength of the shock'}
        c['knowledge_scope'] = 'local_definition'
        note = 'The explicit parenthetical pressure ratio (i.e., the strength) supplies a local characterization. Ratio, pressure and shock intensity are distinct roles; this is not global identity or class equivalence. Original unknown polarity remains unknown pending review.'
    elif number == 15:
        patch = {'head':'the molecular packing, collision frequency, and gas pressure described in this passage',
            'surface_relation':'are related by the local comparative claim',
            'tail':'The more tightly packed the molecules are, the more collisions occur—the higher the pressure is.',
            'tail_kind':'proposition','qualifiers':{'quantifier':None},'qualifier_status':{'quantifier':'not_stated'}}
        remove('degree')
        attach('degree','relation','The more tightly packed ... the more collisions ... the higher the pressure')
        c['knowledge_scope'] = 'conditional'
        note = 'Keep the whole comparative proposition. Do not silently distribute it into separate unconditional causal edges; more marks comparative degree rather than a count of objects.'
    elif number == 16:
        patch = {'head':'the functional relationship described here as an equation of state',
            'surface_relation':'varies with','tail':'the substance whose state it describes'}
        c['knowledge_scope'] = 'local_definition'
        note = 'The object denotes the substance argument of the state relationship, not another function or a class-equivalence target.'
    elif number == 17:
        patch = {'head':"the authors' analysis in the Eulerian frame",'surface_relation':'uses',
            'tail':'a chosen real or imaginary control volume',
            'qualifiers':{'applicability':'the Eulerian-frame discussion in this passage'},
            'qualifier_status':{'applicability':'extracted'}}
        c['knowledge_scope'] = 'derivation'
        note = 'We chose the control volume in this analysis. The frame is the setting, not the choosing agent; retain real or imaginary without distribution.'
    elif number == 20:
        patch = {'head':'the second term on the right-hand side of Equation 2.129 in this text',
            'surface_relation':'is described by the text as indicating','tail':'how much material has left the control volume',
            'tail_kind':'proposition', 'qualifiers':{'polarity':'positive','modality':'asserted',
                'applicability':'the explanation of Equation 2.129 in this text'},
            'qualifier_status':{'polarity':'extracted','modality':'extracted','applicability':'extracted'}}
        c['knowledge_scope'] = 'local_definition'
        note = 'Contiguous original source identifies Equation 2.129 and explicitly says what its second term tells us. Preserve that affirmative textual statement; do not infer cumulative mass or replace material with another property.'
    elif number == 21:
        patch = {'head':'the diaphragm separating the gas regions in this passage','surface_relation':'can undergo',
            'tail':'a rupture described as very quick','tail_kind':'event'}
        remove('degree','very quickly')
        attach('degree','tail.event_manner','very quickly')
        c['knowledge_scope'] = 'example'
        note = 'Rupture is the event; very quickly is its manner. Keep the original can and unknown polarity; do not create a physical object named very quickly or add device instructions.'
    elif number == 23:
        patch = {'surface_relation':'is to be treated in this passage as',
            'tail':'a number that allows for a better fit to observed experimental data',
            'qualifiers':{'quantifier':None,'modality':'unknown'},
            'qualifier_status':{'quantifier':'not_stated','modality':'unknown'}}
        remove('quantity','simply')
        attach('discourse','relation','Think of it as ... but as simply a number')
        c['knowledge_scope'] = 'local_definition'
        note = 'Retain the interpretive directive and the contrast with the preceding negated characterization. Simply is discourse, not a numerical quantifier; directive force is preserved lexically rather than normalized to asserted.'
    elif number == 24:
        patch = {'head':"the authors' analysis in this passage",'surface_relation':'assumes',
            'qualifiers':{'quantifier':'one inlet and one outlet'},'qualifier_status':{'quantifier':'extracted'}}
        remove('quantity','one inlet and one outlet')
        attach('quantity','tail.inlet_and_outlet_roles','one inlet and one outlet')
        c['knowledge_scope'] = 'derivation'
        note = 'One attaches separately to inlet and outlet within a conjunctive local assumption. Preserve assumed; do not turn it into an asserted structure of every control volume.'
    elif number == 27:
        patch = {'head':'本段伯努利方程的沿流线积分推导','surface_relation':'以……为前提之一',
            'tail':'质量力为有势力','tail_kind':'proposition',
            'qualifiers':{'condition':'流动定常（各参量对时间的偏导数为零），且（流体为正压性的，或流体不可压缩）',
                'applicability':'本段理想流体欧拉运动方程沿流线积分的推导'},
            'qualifier_status':{'condition':'extracted','applicability':'extracted'}}
        c['knowledge_scope'] = 'derivation'
        remove('condition','如果流动满足')
        attach('condition','relation',patch['qualifiers']['condition'])
        note = '有势质量力是前提之一；其余条件为定常 AND（正压 OR 不可压缩）。不是所有质量力的无条件分类。'
    elif number == 28:
        c['knowledge_scope'] = 'local_definition'
        remove('modality','asserted (attributed description)')
        attach('discourse','relation.attribution','has been described as')
        note = 'An attributed description of covolume, preserving its source voice and both molecular-size/intermolecular-force components. Not a universal identity or an automatic contradiction of the neighboring interpretive instruction.'
    elif number == 29:
        patch = {'tail_kind':'entity'}
        c['knowledge_scope'] = 'conditional'
        note = 'The tail denotes a fixed-mass system in the stated process context, not an independent event that has been asserted to happen. Retain the can and the full fixed-mass/process condition.'
    elif number == 30:
        patch = {'head':'the shock described in this passage','surface_relation':'develops through',
            'tail':'a development event occurring over a very small distance','tail_kind':'event',
            'qualifiers':{'quantifier':None,'applicability':'the shock model discussed in this paragraph'},
            'qualifier_status':{'quantifier':'not_stated','applicability':'extracted'}}
        remove('quantity','a very small distance')
        attach('quantity','tail.spatial_extent','a very small distance')
        c['knowledge_scope'] = 'conditional'
        note = 'Separate the development event from its qualitative spatial extent. The repeated tail development is not an independent object; do not add numerical scales or operational calculations.'
    elif number == 31:
        patch = {'head':'the characteristics involved in the rarefaction-wave reflection described here',
            'surface_relation':'tend to undergo','tail':'bending during that reflection','tail_kind':'event',
            'qualifiers':{'modality':'unknown','applicability':'the rarefaction-wave reflection described in this passage'},
            'qualifier_status':{'modality':'unknown','applicability':'extracted'}}
        attach('modality','relation','tend to')
        c['knowledge_scope'] = 'example'
        note = 'Resolve the characteristics through the source paragraph. Preserve lexical tendency; the existing enum does not establish equivalence with frequency usually, so the modality status stays explicitly unknown.'
    elif number == 32:
        patch = {'surface_relation':'can be determined through',
            'tail':'the source-described determination using the universal gas constant and the molar mass',
            'tail_kind':'event'}
        attach('discourse','tail.input_roles','the universal gas constant and the molar mass')
        attach('discourse','tail.source_operation','by dividing the latter by the molar mass')
        c['knowledge_scope'] = 'derivation'
        note = 'Represent the described determination with both input roles. Do not make the universal gas constant the sole input, reverse the operation or perform a calculation.'
    elif number == 36:
        patch = {'qualifiers':{'modality':'unknown','condition':None},
            'qualifier_status':{'modality':'unknown','condition':'not_stated'}}
        remove('condition','because of path dependency considerations')
        attach('discourse','relation.reason','because of path dependency considerations')
        attach('discourse','relation.author_practice','but for our purposes, we shall ignore this fact')
        c['knowledge_scope'] = 'local_definition'
        note = 'Keep would require in the predicate and modality attachment, and preserve the original δ double-stroke text verified in the PDF. Because gives a reason, not an antecedent. The authors ignore the convention here; they do not assert that they used it. Unencoded normative modality remains unknown, never asserted.'
    elif number == 38:
        patch = {'head':'本段不可压缩流体的密度','surface_relation':'满足时空约束',
            'tail':'各处密度相等且不随时间变化','tail_kind':'proposition'}
        c['knowledge_scope'] = 'conditional'
        note = '将密度量与其约束命题分开，保留空间各处相等 AND 时间不变及不可压缩条件；与019同源，不生成独立证据票。'
    elif number == 11:
        return {'value':t['tail'],'datatype':'http://www.w3.org/2001/XMLSchema#string'}
    else:
        raise ValueError('not in the authorized expression repair cohort')
    # Keep original mentions immutable through merge_triple_patch, while describing
    # the new expression's referents explicitly. Existing attachments stay visible.
    for side in ('head','tail'):
        if side in patch:
            c['entity_mentions'][side] = {'entity_surface':patch[side],'canonical_entity':patch[side],
                'referent_scope':'the source-bounded referent described in the complete frozen evidence packet'}
    c['relation_meaning'] = {'semantic_equivalence':'needs_review','note':note}
    c['qualifier_attachments'] = []
    seen = set()
    for a in attachments:
        key = (a['qualifier_kind'],a['attachment'],a['value'])
        if key not in seen:
            c['qualifier_attachments'].append({**a,'scope_id':sid})
            seen.add(key)
    patch['semantic_context'] = c
    return patch
