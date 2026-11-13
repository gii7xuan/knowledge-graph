"""Read-only analysis of existing requests; no provider calls or vote updates."""
from datetime import datetime, timezone, timedelta
import importlib.metadata
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from evaluation.review_samples import ReadOnlyRepository
from ontology_review import semantic_competency as cq
from ontology_review.review_payload import validator_payload
from ontology_review.validators import prompt_for
from ontology_review.contracts import RESPONSE_SCHEMA

HERE = Path(__file__).resolve().parent
DATABASE = ROOT / 'outputs/formal_definition_rerun_20261112_prepared/review.sqlite3'
TASK = 'ext_bf690b903dfa4b3faf2510c98ce4310d'


def write(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def local_time(epoch):
    return datetime.fromtimestamp(epoch, timezone(timedelta(hours=8))).isoformat() if epoch else None


def main():
    repository = ReadOnlyRepository(DATABASE)
    with repository.connect() as connection:
        rows = connection.execute('SELECT * FROM request_attempts ORDER BY started_at').fetchall()
    records = []
    for row in rows:
        metadata = json.loads(row['metadata_json'])
        proposal = metadata.get('proposal') or {}
        if proposal.get('task_id') != TASK:
            continue
        result = json.loads(row['result_json'] or '{}')
        generation = metadata.get('generation') or {}
        feedback = metadata.get('feedback')
        if metadata['task_type'] == 'ontology_review':
            payload = {**validator_payload(proposal, delivery_version=generation.get('ontology_delivery_version')),
                       'output_contract': RESPONSE_SCHEMA, 'allowed_evidence_ids': [e['evidence_id'] for e in proposal['evidence']]}
            prompt = prompt_for(proposal)
        else:
            payload = {**proposal, 'output_contract': cq.SCHEMA}
            prompt = cq.PROMPT
        if feedback:
            payload['correction_feedback'] = feedback
        content = json.dumps(payload, ensure_ascii=False)
        provider_started = result.get('provider_started_at')
        received = row['response_received_at']
        records.append({
            'attempt_id': row['attempt_id'], 'request_key': row['request_key'],
            'model_id': metadata['binding']['model_id'], 'task_type': metadata['task_type'],
            'started_at': local_time(row['started_at']), 'state': row['state'],
            'response_status': result.get('response_status'), 'http_status': result.get('http_status'),
            'error_code': result.get('error_code'),
            'elapsed_to_receipt_seconds': round(received - row['started_at'], 3) if received else None,
            'provider_to_receipt_seconds': round(received - provider_started, 3) if received and provider_started else None,
            'local_wait_limit_seconds': round(row['deadline_at'] - row['started_at'], 3),
            'worker_queue_ms': result.get('queue_time_ms'), 'sdk_latency_ms': result.get('latency_ms'),
            'stream_requested': bool(generation.get('stream_responses')),
            'max_output_tokens': generation.get('max_output_tokens'),
            'system_prompt_chars': len(prompt + '\nReturn only a valid JSON object.'),
            'user_json_chars': len(content), 'user_json_utf8_bytes': len(content.encode('utf-8')),
            'source_document_chars': sum(len(d.get('text', '')) for d in proposal.get('source_documents', [])),
            'has_correction_feedback': bool(feedback), 'input_tokens': result.get('input_tokens'),
            'output_tokens': result.get('output_tokens'), 'finish_reason': result.get('finish_reason'),
            'response_id': result.get('response_id'), 'provider_request_id': row['provider_request_id'],
            'transport_metrics_saved': result.get('transport_metrics'),
            'nginx_version_in_error_body': 'nginx/1.24.0 (Ubuntu)' if 'nginx/1.24.0 (Ubuntu)' in str(result.get('error', '')) else None,
            'response_headers_saved': 'response_headers' in result,
            'unknown_not_classified_unexecuted': result.get('outcome_unknown', False),
        })
    assert len(records) == 11
    gemma = [record for record in records if record['model_id'] == 'Gemma']
    failures = [record for record in gemma if record['http_status'] == 504]
    assert len(gemma) == 5 and len(failures) == 4
    config = json.loads((ROOT / 'config/review_agent.json').read_text(encoding='utf-8'))
    slot = next(item for item in config['validators'] if item['id'] == 'judge_b')
    summary = {'model_calls_made_for_this_diagnosis': 0, 'historical_model_calls': len(records),
        'gemma_historical_attempts': len(gemma), 'gemma_http_504_count': len(failures),
        'gemma_returned_model_body_count': sum(bool(r['response_id']) for r in gemma),
        '504_elapsed_seconds': [r['elapsed_to_receipt_seconds'] for r in failures],
        '504_provider_elapsed_seconds': [r['provider_to_receipt_seconds'] for r in failures],
        '504_local_wait_limits_seconds': [r['local_wait_limit_seconds'] for r in failures],
        'configured_http_timeout_seconds': slot['http_timeout_seconds'],
        'configured_request_wait_seconds': slot['request_timeout_seconds'],
        'configured_stream_responses': slot['stream_responses'],
        'all_504s_arrived_before_local_wait_deadline': all(r['elapsed_to_receipt_seconds'] < r['local_wait_limit_seconds'] for r in failures),
        'all_504s_contain_nginx_error_body': all(bool(r['nginx_version_in_error_body']) for r in failures),
        'historical_gemma_stream_timing_observable': any(r['transport_metrics_saved'] for r in gemma),
        'runtime_versions': {package: importlib.metadata.version(package) for package in ('openai', 'httpx')},
        'inferences': [
            'The four failures are actual HTTP 504 responses, not expiration of the 170-second client HTTP timeout or 180-second caller wait.',
            'A gateway/upstream delay is implicated. Exact Nginx timeout configuration and which hop generated the error are unproven without server logs or future response headers.',
            'Requested streaming does not prove any SSE bytes reached this client; current ontology wrappers do not preserve the client stream timing metrics.',
        ],
        'next_probe_requires_additional_model_call_authorization': True}
    write('historical_request_timing.json', records)
    write('offline_diagnosis.json', summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
