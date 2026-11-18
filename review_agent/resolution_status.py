"""Technical recovery and domain adjudication are separate, visible obligations."""
import json


def technical_tasks(repository, audit_id):
    with repository.connect() as connection:
        rows=connection.execute("SELECT attempt_id,request_key,state,metadata_json,result_json FROM request_attempts "
            "WHERE json_extract(metadata_json,'$.proposal.audit_id')=?",(audit_id,)).fetchall()
    result=[]
    for row in rows:
        response=json.loads(row['result_json'] or '{}');meta=json.loads(row['metadata_json'])
        if row['state']=='not_sent' or response.get('actual_calls')==0:continue
        execution_unknown=row['state'] in {'running','unknown'}
        usage_unknown=any(type(response.get(k)) is not int for k in ('input_tokens','output_tokens'))
        if execution_unknown or usage_unknown:
            result.append({'task_type':meta.get('task_type'),'attempt_id':row['attempt_id'],
                'request_key':row['request_key'],'request_state':row['state'],
                'execution_unknown':execution_unknown,'usage_unknown':usage_unknown,
                'semantic_dependency':meta.get('task_type')!='planner_action' and execution_unknown,
                'action_executed':False if meta.get('task_type')=='planner_action' and execution_unknown else None,
                'automatic_resend':False,'required_action':'technical_recovery' if execution_unknown else 'usage_reconciliation'})
    return result


def classify(outcome, tasks):
    tasks=list(tasks)
    recommendation=outcome.get('recommendation')
    complete=bool(outcome.get('review_completed'))
    required=max((d.get('required_seats',0) for d in outcome.get('dimension_results',{}).values()),default=0)
    weighted_complete=bool(outcome.get('weight_snapshot') and all(d.get('judgment_complete') for d in outcome.get('dimension_results',{}).values()))
    if required and not weighted_complete and len(outcome.get('whole_vote_eligible_voter_ids',[]))<required and not any(t.get('semantic_dependency') for t in tasks):
        tasks.append({'task_type':'committee_material_incomplete','semantic_dependency':True,
            'execution_unknown':False,'usage_unknown':False,'automatic_resend':False,
            'required_action':'bounded_contract_or_execution_recovery'})
    if complete and recommendation in {'admit','exclude'}:
        semantic='completed';human='quality_sampling'
    elif recommendation=='wait_mapping':
        semantic='waiting_mapping';human='shared_definition_or_mapping_review'
    elif any(t.get('semantic_dependency') for t in tasks):
        semantic='review_incomplete';human='technical_recovery'
    else:
        semantic='unresolved';human='domain_adjudication'
    return {'semantic_status':semantic,'technical_status':'pending' if tasks else 'settled',
            'required_human_action':human,'technical_recovery_tasks':tasks,
            'technical_incomplete':bool(tasks),'execution_outcome_unknown':any(t.get('execution_unknown') for t in tasks),
            'end_to_end_incomplete':semantic!='completed' or bool(tasks)}
