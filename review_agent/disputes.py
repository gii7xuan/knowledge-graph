"""Claim-level disagreement records, with source positions and no majority adjudication."""
from copy import deepcopy
from .contracts import sha256_json

VERSION='claim_disputes_v1'
CHECKS=('triple_support','entity_reference','relation_semantics','relation_direction','qualifiers',
        'evidence_sufficiency','counterevidence','mapping','knowledge_scope')
TYPE_FOR={'triple_support':'relation_meaning','relation_semantics':'relation_meaning','entity_reference':'referent',
    'relation_direction':'direction','qualifiers':'qualifiers','evidence_sufficiency':'evidence_sufficiency',
    'counterevidence':'counterevidence','mapping':'formal_mapping','knowledge_scope':'scope'}


def policy_for(config):
    return {'version':VERSION,'adjudication':'no_majority_override','max_rechecks_per_dispute':1,
        'scope':'one_frozen_claim_question','counter_role':'seek possible falsification, never mandatory opposition',
        'counter_absence_is_positive_support':False,'scoped_review_is_admission':False,
        'model_rule':'Address each frozen claim target in the corresponding dimension, citing exact source evidence. '
            'Explain the specific expression, issue and effect on meaning; use expression_findings for field rules. '
            'Search for possible falsification without being required to oppose. No counterevidence found is not '
            'positive support. Preserve both observations independently. Do not settle a semantic disagreement by majority.'}


def target_for(candidate,check):
    t=candidate['triple'];expression={k:deepcopy(t.get(k)) for k in ('head','surface_relation','tail','qualifiers','semantic_context')}
    text=f"{t.get('head')} — {t.get('surface_relation')} — {t.get('tail')}"
    questions={
        'triple_support':f'原文是否明确支持“{text}”这一具体关系，还是只说明相关性、所属线索或共同出现？不得替换谓词。',
        'relation_semantics':f'“{text}”中的“{t.get("surface_relation")}”是否与原文关系含义一致？逐项区分明确支持与较弱关联。',
        'relation_direction':f'原文是否支持“{text}”的头尾方向？',
        'qualifiers':f'“{text}”的条件、频率、数量、极性和模态是否完整且附着正确？字段偏好与语义改变分别判断。',
        'entity_reference':f'“{text}”中的实体是否指向原文同一对象？如需拆分，逐条核验配对与集合含义。',
        'mapping':f'“{text}”与已填写关系 {t.get("relation_id")} 的正式定义、实体身份、方向和类型约束是否匹配？',
        'evidence_sufficiency':f'当前证据是否足以确认“{text}”的完整含义？缺哪一段材料？',
        'counterevidence':f'已核验材料中是否存在可能推翻“{text}”的证据？未发现反证与找到正面支持分开记录。',
        'knowledge_scope':f'“{text}”属于哪种收录范围？范围分类不能替代事实判断。'}
    target={'check':check,'expression':expression,'question':questions[check],
        'formal_mapping':{k:t.get(k) for k in ('relation_id','head_type','tail_type')} if check=='mapping' else None}
    target['claim_id']=sha256_json([sha256_json(candidate),target])
    return target


def request_context(candidate,policy):
    return {'version':VERSION,'candidate_hash':sha256_json(candidate),'policy_hash':sha256_json(policy),
        'targets':[target_for(candidate,n) for n in CHECKS]}


def frozen(proposal):
    from .contracts import proposal_digest
    policy=proposal.get('dispute_policy');context=proposal.get('dispute_context')
    if (policy!=policy_for({}) or sha256_json(policy)!=proposal.get('dispute_policy_hash') or
        context!=request_context(proposal['candidate'],policy) or sha256_json(context)!=proposal.get('dispute_context_hash') or
        proposal_digest(proposal)!=proposal.get('proposal_digest')):
        raise ValueError('frozen claim-dispute context mismatch')
    return deepcopy(context)


def _positions(ids,evidence):
    return [{k:deepcopy(evidence[eid].get(k)) for k in ('evidence_id','quote','anchor_valid','document_id',
        'document_version','section_id','chunk_id','start','end','offset_basis','block_type')}
        for eid in ids if eid in evidence]


def _observations(vote):
    dims=vote.get('dimensions') or {};core=(dims.get('triple_support') or {}).get('status')
    primary=vote.get('primary_evidence_ids') or vote.get('supporting_evidence_ids') or []
    axis=(vote.get('review_axes') or {}).get('source',{}).get('status')
    complete=axis=='supported' if axis else vote.get('expression_status')=='faithful' and core=='supported'
    positive={'status':'found' if primary and complete else 'core_only' if primary and core=='supported' else 'unresolved' if primary else 'not_recorded',
        'evidence_ids':list(primary),'core_source_status':core,'full_source_status':axis or vote.get('source_support'),
        'not_inferred_from_counter_absence':True}
    counters=vote.get('counter_evidence_ids') or []
    counter=(dims.get('counterevidence') or {})
    search={'status':'found' if counters else 'reviewed_no_material_counter_recorded' if counter.get('status') in {'pass','not_applicable'} else 'incomplete',
        'counter_evidence_ids':list(counters),'reviewed_evidence_ids':counter.get('evidence_ids',[]),
        'note':counter.get('note'),'scope':'frozen_evidence_bundle_only','absence_is_support':False}
    return positive,search


def build_report(proposal,votes,eligible_ids=None):
    eligible=set(eligible_ids) if eligible_ids is not None else {v['voter_id'] for v in votes if v.get('response_status')=='ok'}
    evidence={e['evidence_id']:e for e in proposal['evidence_bundle']['items']}
    opinions=[];observations=[];technical=[]
    seen_voters=set()
    for vote in votes:
        if vote.get('proposal_digest') and vote['proposal_digest']!=proposal['proposal_digest']:continue
        seen_voters.add(vote.get('voter_id'))
        if vote.get('response_status')!='ok' or vote['voter_id'] not in eligible:
            technical.append({k:vote.get(k) for k in ('voter_id','response_status','error_code')});continue
        positive,counter=_observations(vote)
        observations.append({'voter_id':vote['voter_id'],'positive_support':positive,'counterevidence_search':counter})
        for check,d in (vote.get('dimensions') or {}).items():
            if check not in CHECKS:continue
            findings=[f for f in vote.get('expression_findings',[]) if f['check']==check]
            effect=('changed' if any(f.get('meaning_effect')=='changed' for f in findings) else
                    'preserved' if findings and all(f.get('meaning_effect')=='preserved' for f in findings) else
                    'not_source_judgment' if check in {'mapping','knowledge_scope','counterevidence'} else
                    'legacy_unclassified' if not vote.get('review_axes') else
                    'preserved' if d.get('status') in {'pass','supported','not_applicable'} else 'unresolved')
            kind=('field_normalization' if findings and all(f.get('kind') in {'field_placement','duplicate_qualifier'} and f.get('meaning_effect')=='preserved' for f in findings)
                  else 'modality' if any(f.get('rule_id')=='MOD-001' for f in findings) else TYPE_FOR[check])
            opinions.append({'voter_id':vote['voter_id'],'model_id':vote.get('model_id'),'check':check,
                'claim':target_for(proposal['candidate'],check),'status':d.get('status'),'reason':d.get('note'),
                'issue_type':kind,'meaning_effect':effect,'rule_ids':sorted({f['rule_id'] for f in findings if f.get('rule_id')}),
                'classification_basis':'structured_finding_or_dimension','legacy':not bool(vote.get('review_axes')),
                'evidence_ids':d.get('evidence_ids',[]),'source_positions':_positions(d.get('evidence_ids',[]),evidence),
                'relation_id':d.get('relation_id') if check=='mapping' else None})
    disputes=[]
    for check in CHECKS:
        group=[o for o in opinions if o['check']==check]
        def identity(o):
            if check=='counterevidence':
                found=next(x['counterevidence_search'] for x in observations if x['voter_id']==o['voter_id'])
                return (found['status'],tuple(sorted(found['counter_evidence_ids'])))
            return (o['status'],o['relation_id'],o['issue_type']=='field_normalization')
        differing=len({identity(o) for o in group})>1
        role_conflict=check=='triple_support' and any(x['positive_support']['status']=='unresolved' for x in observations)
        if not differing and not role_conflict:continue
        norm=bool(group) and all(o['issue_type']=='field_normalization' and o['meaning_effect']=='preserved' for o in group)
        route='apply_field_spec' if norm else 'supplement_mapping_information' if check=='mapping' else 'human_scope_review' if check=='knowledge_scope' else 'supplement_source_context'
        target=target_for(proposal['candidate'],check)
        dispute={'dispute_id':'dispute_'+sha256_json([proposal['proposal_digest'],target['claim_id']])[:24],
            'claim':target,'check':check,'issue_type':'field_normalization' if norm else TYPE_FOR[check],
            'question':target['question'],'opinions':group,'route':route,
            'status':'normalization_pending' if norm else 'human_review' if check=='knowledge_scope' else 'open',
            'positive_role_conflict':role_conflict,'majority_is_resolution':False,
            'required_material':'完整正式定义、竞争关系和实体身份' if check=='mapping' else '相关完整句子、条件、公式及前后文'}
        disputes.append(dispute)
    for member in proposal.get('committee_members',[]):
        if member['id'] not in seen_voters:technical.append({'voter_id':member['id'],'response_status':'missing','error_code':None})
    return {'version':VERSION,'candidate_hash':sha256_json(proposal['candidate']),'evidence_hash':proposal['evidence_bundle']['evidence_hash'],
        'base_proposal_digest':proposal['proposal_digest'],'opinions':opinions,'disputes':disputes,
        'evidence_observations':observations,'technical_seats':technical,'majority_is_resolution':False,
        'counter_absence_is_support':False,'admission_authorized':False}


def diagnostic_report(entry,proposal):
    from .mapping_preflight import diagnostic_report as bound_report
    report=bound_report(entry,proposal)
    if report and report.get('version')==VERSION and report.get('base_proposal_digest')==proposal['proposal_digest']:
        return report
    return None
