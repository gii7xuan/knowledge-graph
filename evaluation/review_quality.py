"""Version-bound quality denominators; absent human truth is never a zero score."""
from collections import Counter
from copy import deepcopy
from review_agent.contracts import sha256_json

VERSION='four_axis_quality_v1'
AXES={'source':{'supported','contradicted','insufficient','unresolved'},
      'expression':{'standard','lossless_normalization','semantic_revision','unresolved'},
      'mapping':{'valid','conflict','unassigned','context_incomplete'},'scope':{'include','exclude','unclear'}}
TITLES={'auto_admission_precision':'自动准入精确率','false_admission_rate':'错误放行率',
    'usable_auto_admission_rate':'可用候选自动通过率','revision_success_rate':'修订成功率',
    'unnecessary_revision_rate':'不必要修订率','mapping_accuracy':'本体映射正确率'}


def bind_packet(candidate,proposal,evidence,policy,*,system=None):
    binding={'candidate_id':candidate['candidate_id'],'revision':candidate['revision'],
        'candidate_content_hash':sha256_json(candidate),'proposal_digest':proposal.get('proposal_digest'),
        'evidence_hash':evidence.get('evidence_hash'),'ontology_context_hash':proposal.get('ontology_context_hash'),
        'ontology_version':candidate.get('ontology_version'),'profile_hash':candidate.get('profile_hash'),
        'scope_policy_hash':sha256_json(policy),'field_spec_hash':proposal.get('field_spec_hash')}
    result={'binding':binding,'candidate':deepcopy(candidate),'evidence_bundle':deepcopy(evidence),
        'ontology_context':deepcopy(proposal.get('ontology_context')),'scope_policy':deepcopy(policy),
        'field_spec':deepcopy(proposal.get('field_spec')),'ontology_check':deepcopy(proposal.get('ontology_check')),
        'mapping_preflight':deepcopy(proposal.get('mapping_preflight')),
        'field_standardization':deepcopy(proposal.get('field_standardization'))}
    # Identity covers the complete material shown to a human, not just the name.
    result['packet_id']='packet_'+sha256_json(result)
    result['system']=deepcopy(system or {})
    return result


def annotation_template(packet):
    return {'packet_id':packet['packet_id'],'binding':deepcopy(packet['binding']),'status':'pending',
        'annotated_by':'human','reviewer':None,'annotated_at':None,'full_packet_reviewed':False,
        'saw_model_conclusions':None,'four_axes':{name:None for name in AXES},
        'admission_eligible':None,'needs_revision':None,'mapping_correct':None,'evidence_ids':[],'note':None}


def validate_annotation(annotation,packet):
    if annotation.get('packet_id')!=packet['packet_id'] or annotation.get('binding')!=packet['binding']:
        raise ValueError('human annotation must bind the exact candidate, evidence, ontology and task policy')
    if annotation.get('status')=='pending':return None
    if (annotation.get('status')!='resolved' or annotation.get('annotated_by')!='human' or
        not annotation.get('reviewer') or not annotation.get('annotated_at') or
        annotation.get('full_packet_reviewed') is not True or type(annotation.get('saw_model_conclusions')) is not bool):
        raise ValueError('resolved quality truth requires named human provenance and complete packet review')
    axes=annotation.get('four_axes',{})
    if set(axes)!=set(AXES) or any(value is not None and value not in AXES[name] for name,value in axes.items()):
        raise ValueError('invalid four-axis human annotation')
    eligible=annotation.get('admission_eligible')
    if eligible is not None and type(eligible) is not bool:raise ValueError('admission eligibility must be a human boolean or unknown')
    for key in ('needs_revision','mapping_correct'):
        if annotation.get(key) is not None and type(annotation[key]) is not bool:raise ValueError(key+' must be a human boolean or unknown')
    if eligible is True and axes!={'source':'supported','expression':'standard','mapping':'valid','scope':'include'}:
        raise ValueError('admission gold requires all four complete admission conditions')
    ids=annotation.get('evidence_ids',[])
    available={e['evidence_id'] for e in packet['evidence_bundle'].get('items',[])}
    if not isinstance(ids,list) or any(not isinstance(e,str) for e in ids) or set(ids)-available:
        raise ValueError('human annotation cites foreign evidence')
    if any(v is not None for v in axes.values()) and (not ids or not str(annotation.get('note') or '').strip()):
        raise ValueError('human axis judgments require current source references and explanation')
    return deepcopy(annotation)


def metric(name,numerator,denominator,*,unknown=0,reason=None,denominator_note=''):
    value=None if denominator==0 or unknown else numerator/denominator
    return {'id':name,'title':TITLES[name],'value':value,'numerator':numerator,'denominator':denominator,
        'unknown_count':unknown,'status':'not_evaluable' if value is None else 'evaluated',
        'reason':reason or ('没有符合分母条件的样本' if not denominator else '缺少匹配版本的人工标注或系统结果' if unknown else None),
        'denominator_note':denominator_note}


def quality_report(packets,annotations=(),*,revision_operations=(),revision_assessments=(),partition='development',
                   shadow_revision_operations=(),shadow_revision_assessments=()):
    for p in packets:
        material={k:v for k,v in p.items() if k not in {'packet_id','system','sample_id','human_and_model_same_packet'}}
        if p['packet_id']!='packet_'+sha256_json(material) or p['binding']['candidate_content_hash']!=sha256_json(p['candidate']):
            raise ValueError('review packet changed after its version binding was frozen')
    indexed={p['packet_id']:p for p in packets}
    if len(indexed)!=len(packets):raise ValueError('duplicate review packet')
    annotations_by_id={}
    for annotation in annotations:
        identity=annotation.get('packet_id')
        if identity not in indexed or identity in annotations_by_id:raise ValueError('foreign or duplicate human annotation')
        annotations_by_id[identity]=validate_annotation(annotation,indexed[identity])
    rows=[(p,annotations_by_id.get(p['packet_id'])) for p in packets]
    auto=[(p,a) for p,a in rows if p['system'].get('auto_admitted') is True]
    known_auto=[a for _,a in auto if a and type(a.get('admission_eligible')) is bool]
    known=[(p,a) for p,a in rows if a and type(a.get('admission_eligible')) is bool]
    invalid=[(p,a) for p,a in known if a['admission_eligible'] is False]
    usable=[(p,a) for p,a in known if a['admission_eligible'] is True]
    missing_gold=len(rows)-len(known)
    standard=[(p,a) for p,a in rows if a and a['four_axes']['expression']=='standard']
    mapping=[(p,a) for p,a in rows if p['system'].get('mapping_completed') is True]
    known_mapping=[a for _,a in mapping if a and a['four_axes']['mapping'] in {'valid','conflict'}]
    operations={o['revision_id']:o for o in revision_operations}
    if len(operations)!=len(revision_operations):raise ValueError('duplicate applied revision operation')
    assessed={}
    for assessment in revision_assessments:
        operation=operations.get(assessment.get('revision_id'))
        if not operation or assessment['revision_id'] in assessed:raise ValueError('foreign or duplicate revision assessment')
        for key in ('parent_packet_id','child_packet_ids'):
            if assessment.get(key)!=operation[key]:raise ValueError('revision truth must bind the exact parent and every independently reviewed child')
        if operation['parent_packet_id'] not in indexed or not operation['child_packet_ids'] or any(x not in indexed for x in operation['child_packet_ids']):
            raise ValueError('revision packets missing')
        if (assessment.get('annotated_by')!='human' or not assessment.get('reviewer') or not assessment.get('annotated_at') or
            type(assessment.get('original_issue_resolved')) is not bool or type(assessment.get('introduced_new_problem')) is not bool):
            raise ValueError('revision outcome requires independent human review of original and new problems')
        children=[annotations_by_id.get(x) for x in operation['child_packet_ids']]
        if not all(children):raise ValueError('every split child needs its own new-version annotation')
        assessed[assessment['revision_id']]=assessment
    results=[
        metric('auto_admission_precision',sum(a['admission_eligible'] for a in known_auto),len(auto),unknown=len(auto)-len(known_auto),
            reason='没有自动准入候选，暂不可评估' if not auto else None,denominator_note='所有已实际自动准入的候选；缺失真值不缩小分母'),
        metric('false_admission_rate',sum(p['system'].get('auto_admitted') is True for p,_ in invalid),len(invalid),unknown=missing_gold,
            denominator_note='人工确认不满足完整准入条件的候选；全体缺失真值另计'),
        metric('usable_auto_admission_rate',sum(p['system'].get('auto_admitted') is True for p,_ in usable),len(usable),unknown=missing_gold,
            denominator_note='人工确认满足全部准入条件的候选'),
        metric('revision_success_rate',sum(a['original_issue_resolved'] and not a['introduced_new_problem'] for a in assessed.values()),len(operations),
            unknown=len(operations)-len(assessed),reason='没有实际应用的修订；修订建议不计成功' if not operations else None,
            denominator_note='实际应用的修订操作；拆分须全部子候选独立验证'),
        metric('unnecessary_revision_rate',sum(p['system'].get('revision_requested') is True for p,_ in standard),len(standard),
            unknown=sum(not a or a['four_axes']['expression'] in {None,'unresolved'} for _,a in rows)+sum(p['system'].get('prediction_observed') is not True for p,_ in standard),
            denominator_note='人工确认原始表达规范的候选；范围排除、等待映射和技术失败不算修订要求'),
        metric('mapping_accuracy',sum(a['four_axes']['mapping']=='valid' for a in known_mapping),len(mapping),unknown=len(mapping)-len(known_mapping),
            reason='没有完成的正式映射，暂不可评估' if not mapping else None,
            denominator_note='已完成且有正式ID的映射；人工同时核验身份、关系语义和类型约束')]
    return {'version':VERSION,'partition':partition,'quality_claim':'开发回归，不能作为独立泛化结论' if partition=='development' else '仅适用于已核验的独立标注样本',
        'population':len(rows),'human_resolved':sum(bool(a) for _,a in rows),'human_admission_truth_missing':missing_gold,
        'auto_admitted':len(auto),'all_manual_is_success':False,'metrics':results,
        'recommendation_metrics':recommendation_metrics(rows,indexed,annotations_by_id,shadow_revision_operations,shadow_revision_assessments),
        'axis_annotation_counts':{name:dict(Counter(a['four_axes'][name] for _,a in rows if a and a['four_axes'][name] is not None)) for name in AXES}}


def recommendation_metrics(rows,packets,annotations,operations=(),assessments=()):
    """Labeled-subset estimates, with explicit unknown coverage; never actual admission."""
    known=[(p,a) for p,a in rows if a and type(a.get('admission_eligible')) is bool and p['system'].get('recommendation') is not None]
    admitted=[(p,a) for p,a in known if p['system']['recommendation']=='admit']
    invalid=[(p,a) for p,a in known if a['admission_eligible'] is False]
    usable=[(p,a) for p,a in known if a['admission_eligible'] is True]
    no_revision=[(p,a) for p,a in rows if a and a.get('needs_revision') is False and p['system'].get('recommendation') is not None]
    mappings=[(p,a) for p,a in rows if p['system'].get('mapping_completed') is True]
    labeled_mapping=[(p,a) for p,a in mappings if a and type(a.get('mapping_correct')) is bool]
    ops={o['revision_id']:o for o in operations};truth={}
    if len(ops)!=len(operations):raise ValueError('duplicate shadow revision case')
    for a in assessments:
        op=ops.get(a.get('revision_id'))
        if (not op or a['revision_id'] in truth or op.get('mode')!='isolated_shadow' or
            any(a.get(k)!=op.get(k) for k in ('parent_packet_id','child_packet_ids')) or
            op['parent_packet_id'] not in packets or not op['child_packet_ids'] or
            not all(child in packets and annotations.get(child) for child in op['child_packet_ids']) or
            a.get('annotated_by')!='human' or not a.get('reviewer') or not a.get('annotated_at') or
            any(type(a.get(k)) is not bool for k in ('original_issue_resolved','introduced_new_problem'))):
            raise ValueError('shadow revision truth must bind one original case and all independently annotated children')
        truth[a['revision_id']]=a
    def m(identity,title,n,d,coverage,reason=None):
        return {'id':identity,'title':title,'value':n/d if d else None,'numerator':n,'denominator':d,
            'status':'evaluated' if d else 'not_evaluable','coverage':coverage,
            'reason':None if d else reason or '没有对应的有效人工标签或分母样本',
            'scope':'仅有效标签子集，未标注与不可判断样本不计对错'}
    total=len(rows);common={'population':total,'labeled_comparable':len(known),'unknown_or_missing':total-len(known)}
    return {'version':'recommendation_quality_v1','human_resolved':sum(bool(a) for _,a in rows),
        'unlabeled':sum(not a for _,a in rows),'human_admission_unknown':sum(bool(a) and a.get('admission_eligible') is None for _,a in rows),
        'metrics':[
            m('recommendation_precision','准入建议精确率',sum(a['admission_eligible'] for _,a in admitted),len(admitted),common),
            m('false_recommendation_rate','错误建议准入率',sum(p['system']['recommendation']=='admit' for p,_ in invalid),len(invalid),common),
            m('usable_recommendation_rate','可用候选建议通过率',sum(p['system']['recommendation']=='admit' for p,_ in usable),len(usable),common),
            m('unnecessary_revision_recommendation_rate','不必要修订建议率',sum(p['system']['recommendation']=='revise' for p,_ in no_revision),len(no_revision),
                {'population':total,'human_no_revision':len(no_revision),'unknown_or_missing':sum(not a or a.get('needs_revision') is None for _,a in rows)}),
            m('shadow_revision_success_rate','影子修订成功率',sum(a['original_issue_resolved'] and not a['introduced_new_problem'] for a in truth.values()),len(truth),
                {'attempted_cases':len(ops),'labeled_cases':len(truth),'unlabeled_cases':len(ops)-len(truth)},'尚未执行影子修订' if not ops else '缺少当前父版本与全部子版本的人工评价'),
            m('recommendation_mapping_accuracy','建议映射正确率',sum(a['mapping_correct'] for _,a in labeled_mapping),len(labeled_mapping),
                {'population':total,'formed_mappings':len(mappings),'labeled_mappings':len(labeled_mapping),'unknown_or_missing':len(mappings)-len(labeled_mapping)},
                '尚未形成正式映射' if not mappings else '缺少身份、关系语义与约束的人工核验')
        ]}


def repeat_consistency(trials):
    if not trials:return {'status':'not_evaluable','reason':'尚未执行固定输入重复审核'}
    bindings={t['input_fingerprint'] for t in trials}
    if len(bindings)!=1:raise ValueError('repeatability requires identical candidate/evidence/ontology/prompt/model/settings/policy inputs')
    voters=sorted({v['voter_id'] for trial in trials for v in trial['votes']});rows=[]
    for voter in voters:
        votes=[next((v for v in trial['votes'] if v['voter_id']==voter),{}) for trial in trials]
        from review_agent.validator_contracts import validate_vote
        valid=[v for v in votes if v.get('response_status')=='ok' and
            (not v.get('response_contract_version') or not validate_vote(v))]
        checks=('triple_support','entity_reference','relation_semantics','relation_direction','qualifiers','evidence_sufficiency','mapping','knowledge_scope','counterevidence')
        def finding_state(v):
            return sorted((f.get('check',''),f.get('kind',''),f.get('rule_id',''),f.get('meaning_effect',''),
                (f.get('normalization') or {}).get('operation',''),(f.get('normalization') or {}).get('plan_id',''))
                for f in v.get('expression_findings',[]))
        states={sha256_json({'dimensions':{name:v.get('dimensions',{}).get(name,{}).get('status') for name in checks},
            'mapping_relation_id':v.get('dimensions',{}).get('mapping',{}).get('relation_id'),
            'axes':{name:(v.get('review_axes') or {}).get(name,{}).get('status') for name in AXES},
            'findings':finding_state(v)}) for v in valid}
        rows.append({'voter_id':voter,'valid_trials':len(valid),'technical_failures':len(votes)-len(valid),
            'changed':len(states)>1 if len(valid)>=2 else None,
            'changed_checks':[name for name in checks if len({v.get('dimensions',{}).get(name,{}).get('status') for v in valid})>1],
            'changed_axes':[name for name in AXES if len({(v.get('review_axes') or {}).get(name,{}).get('status') for v in valid})>1],
            'findings_changed':len({sha256_json(finding_state(v)) for v in valid})>1 if len(valid)>=2 else None,
            'reported_model_ids':sorted({v.get('returned_model_id') for v in votes if v.get('returned_model_id')}),
            'backend_weight_version_pinned':False})
    return {'status':'observed','comparison_version':'dimensions_axes_findings_v2','trials':len(trials),'input_fingerprint':next(iter(bindings)),'voters':rows,
        'interpretation':'重复一致性仅说明固定请求下的行为，不等于正确率；服务模型别名不能证明后端权重版本固定。'}
