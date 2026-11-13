"""Two read-only GET checks. Never submits chat/completions or changes settings."""
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import httpx
from review_agent.cli import _configured_validators
from review_agent.service import load_config

HERE = Path(__file__).resolve().parent
SAFE_HEADERS = ('server', 'date', 'content-type', 'content-length', 'transfer-encoding',
                'connection', 'via', 'x-request-id', 'x-oneapi-request-id', 'traceparent')


def main():
    voter = next(v for v in _configured_validators(load_config()) if v['id'] == 'judge_b')
    configured = voter['client']
    parts = urlsplit(configured.base_url)
    assert parts.scheme == 'https' and parts.netloc == '43.143.151.41:3000'
    origin = parts.scheme + '://' + parts.netloc
    records = []
    for path, authenticated in (('/api/status', False), ('/v1/models', True)):
        started = time.monotonic()
        events = []
        def trace(event_name, event_info):
            # Do not retain event_info: it can include credential-bearing headers.
            events.append({'event': event_name, 'elapsed_ms': round((time.monotonic() - started) * 1000, 3)})
        record = {'method': 'GET', 'path': path, 'authenticated_with_existing_model_key': authenticated,
                  'model_inference_calls': 0, 'redirects_followed': False, 'trace': events}
        try:
            with httpx.Client(verify=configured.verify_ssl, trust_env=False, timeout=20,
                              follow_redirects=False) as client:
                response = client.get(origin + path,
                    headers={'Authorization': 'Bearer ' + configured.api_key} if authenticated else {},
                    extensions={'trace': trace})
                record.update(status=response.status_code, elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                              headers={name: response.headers[name] for name in SAFE_HEADERS if name in response.headers})
                ssl_object = response.extensions.get('network_stream').get_extra_info('ssl_object') if response.extensions.get('network_stream') else None
                if ssl_object:
                    record['tls'] = {'version': ssl_object.version(), 'cipher': ssl_object.cipher()[0]}
                try:
                    value = response.json()
                    if path == '/api/status':
                        data = value.get('data') or {}
                        record['status_identity'] = {name: data.get(name) for name in ('system_name', 'version', 'start_time')}
                        record['success'] = value.get('success')
                    elif isinstance(value.get('data'), list):
                        ids = [str(item.get('id', '')) for item in value['data'] if isinstance(item, dict)]
                        record.update(advertised_model_count=len(ids), gemma_advertised=any('gemma' in model.lower() for model in ids),
                                      qwen_advertised=any('qwen' in model.lower() for model in ids),
                                      listing_is_not_inference_health_check=True)
                    else:
                        record['json_top_level_keys'] = list(value) if isinstance(value, dict) else []
                except (ValueError, AttributeError):
                    record['body_kind'] = 'not the expected JSON metadata'
        except Exception as error:
            record.update(error_type=type(error).__name__,
                          error=str(error).replace(configured.api_key, '[REDACTED]')[:300],
                          elapsed_ms=round((time.monotonic() - started) * 1000, 3))
        records.append(record)
    (HERE / 'read_only_connectivity.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps([{key: record.get(key) for key in ('path', 'status', 'elapsed_ms', 'success', 'gemma_advertised', 'error_type')}
                      for record in records], ensure_ascii=False))


if __name__ == '__main__':
    main()
