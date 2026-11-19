"""Thread-safe physical request journal, independent of shared-case charges."""
import json
import threading
import time
from copy import deepcopy
from uuid import uuid4
from review_agent.contracts import sha256_json


class StudyBudget:
    """Global budget limits; request uncertainty is tracked for human handoff."""
    def __init__(self, limits):
        self.limits=dict(limits);self.calls=0;self.known_tokens=0;self.blocked_reason=None
        self.started=time.monotonic();self.lock=threading.Lock()
        self.unknown_requests={}

    def begin(self):
        with self.lock:
            reason=self.blocked_reason
            if self.calls>=self.limits['llm_calls']:reason=reason or 'study_call_budget_exhausted'
            if self.known_tokens>=self.limits['known_tokens']:reason=reason or 'study_token_stop_reached'
            if time.monotonic()-self.started>=self.limits['seconds']:reason=reason or 'study_timeout'
            if reason:
                self.blocked_reason=reason
                raise RuntimeError(reason)
            self.calls+=1

    def finish(self, record):
        with self.lock:
            self.known_tokens+=sum(record.get(k) or 0 for k in ('input_tokens','output_tokens'))
            if record['status']=='outcome_unknown':self.unknown_requests[record['request_id']]=tuple(record.get('case_ids',[]))
            else:self.unknown_requests.pop(record['request_id'],None)

    def block_unknown(self,record):
        # Keep the request quarantined by its durable identity; do not stop
        # other candidates or methods because one request has no final result.
        with self.lock:self.unknown_requests[record['request_id']]=tuple(record.get('case_ids',[]))

    def snapshot(self):
        with self.lock:
            return {'limits':dict(self.limits),'physical_calls':self.calls,'known_tokens':self.known_tokens,
                    'outcome_unknown_requests':len(self.unknown_requests),
                    'human_review_case_ids':sorted({cid for ids in self.unknown_requests.values() for cid in ids}),
                    'blocked_reason':self.blocked_reason,'token_limit_kind':'known-usage dispatch stop; in-flight usage may exceed it'}


class RequestMeter:
    def __init__(self,path,limits,study=None):
        self.path=path; self.limits=limits; self.records={}; self.lock=threading.Lock(); self.usage={}
        self.study=study

    def write(self,record):
        with self.path.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(record,ensure_ascii=False)+'\n'); stream.flush()

    def wrap(self,client,role,case_ids):
        return MeteredClient(self,client,role,tuple(sorted(set(case_ids)))) if client is not None else None

    def begin(self,client,payload,role,case_ids,settings):
        with self.lock:
            now=time.monotonic()
            accounts=[self.usage.setdefault(cid,{'calls':0,'tokens':0,'started':now}) for cid in case_ids]
            if any(a['calls']>=self.limits['llm_calls'] or a['tokens']>=self.limits['tokens']
                   or now-a['started']>=self.limits['seconds'] for a in accounts):
                raise RuntimeError('shared experiment case budget exhausted')
            if self.study:self.study.begin()
            for account in accounts: account['calls']+=1
            record={'request_id':uuid4().hex,'role':role,'case_ids':list(case_ids),'model_id':client.model_id,
                'model_family':client.model_family,'payload_hash':sha256_json(payload),
                'provider_endpoint_hash':sha256_json(str(getattr(client,'base_url',''))),
                'context_limit':None,'capability_verification':'not_run',
                'retry':bool(payload.get('correction_feedback')),'status':'started',
                'generation_settings':settings,
                'input_tokens':None,'output_tokens':None,'latency_ms':None}
            self.records[record['request_id']]=record;self.write(record)
            return record['request_id']

    def finish(self,request_id,response,error,latency):
        with self.lock:
            record=self.records[request_id]
            # Reconcile the original request and charge its late usage. Returned
            # snapshots are independent copies and keep their original unknown.
            if record['status'] not in {'started','outcome_unknown'} or record.get('response_reconciled'): return
            late=record['status']=='outcome_unknown'
            if error is None:
                fields={}
            else:
                from review_agent.execution import api_error_fields
                fields=api_error_fields(error)
            record.update(status='completed' if error is None else 'outcome_unknown' if fields.get('outcome_unknown') else 'failed',
                latency_ms=latency,late_response=late,response_reconciled=True,**fields)
            if response is not None:
                record.update(input_tokens=getattr(response,'input_tokens',None),output_tokens=getattr(response,'output_tokens',None),
                    returned_model_id=getattr(response,'returned_model_id',None),response_id=getattr(response,'response_id',None))
            if error is not None: record['error_type']=type(error).__name__
            for cid in record['case_ids']:
                self.usage[cid]['tokens']+=sum(record.get(k) or 0 for k in ('input_tokens','output_tokens'))
            if self.study:self.study.finish(record)
            self.write(record)

    def snapshot(self):
        with self.lock:
            for record in self.records.values():
                if record['status']=='started':
                    record['status']='outcome_unknown';self.write(record)
                    if self.study:self.study.block_unknown(record)
            return deepcopy(list(self.records.values()))


class MeteredClient:
    def __init__(self,meter,client,role,case_ids):
        self.meter,self.client,self.role,self.case_ids=meter,client,role,case_ids

    def __getattr__(self,key): return getattr(self.client,key)

    def complete_json(self,payload,**kwargs):
        settings={k:kwargs.get(k) for k in ('temperature','max_tokens')}
        settings['temperature']=kwargs.get('temperature',0.0)
        request_id=self.meter.begin(self.client,payload,self.role,self.case_ids,settings)
        began=time.monotonic(); response=None;error=None
        try:
            response=self.client.complete_json(payload,**kwargs)
            return response
        except BaseException as exc:
            error=exc;raise
        finally: self.meter.finish(request_id,response,error,int((time.monotonic()-began)*1000))
