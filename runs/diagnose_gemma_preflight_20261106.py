"""One bounded synthetic Gemma output check after the short probe parse error."""
from datetime import datetime, timezone, timedelta
import json
import multiprocessing
import time
from uuid import uuid4
from urllib.parse import urlsplit
from runs.rerun_dev30_v63_20261106 import OUT, configuration, save


def worker(sender):
    from review_agent.model_settings import validator_settings
    from review_agent.llm_clients import OpenAICompatibleClient
    from review_agent.validators import _parse_content
    from review_agent.execution import api_error_fields
    s=validator_settings(next(v for v in configuration()['validators'] if v['id']=='judge_b'))
    u=urlsplit(s['base_url']);assert u.scheme=='https' and u.hostname=='43.143.151.41'
    response=None;nonce=uuid4().hex;started=time.monotonic()
    try:
        client=OpenAICompatibleClient(api_key=s['api_key'],base_url=s['base_url'],model_id=s['model'],
            model_family=s['family'],verify_ssl=s['verify_ssl'],thinking_mode=s['thinking_mode'],
            stream_responses=s['stream_responses'],timeout_seconds=30)
        response=client.complete_json({'task_type':'service_availability_probe','probe_nonce':nonce,
            'instruction':'Return exactly {"ok":true,"probe_nonce":"'+nonce+'"}. No explanation.'},
            system_prompt='Synthetic service check. Return the requested JSON only.',max_tokens=512,timeout_seconds=30)
        record={'voter_id':'judge_b','model_id':s['model'],'response_id':response.response_id,
            'returned_model_id':response.returned_model_id,'finish_reason':response.finish_reason,
            'input_tokens':response.input_tokens,'output_tokens':response.output_tokens,
            'raw_response':response.content,'outcome_unknown':False,'response_received':True}
        try:
            value=_parse_content(response.content)
            record['response_status']='ok' if value=={'ok':True,'probe_nonce':nonce} and response.response_id and str(response.returned_model_id).casefold()==s['model'].casefold() else 'output_invalid'
        except (ValueError,TypeError) as exc:
            record.update(response_status='output_invalid',error=str(exc))
    except Exception as exc:
        record={'voter_id':'judge_b','model_id':s['model'],'response_status':'failed','error':str(exc)[:800],
                'response_received':response is not None,**api_error_fields(exc)}
    record['latency_ms']=int((time.monotonic()-started)*1000)
    sender.send(record);sender.close()


def main():
    target=OUT/'gemma_preflight_detail.json'
    assert not target.exists(),'this supplemental probe already ran; never retry automatically'
    ctx=multiprocessing.get_context('spawn');receiver,sender=ctx.Pipe(duplex=False)
    process=ctx.Process(target=worker,args=(sender,));process.start();sender.close()
    try:
        result=receiver.recv() if receiver.poll(45) else {'voter_id':'judge_b','response_status':'timeout','outcome_unknown':True,'error_code':'PROBE_HARD_TIMEOUT'}
        result.update(synthetic_only=True,max_output_tokens=512,automatic_retries=0,
            purpose='diagnose prior short probe JSON parse error; not a review vote',
            checked_at_beijing=datetime.now(timezone(timedelta(hours=8))).isoformat())
        save(target.name,result)
        # Keep the initial evidence and replace only the explicitly rechecked seat.
        if result['response_status']=='ok':
            previous=json.loads((OUT/'preflight.json').read_text(encoding='utf-8'))
            save('preflight_initial.json',previous)
            previous['items']=[result if r['voter_id']=='judge_b' else r for r in previous['items']]
            previous.update(status='ready' if all(r['response_status']=='ok' for r in previous['items']) else 'blocked',
                supplemental_probe='gemma_preflight_detail.json',supplemental_requests=1,finished_at_epoch=time.time())
            save('preflight.json',previous)
        print(json.dumps({k:v for k,v in result.items() if k!='raw_response'},ensure_ascii=False,indent=2))
    finally:
        process.join(timeout=.1)
        if process.is_alive():process.terminate();process.join(timeout=2)
        if process.is_alive():process.kill();process.join(timeout=2)
        receiver.close();process.close()


if __name__=='__main__':main()
