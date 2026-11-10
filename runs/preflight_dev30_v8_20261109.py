"""One short synthetic request per configured seat, no textbook or ontology."""
from datetime import datetime, timezone, timedelta
import json
import multiprocessing
import time
from urllib.parse import urlsplit
from uuid import uuid4

from runs.rerun_dev30_v8_20261109 import OUT, configuration, public_settings, save
from review_agent.contracts import sha256_json


def worker(sender, slot_id, nonce):
    from review_agent.model_settings import validator_settings
    from review_agent.llm_clients import OpenAICompatibleClient
    from review_agent.execution import api_error_fields
    settings=validator_settings(next(v for v in configuration()['validators'] if v['id']==slot_id))
    parsed=urlsplit(settings['base_url'])
    assert parsed.scheme=='https' and parsed.hostname in {'43.143.151.41','api.deepseek.com'}
    started=time.monotonic()
    try:
        client=OpenAICompatibleClient(api_key=settings['api_key'],base_url=settings['base_url'],
            model_id=settings['model'],model_family=settings['family'],timeout_seconds=25,
            verify_ssl=settings['verify_ssl'],thinking_mode=settings['thinking_mode'],stream_responses=settings['stream_responses'])
        response=client.complete_json({'task_type':'service_availability_probe','probe_nonce':nonce,
            'instruction':'Return exactly {"ok":true,"probe_nonce":"'+nonce+'"}.'},
            system_prompt='Synthetic service availability check; no project data.',max_tokens=512,timeout_seconds=25)
        content=json.loads(response.content) if isinstance(response.content,str) else response.content
        assert content=={'ok':True,'probe_nonce':nonce},'unexpected probe JSON'
        assert response.response_id and str(response.returned_model_id).casefold()==settings['model'].casefold(), 'model identity mismatch'
        result={'voter_id':slot_id,'model_id':settings['model'],'response_status':'ok',
            'response_id':response.response_id,'returned_model_id':response.returned_model_id,
            'input_tokens':response.input_tokens,'output_tokens':response.output_tokens,'outcome_unknown':False}
    except Exception as exc:
        result={'voter_id':slot_id,'model_id':settings['model'],'response_status':'failed',
                'error_type':type(exc).__name__,'error':str(exc)[:800],**api_error_fields(exc)}
    result['latency_ms']=int((time.monotonic()-started)*1000)
    sender.send(result);sender.close()


def main():
    assert (OUT/'experiment.json').exists(),'prepare isolated experiment first'
    config=configuration();settings_hash=sha256_json(public_settings(config))
    path=OUT/'preflight.json'
    if path.exists():
        previous=json.loads(path.read_text(encoding='utf-8'))
        assert previous['settings_hash']==settings_hash,'probe settings changed'
        print(json.dumps({'status':'already_probed','previous_status':previous['status'],'automatic_retry':False}));return
    context=multiprocessing.get_context('spawn');workers=[]
    save('preflight_started.json',{'started_at_beijing':datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'settings_hash':settings_hash,'synthetic_only':True,'max_requests':3,'retries':0})
    try:
        for slot in config['validators']:
            receiver,sender=context.Pipe(duplex=False)
            process=context.Process(target=worker,args=(sender,slot['id'],uuid4().hex))
            process.start();sender.close();workers.append((slot,process,receiver,time.monotonic()+45))
        results=[]
        for slot,process,receiver,deadline in workers:
            if receiver.poll(max(0,deadline-time.monotonic())):
                try:result=receiver.recv()
                except EOFError:result={'voter_id':slot['id'],'response_status':'failed','error_code':'PROBE_WORKER_EXITED','outcome_unknown':True}
            else:
                result={'voter_id':slot['id'],'response_status':'timeout','error_code':'PROBE_HARD_TIMEOUT','outcome_unknown':True}
            results.append(result)
        record={'status':'ready' if all(r['response_status']=='ok' for r in results) else 'blocked',
            'settings_hash':settings_hash,'finished_at_epoch':time.time(),
            'finished_at_beijing':datetime.now(timezone(timedelta(hours=8))).isoformat(),
            'synthetic_only':True,'automatic_retries':0,'items':results}
        save('preflight.json',record);print(json.dumps(record,ensure_ascii=False,indent=2),flush=True)
    finally:
        for _,process,receiver,_ in workers:
            process.join(timeout=.1)
            if process.is_alive():process.terminate();process.join(timeout=2)
            if process.is_alive():process.kill();process.join(timeout=2)
            receiver.close();process.close()


if __name__=='__main__':
    main()
