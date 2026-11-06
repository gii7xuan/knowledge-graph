"""Confirm actual review schema or template mode with synthetic content only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
from pathlib import Path
import time
from uuid import uuid4
from review_agent.service import load_config
from review_agent.model_settings import validator_settings
from review_agent.llm_clients import OpenAICompatibleClient
from review_agent.progress import write_status_snapshot
from review_agent.contracts import sha256_json
from review_agent.schema_validation import schema_errors,fixed_template
from review_agent.validators import _parse_content
from review_agent import response_contract_v6 as contract
from runs.probe_review_output_structure_20261104 import OUT


def call(slot,stage,mode,schema,payload,nonce=None):
    path=OUT/(stage+'_'+slot['id']+'.json')
    if path.exists():return json.loads(path.read_text(encoding='utf-8'))
    s=validator_settings(slot)
    record={**{k:s[k] for k in ('id','model','base_url','stream_responses','thinking_mode')},'protocol':'chat_completions',
        'stage':stage,'state':'dispatching','mode':mode,'schema_hash':sha256_json(schema),'schema':schema,
        'payload':payload,'started_at':datetime.now(timezone.utc).isoformat(),'synthetic_only':True,
        'document_content_sent':False,'automatic_retry':False}
    write_status_snapshot(path,record)
    c=OpenAICompatibleClient(api_key=s['api_key'],base_url=s['base_url'],model_id=s['model'],model_family=s['family'],
        verify_ssl=s['verify_ssl'],timeout_seconds=60,thinking_mode=s['thinking_mode'],stream_responses=s['stream_responses'])
    wire={'type':'json_schema','json_schema':{'name':'review_probe','strict':True,'schema':schema}} if mode=='json_schema' else {'type':'json_object'}
    started=time.monotonic()
    try:
        result=c.complete_json(payload,system_prompt='Synthetic JSON API test. No real documents are present. Follow the supplied output schema.',
            max_tokens=128 if nonce else 2500,response_format=wire,timeout_seconds=60)
        parsed=_parse_content(result.content);errors=schema_errors(parsed,schema)
        if nonce and parsed!={'probe_token':nonce}:errors.append('strict nonce mismatch')
        semantic=[] if nonce else contract.validate_response(parsed)
        record.update(state='response_received',shape_errors=errors,semantic_errors=semantic,
            conforms=not errors and result.finish_reason=='stop',review_contract_passed=not semantic and not errors and result.finish_reason=='stop',
            raw_response=result.content,finish_reason=result.finish_reason,response_id=result.response_id,
            returned_model_id=result.returned_model_id,input_tokens=result.input_tokens,output_tokens=result.output_tokens)
    except Exception as exc:
        code=getattr(exc,'status_code',None)
        record.update(state='unknown' if code is None or code>=500 else 'rejected',http_status=code,
            conforms=False,review_contract_passed=False,error_type=type(exc).__name__,error=str(exc).replace(s['api_key'],'[REDACTED]')[:1800])
    record['latency_ms']=int((time.monotonic()-started)*1000);write_status_snapshot(path,record)
    return record


def confirm(slot):
    smoke=json.loads((OUT/('smoke_'+slot['id']+'.json')).read_text(encoding='utf-8'))
    follow=[];schema=contract.RESPONSE_SCHEMA
    instruction=('Return a synthetic review with NO evidence or source material. Set dimensions.triple_support.status=insufficient; '
        'dimensions.mapping.status=unassigned, relation_id=null, competing_relation_ids=[]; knowledge_scope.status=unresolved. '
        'All other dimension statuses must be unknown, all evidence_ids empty, each note="synthetic probe: no source". '
        'evidence_assessments, primary_evidence_ids and counterevidence_reviewed_ids are empty arrays. '
        'requested_context, suggested_revision and schema_change_hint are null. Return the complete nine-dimension JSON without summary fields.')
    if smoke.get('support')=='conformance_observed':
        nonce='confirm_'+uuid4().hex[:16]
        follow.append(call(slot,'nonce_confirmation','json_schema',{'type':'object','additionalProperties':False,
            'required':['probe_token'],'properties':{'probe_token':{'type':'string','enum':[nonce]}}},
            {'instruction':'Return only {"unconstrained": true}; omit any other keys.'},nonce))
        follow.append(call(slot,'actual_review_schema','json_schema',schema,{'instruction':instruction}))
    else:
        follow.append(call(slot,'fixed_template_fallback','json_template',schema,
            {'instruction':instruction+' Replace all template placeholders; never copy a placeholder as a judgment.',
             'output_contract':schema,'output_template':fixed_template(schema)}))
    strict=smoke.get('support')=='conformance_observed' and len(follow)==2 and all(r.get('conforms') for r in follow)
    if not strict and smoke.get('support')=='conformance_observed':
        follow.append(call(slot,'fixed_template_fallback','json_template',schema,
            {'instruction':instruction,'output_contract':schema,'output_template':fixed_template(schema)}))
    return {'base_url':smoke['base_url'].rstrip('/'),'model':smoke['model'],'protocol':'chat_completions',
        'stream_responses':smoke['stream_responses'],'thinking_mode':smoke['thinking_mode'],
        'schema_hash':sha256_json(schema),'strict_supported':strict,
        'verification':'strict_schema_observed' if strict else 'schema_ignored' if smoke.get('support')=='accepted_but_not_conformant'
            else 'schema_rejected' if smoke.get('support')=='unsupported' else 'unverified',
        'fallback_review_contract_passed':next((r['review_contract_passed'] for r in follow if r['mode']=='json_template'),None),
        'probe_files':['smoke_'+slot['id']+'.json']+[r['stage']+'_'+slot['id']+'.json' for r in follow],
        'verified_at':datetime.now(timezone.utc).isoformat()}


def main():
    with ThreadPoolExecutor(max_workers=3) as pool:entries=list(pool.map(confirm,load_config()['validators']))
    result={'version':'review_output_capabilities_v1','schema_hash':sha256_json(contract.RESPONSE_SCHEMA),
        'synthetic_only':True,'document_content_sent':False,'entries':entries}
    write_status_snapshot(OUT/'capabilities.json',result)
    print(json.dumps(entries,ensure_ascii=False))


if __name__=='__main__':main()
