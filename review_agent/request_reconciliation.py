"""Evidence-based, zero-call classification of unsettled requests. No status invention."""
from datetime import datetime,timezone,timedelta
import json


def report(repository,*,candidate_id=None):
    with repository.connect() as connection:
        rows=connection.execute("SELECT * FROM request_attempts WHERE state IN ('running','unknown') ORDER BY started_at").fetchall()
        result=[]
        for row in rows:
            meta=json.loads(row['metadata_json']);proposal=meta.get('proposal')or{};binding=meta.get('binding')or{}
            identity=(proposal.get('candidate')or proposal.get('before')or{}).get('candidate_id')or proposal.get('candidate_id')
            if candidate_id and identity!=candidate_id:continue
            receipt=json.loads(row['result_json']or'{}');current=repository.get_candidate(identity) if identity else None
            planner=meta.get('task_type')=='planner_action'
            action_applied=False if planner and receipt.get('response_status')!='ok' and not receipt.get('action') else None
            if planner:
                records=connection.execute('SELECT record_json FROM planner_steps WHERE audit_id=?',(proposal.get('audit_id'),)).fetchall()
                for record in records:
                    data=json.loads(record[0]);chosen=data.get('selected_attempt_id')or data.get('attempt_id')
                    if chosen==row['attempt_id'] and data.get('action_executed'):action_applied=True
            request_id=row['provider_request_id']or receipt.get('gateway_request_id')or receipt.get('response_id')
            result.append({'attempt_id':row['attempt_id'],'request_key':row['request_key'],'task_type':meta.get('task_type'),
                'candidate_id':identity,'candidate_revision':binding.get('object_revision'),
                'current_revision':current.get('revision') if current else None,
                'started_at_beijing':datetime.fromtimestamp(row['started_at'],timezone(timedelta(hours=8))).isoformat(),
                'state':row['state'],'http_status':receipt.get('http_status'),'error_code':receipt.get('error_code'),
                'provider_request_id':request_id,'client_request_id':(receipt.get('transport_metrics')or{}).get('client_request_id'),
                'usage_known':row['usage_state']=='known','semantic_dependency':not planner,
                'planner_action_applied':action_applied if planner else None,
                'receipt_available':bool(receipt.get('response_status')=='ok'),
                'recovery_status':'provider_evidence_required','automatic_resend':False,
                'next_step':'query_provider_by_request_id' if request_id else 'provider_logs_required_by_time_model_and_input_hash',
                'input_hash':proposal.get('input_hash')or proposal.get('proposal_digest'),
                'model_id':binding.get('model_id'),'late_action_execution_allowed':False})
    return {'version':'request_reconciliation_v1','model_calls':0,'requests':result,
        'execution_unknown_count':len(result),'usage_unknown_count':sum(not r['usage_known'] for r in result),
        'original_request_states_changed':False,'unknown_does_not_mean_not_executed':True}
