"""One negative-control capability probe; synthetic input, never a review vote."""
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
from review_agent.cli import _configured_validators
from review_agent.contracts import sha256_json
from review_agent.llm_clients import response_metadata
from review_agent.output_structure import endpoint_binding
from review_agent.response_contract_v8 import RESPONSE_SCHEMA
from review_agent.schema_validation import schema_errors
from runs.rerun_dev30_v8_20261109 import configuration


def main():
    root=Path(__file__).resolve().parents[1];out=root/'outputs/missing_votes_qwen_schema_probe_20261110'
    out.mkdir(exist_ok=False)
    voter=next(v for v in _configured_validators(configuration()) if v['id']=='judge_a')
    binding=endpoint_binding(voter)
    assert binding['base_url']=='https://43.143.151.41:3000/v1'
    instruction='This is a synthetic transport capability test. Return exactly {"unexpected_probe_field":true}. Do not return dimensions or any other fields.'
    result={'started_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),'binding':binding,
        'schema_hash':sha256_json(RESPONSE_SCHEMA),'synthetic_input_only':True,'counts_as_review_vote':False,'model_calls':1}
    try:
        response=voter['client'].complete_json({'probe_instruction':instruction},system_prompt=instruction,
            max_tokens=4096,timeout_seconds=90,response_format={'type':'json_schema','json_schema':{
                'name':'triple_review_v8','strict':True,'schema':RESPONSE_SCHEMA}})
        result.update(response_metadata(response));result['raw_response']=response.content
        try:parsed=json.loads(response.content)
        except (TypeError,ValueError):parsed=None
        errors=schema_errors(parsed,RESPONSE_SCHEMA)
        result.update(schema_errors=errors,strict_supported=not errors and response.finish_reason=='stop',
            verification='strict_schema_observed' if not errors and response.finish_reason=='stop' else 'schema_not_enforced')
    except Exception as exc:
        result.update(error_type=type(exc).__name__,http_status=getattr(exc,'status_code',None),
            error=str(exc),strict_supported=False,verification='strict_schema_probe_failed')
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'raw_response','schema_errors'}},ensure_ascii=False))


if __name__=='__main__':main()
