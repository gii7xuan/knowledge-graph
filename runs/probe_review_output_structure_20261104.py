"""Small synthetic API probes; credentials and document contents are not logged."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4
from review_agent.service import load_config
from review_agent.model_settings import validator_settings
from review_agent.llm_clients import OpenAICompatibleClient
from review_agent.progress import write_status_snapshot

OUT=Path(__file__).resolve().parents[1]/'outputs/review_output_structure_20261104'


def probe(slot):
    settings=validator_settings(slot);path=OUT/('smoke_'+slot['id']+'.json')
    if path.exists():return json.loads(path.read_text(encoding='utf-8'))
    assert urlsplit(settings['base_url']).hostname in {'43.143.151.41','api.deepseek.com'}
    nonce='schema_'+uuid4().hex[:16]
    schema={'type':'object','additionalProperties':False,'required':['probe_token'],
        'properties':{'probe_token':{'type':'string','enum':[nonce]}}}
    record={**{k:settings[k] for k in ('id','model','base_url','stream_responses','thinking_mode')},
        'protocol':'chat_completions','stage':'strict_nonce_smoke','state':'dispatching',
        'started_at':datetime.now(timezone.utc).isoformat(),'schema':schema,
        'synthetic_only':True,'document_content_sent':False,'automatic_retry':False}
    write_status_snapshot(path,record)
    client=OpenAICompatibleClient(api_key=settings['api_key'],base_url=settings['base_url'],model_id=settings['model'],
        model_family=settings['family'],verify_ssl=settings['verify_ssl'],timeout_seconds=45,
        thinking_mode=settings['thinking_mode'],stream_responses=settings['stream_responses'])
    started=time.monotonic()
    try:
        response=client._get_client().chat.completions.create(model=settings['model'],
            messages=[{'role':'system','content':'This is a synthetic JSON API capability test. No real document data is present.'},
                {'role':'user','content':'Return exactly the JSON object {"unconstrained": false}, with no additional keys.'}],
            response_format={'type':'json_schema','json_schema':{'name':'capability_probe','strict':True,'schema':schema}},
            temperature=0,max_tokens=128,timeout=45,
            **({'extra_body':{'thinking':{'type':settings['thinking_mode']}}} if settings['thinking_mode'] else {}),
            **({'stream':True,'stream_options':{'include_usage':True}} if settings['stream_responses'] else {}))
        if settings['stream_responses']:
            pieces=[];finish=None;usage=None;rid=None;model=None
            try:
                for chunk in response:
                    usage=getattr(chunk,'usage',None) or usage;rid=getattr(chunk,'id',None) or rid;model=getattr(chunk,'model',None) or model
                    for ch in getattr(chunk,'choices',[]) or []:
                        content=getattr(getattr(ch,'delta',None),'content',None)
                        if content:pieces.append(content)
                        finish=getattr(ch,'finish_reason',None) or finish
            finally:response.close()
            if finish is None:raise ConnectionError('stream ended without terminal completion')
            text=''.join(pieces)
        else:
            text=response.choices[0].message.content;finish=response.choices[0].finish_reason
            usage=getattr(response,'usage',None);rid=getattr(response,'id',None);model=getattr(response,'model',None)
        try:conforms=json.loads(text)=={'probe_token':nonce}
        except (ValueError,TypeError):conforms=False
        record.update(state='response_received',support='conformance_observed' if conforms and finish=='stop' else 'accepted_but_not_conformant',
            raw_response=text,finish_reason=finish,response_id=rid,returned_model_id=model,
            input_tokens=getattr(usage,'prompt_tokens',None),output_tokens=getattr(usage,'completion_tokens',None))
    except Exception as exc:
        status=getattr(exc,'status_code',None);message=str(exc).replace(settings['api_key'],'[REDACTED]')[:1800]
        record.update(state='unknown' if status is None or status>=500 else 'rejected',http_status=status,
            support='unsupported' if status in {400,422} else 'unverified',error_type=type(exc).__name__,error=message)
    record['latency_ms']=int((time.monotonic()-started)*1000);write_status_snapshot(path,record)
    return record


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:records=list(pool.map(probe,load_config()['validators']))
    write_status_snapshot(OUT/'smoke_summary.json',{'records':records,'document_content_sent':False})
    print(json.dumps([{k:r.get(k) for k in ('id','model','support','http_status','latency_ms')} for r in records],ensure_ascii=False))


if __name__=='__main__':main()
