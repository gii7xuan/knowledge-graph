"""One synthetic, no-retry capability check; never dispatch real review material."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from review_agent.service import load_config
from review_agent.model_settings import validator_settings
from review_agent.llm_clients import OpenAICompatibleClient
from review_agent.response_contract_v7 import RESPONSE_SCHEMA
from review_agent.schema_validation import schema_errors
from review_agent.contracts import sha256_json
from review_agent.execution import api_error_fields

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/review_four_axes_20261106'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / 'qwen_v7_schema_probe.json'
    if path.exists():
        print('Existing probe preserved; no automatic resend.'); return
    slot = next(v for v in load_config()['validators'] if v['id'] == 'judge_a')
    settings = validator_settings(slot)
    assert settings['model'] == 'Qwen' and settings['base_url'] == 'https://43.143.151.41:3000/v1'
    record = {'synthetic_only': True, 'project_content_sent': False, 'review_votes_written': False,
        'automatic_retries': 0, 'model': settings['model'], 'base_url': settings['base_url'],
        'stream_responses': settings['stream_responses'], 'thinking_mode': settings['thinking_mode'],
        'schema_hash': sha256_json(RESPONSE_SCHEMA), 'state': 'dispatching'}
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    started = time.monotonic()
    try:
        client = OpenAICompatibleClient(api_key=settings['api_key'], base_url=settings['base_url'],
            model_id=settings['model'], model_family=settings['family'], timeout_seconds=90,
            verify_ssl=settings['verify_ssl'], stream_responses=settings['stream_responses'], thinking_mode=settings['thinking_mode'])
        response = client.complete_json({'synthetic_probe_nonce': uuid4().hex,
            'instruction': 'Return exactly {"synthetic_probe":true}; do not add any other fields.'},
            system_prompt='Synthetic schema enforcement test. No document, ontology or review prompt is provided.',
            response_format={'type': 'json_schema', 'json_schema': {'name':'triple_review_v7', 'strict':True, 'schema':RESPONSE_SCHEMA}},
            max_tokens=4096, timeout_seconds=90)
        raw = json.loads(response.content)
        errors = schema_errors(raw, RESPONSE_SCHEMA)
        record.update(state='completed', strict_supported=not errors, schema_errors=errors,
            finish_reason=response.finish_reason, response_id=response.response_id, returned_model_id=response.returned_model_id,
            raw_response=raw, input_tokens=response.input_tokens, output_tokens=response.output_tokens)
    except Exception as error:
        record.update(state='failed', strict_supported=False, error_type=type(error).__name__,
            error=str(error).replace(settings['api_key'], '[REDACTED]')[:1200], **api_error_fields(error))
    record.update(latency_ms=int((time.monotonic()-started)*1000), verified_at=datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    if record.get('strict_supported') and record.get('finish_reason') == 'stop':
        registry_path = ROOT/'config/review_output_capabilities.json'
        registry = json.loads(registry_path.read_text(encoding='utf-8'))
        registry['entries'].append({k:record[k] for k in ('base_url','model','stream_responses','thinking_mode','schema_hash','verified_at')})
        registry['entries'][-1].update(protocol='chat_completions', strict_supported=True,
            verification='strict_schema_observed', fallback_review_contract_passed=None,
            probe_files=[path.name], source_report_directory='outputs/review_four_axes_20261106')
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in record.items() if k not in ('raw_response',)}, ensure_ascii=False))


if __name__ == '__main__': main()
