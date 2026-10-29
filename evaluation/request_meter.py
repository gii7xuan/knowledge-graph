"""Thread-safe physical request journal, independent of shared-case charges."""
import json
import threading
import time
from uuid import uuid4
from review_agent.contracts import sha256_json


class RequestMeter:
    def __init__(self,path,limits):
        self.path=path; self.limits=limits; self.records={}; self.lock=threading.Lock(); self.usage={}

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
            # Snapshot marks unresolved requests unknown. A late reply never
            # changes an already frozen experiment result.
            if record['status']!='started': return
            record.update(status='completed' if error is None else 'failed',latency_ms=latency)
            if response is not None:
                record.update(input_tokens=getattr(response,'input_tokens',None),output_tokens=getattr(response,'output_tokens',None),
                    returned_model_id=getattr(response,'returned_model_id',None),response_id=getattr(response,'response_id',None))
            if error is not None: record['error_type']=type(error).__name__
            for cid in record['case_ids']:
                self.usage[cid]['tokens']+=sum(record.get(k) or 0 for k in ('input_tokens','output_tokens'))
            self.write(record)

    def snapshot(self):
        with self.lock:
            for record in self.records.values():
                if record['status']=='started': record['status']='outcome_unknown';self.write(record)
            return [dict(r) for r in self.records.values()]


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
