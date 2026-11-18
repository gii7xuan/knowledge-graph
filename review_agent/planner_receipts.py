"""Resolve planner bookkeeping only from exactly bound durable receipts."""
from copy import deepcopy
import json
from .contracts import sha256_json

TERMINAL={'completed','failed','not_sent'}


def durable_planner_rows(connection):
    columns={r[1] for r in connection.execute('PRAGMA table_info(request_attempts)')}
    if not {'attempt_id','state','result_json','metadata_json'}<=columns:return []
    return [{'attempt_id':r[0],'state':r[1],'result':json.loads(r[2]or'{}'),'metadata':json.loads(r[3]or'{}')}
        for r in connection.execute('SELECT attempt_id,state,result_json,metadata_json FROM request_attempts ORDER BY rowid')]


def reconcile_attempts(step,rows):
    original=deepcopy(step.get('attempts')or[])
    proposal=step.get('request_proposal')or{}
    digest=proposal.get('proposal_digest')
    valid=bool(digest and sha256_json({k:v for k,v in proposal.items() if k!='proposal_digest'})==digest)
    existing={a.get('attempt_id') for a in original if a.get('attempt_id')}
    matching=[]
    if valid:
        for row in rows:
            m=row['metadata'];p=m.get('proposal')or{};b=m.get('binding')or{}
            if (m.get('task_type')=='planner_action' and p==proposal and
                b.get('proposal_digest')==digest and b.get('input_hash')==proposal.get('input_hash') and
                b.get('task_id')==proposal.get('candidate_id') and b.get('object_revision')==proposal.get('candidate_revision')):
                matching.append(row)
    result=[]
    for item in original:
        row=next((r for r in rows if r['attempt_id']==item.get('attempt_id')),None)
        if row is not None:
            result.append({**item,**deepcopy(row['result']),'attempt_id':row['attempt_id'],
                'outcome_unknown':row['state'] not in TERMINAL,'response_status':row['result'].get('response_status')or'started'})
        elif item.get('response_status')=='started' and not item.get('attempt_id'):
            unrecorded=[r for r in matching if r['attempt_id']not in existing]
            if not unrecorded:result.append(item)
            for r in unrecorded:
                result.append({**item,**deepcopy(r['result']),'attempt_id':r['attempt_id'],
                    'outcome_unknown':r['state']not in TERMINAL,'response_status':r['result'].get('response_status')or'started',
                    'reconciled_from_exact_durable_proposal':True})
                existing.add(r['attempt_id'])
        else:result.append(item)
    return result
