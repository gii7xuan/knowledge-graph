"""Extend version-bound quality reports with visible completion, cost and human labor."""
import math
from review_agent.contracts import sha256_json


def report(rows,*,work_logs=(),execution_costs=None):
    def metric(identity,n,d,unknown=0):
        return {'id':identity,'numerator':n,'denominator':d,'unknown':unknown,
                'value':n/d if d and not unknown else None,'status':'not_evaluable' if not d or unknown else 'evaluated'}
    closed=[(p,a) for p,a in rows if p['system'].get('review_completed') is True and
            p['system'].get('recommendation') in {'admit','exclude'} and p['system'].get('semantic_status')=='completed']
    admission=[(p,a) for p,a in closed if p['system']['recommendation']=='admit']
    exclusion=[(p,a) for p,a in closed if p['system']['recommendation']=='exclude']
    usable=[(p,a) for p,a in rows if a and a.get('admission_eligible') is True]
    unknown_closure=sum(p['system'].get('semantic_status') is None or type(p['system'].get('review_completed')) is not bool for p,_ in rows)
    known_admission=[a for _,a in admission if a and type(a.get('admission_eligible')) is bool]
    known_exclusion=[a for _,a in exclusion if a and a['four_axes']['scope'] in {'include','exclude'}]
    technical=sum(p['system'].get('technical_incomplete') is True for p,_ in rows)
    unknown_technical=sum(type(p['system'].get('technical_incomplete')) is not bool for p,_ in rows)
    incomplete=sum(p['system'].get('end_to_end_incomplete') is True for p,_ in rows)
    unknown_end_to_end=sum(type(p['system'].get('end_to_end_incomplete')) is not bool for p,_ in rows)
    ids=set();minutes=0
    candidate_ids={p['candidate']['candidate_id'] for p,_ in rows}
    for entry in work_logs:
        if (not entry.get('work_id') or entry['work_id'] in ids or not entry.get('reviewer')
                or not entry.get('recorded_at') or entry.get('kind') not in {'candidate_review','shared_definition','quality_sampling'}
                or type(entry.get('minutes')) not in (int,float) or not math.isfinite(entry['minutes']) or entry['minutes']<0):
            raise ValueError('human work requires unique provenance and actual finite nonnegative minutes')
        scope=set(entry.get('candidate_ids') or [])
        if not scope or (not scope&candidate_ids if entry['kind']=='shared_definition' else not scope<=candidate_ids):
            raise ValueError('human work must identify the affected candidates in this report')
        ids.add(entry['work_id']);minutes+=entry['minutes']
    correct=sum(a['admission_eligible'] for a in known_admission)+sum(a['four_axes']['scope']=='exclude' for a in known_exclusion)
    cost_per_correct=None
    if correct and len(known_admission)+len(known_exclusion)==len(closed) and execution_costs:
        cost_per_correct={'correct_closures':correct,'requests':execution_costs['physical_model_calls']/correct,
            'tokens':sum(execution_costs['reported_tokens'].values())/correct if not execution_costs.get('usage_missing_requests') else None,
            'seconds':execution_costs.get('elapsed_seconds')/correct if execution_costs.get('elapsed_seconds') is not None else None,
            'scope':'all cohort execution cost, including unfinished cases; divided by human-confirmed correct automatic closures'}
    return {'version':'agent_resolution_metrics_v1','metrics':[
        metric('automatic_closure_rate',len(closed),len(rows),unknown_closure),
        metric('admissible_candidate_identification_recall',sum(p['system'].get('recommendation')=='admit' for p,_ in usable),len(usable),
               sum(not a or type(a.get('admission_eligible')) is not bool for _,a in rows)),
        metric('domain_adjudication_queue_rate',sum(p['system'].get('required_human_action')=='domain_adjudication' for p,_ in rows),len(rows),
               sum(p['system'].get('required_human_action') is None for p,_ in rows)),
        metric('automatic_admission_recommendation_precision',sum(a['admission_eligible'] for a in known_admission),len(admission),len(admission)-len(known_admission)),
        metric('automatic_exclusion_precision',sum(a['four_axes']['scope']=='exclude' for a in known_exclusion),len(exclusion),len(exclusion)-len(known_exclusion)),
        metric('technical_incomplete_rate',technical,len(rows),unknown_technical),
        metric('end_to_end_incomplete_rate',incomplete,len(rows),unknown_end_to_end)],
        'human_work':{'actual_minutes':minutes if work_logs else None,'minutes_per_100_candidates':minutes*100/len(rows) if work_logs and rows else None,
            'shared_definition_work_included':True,'provenance_records':len(ids),'missing_logs_are_not_zero_work':True},
        'execution_costs':execution_costs,'cost_per_correct_automatic_closure':cost_per_correct,
        'cost_metric_reason':'requires matched human truth for closures and complete physical usage',
        'quality_improvement_established':False}


def sampling_plan(packets,*,fraction=.2,seed='quality_sampling_v1'):
    if not 0<fraction<=1:raise ValueError('sampling fraction must be positive and at most one')
    strata={}
    for packet in packets:
        key=(packet['system'].get('recommendation'),packet['candidate'].get('triple',{}).get('relation_id'),
             packet['system'].get('error_category'))
        strata.setdefault(key,[]).append(packet)
    rows=[]
    for key,population in sorted(strata.items(),key=lambda pair:str(pair[0])):
        n=max(1,math.ceil(len(population)*fraction))
        selected=sorted(population,key=lambda p:sha256_json([seed,p['packet_id']]))[:n]
        rows.extend({'packet_id':p['packet_id'],'stratum':key,'stratum_population':len(population),
            'stratum_sample_size':n,'inclusion_probability':n/len(population),'weight':len(population)/n} for p in selected)
    return {'version':'stratified_quality_sample_v1','seed':seed,'fraction':fraction,'population':len(packets),
            'samples':rows,'includes_automatic_exclusion_and_revision':True,'must_apply_weights_when_aggregating':True}


def weighted_audit_estimate(plan,judgments):
    samples=plan['samples'];allowed={s['packet_id'] for s in samples}
    if set(judgments)-allowed or any(type(v) is not bool for v in judgments.values()):
        raise ValueError('weighted audit judgments must be booleans for the exact frozen sample IDs')
    missing=allowed-set(judgments)
    denominator=sum(s['weight'] for s in samples)
    return {'value':sum(s['weight']*judgments[s['packet_id']] for s in samples)/denominator if denominator and not missing else None,
        'weighted_population':denominator,'unlabeled_sample_count':len(missing),
        'method':'stratum_population/sample_size weighting','human_judgments_required':True}
