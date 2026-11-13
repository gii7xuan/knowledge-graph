"""Authorized synthetic transport probes; never reruns project review requests."""
from datetime import datetime, timezone, timedelta
import codecs
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUT = HERE / 'client_probes_20261112'
sys.path.insert(0, str(ROOT))
import httpx

SAFE_HEADERS = ('server', 'date', 'content-type', 'content-length', 'content-encoding',
                'transfer-encoding', 'connection', 'via', 'x-request-id',
                'x-oneapi-request-id', 'traceparent', 'x-accel-buffering')


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    temporary = path.with_suffix('.writing')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


class SSECollector:
    def __init__(self, record):
        self.record, self.pending, self.data, self.visible = record, '', [], []

    def feed(self, text, elapsed_ms):
        self.pending += text
        while '\n' in self.pending:
            line, self.pending = self.pending.split('\n', 1)
            line = line.removesuffix('\r')
            if not line:
                if self.data:
                    self.event('\n'.join(self.data), elapsed_ms)
                    self.data = []
            elif line.startswith(':'):
                self.record.setdefault('first_sse_activity_ms', elapsed_ms)
                self.record['keepalive_lines'] = self.record.get('keepalive_lines', 0) + 1
            elif line.startswith('data:'):
                self.record.setdefault('first_sse_activity_ms', elapsed_ms)
                self.data.append(line[5:].removeprefix(' '))

    def event(self, data, elapsed_ms):
        self.record['sse_events'] = self.record.get('sse_events', 0) + 1
        self.record.setdefault('first_sse_event_ms', elapsed_ms)
        if data.strip() == '[DONE]':
            self.record.update(done_event_received=True, done_event_ms=elapsed_ms)
            return
        try:
            value = json.loads(data)
        except ValueError:
            self.record['invalid_sse_json_events'] = self.record.get('invalid_sse_json_events', 0) + 1
            return
        if not isinstance(value, dict):
            return
        self.record.setdefault('first_json_chunk_ms', elapsed_ms)
        for key in ('id', 'model'):
            if isinstance(value.get(key), str):
                self.record['response_id' if key == 'id' else 'returned_model'] = value[key]
        if isinstance(value.get('usage'), dict):
            self.record['usage'] = {key: value['usage'][key] for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                                    if isinstance(value['usage'].get(key), int)}
        if value.get('error'):
            self.record['server_error_event'] = True
        for choice in value.get('choices') or []:
            delta = choice.get('delta') or {}
            content = delta.get('content')
            if isinstance(content, str) and content:
                self.record.setdefault('first_visible_content_ms', elapsed_ms)
                self.record['visible_content_chars'] = self.record.get('visible_content_chars', 0) + len(content)
                if sum(map(len, self.visible)) < 16384:
                    self.visible.append(content)
            reasoning = delta.get('reasoning_content')
            if isinstance(reasoning, str):
                self.record['reasoning_chars_not_retained'] = self.record.get('reasoning_chars_not_retained', 0) + len(reasoning)
            if choice.get('finish_reason') is not None:
                self.record.update(finish_reason=str(choice['finish_reason'])[:80], terminal_event_ms=elapsed_ms)

    def expected_json(self):
        try:
            return json.loads(''.join(self.visible)) == {'ok': True}
        except ValueError:
            return False


def http_probe(body, *, url, api_key, verify_ssl, checkpoint=None, transport=None):
    started = time.monotonic()
    elapsed = lambda: round((time.monotonic() - started) * 1000, 3)
    record = {'started_at': now(), 'state': 'in_flight', 'stream': body['stream'],
        'model': body['model'], 'max_output_tokens': body['max_tokens'],
        'synthetic_prompt_only': True, 'retry_count': 0, 'trace': [],
        'request_json_bytes': len(json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')),
        'outcome_unknown': True}
    persist = lambda: checkpoint(record) if checkpoint else None
    def trace(event_name, event_info):
        # Event payloads can contain Authorization. Record names and times only.
        record['trace'].append({'event': event_name, 'elapsed_ms': elapsed()})
    persist()
    try:
        with httpx.Client(verify=verify_ssl, trust_env=False, timeout=170,
                          follow_redirects=False, transport=transport) as client:
            with client.stream('POST', url, json=body,
                headers={'Authorization': 'Bearer ' + api_key, 'Accept-Encoding': 'identity',
                         'Accept': 'text/event-stream' if body['stream'] else 'application/json'},
                extensions={'trace': trace}) as response:
                record.update(http_status=response.status_code, response_headers_ms=elapsed(),
                    http_version=response.http_version,
                    headers={key: response.headers[key] for key in SAFE_HEADERS if key in response.headers})
                persist()
                if response.status_code != 200:
                    content = response.read()
                    record.update(state='http_error', outcome_unknown=response.status_code >= 500 or response.status_code == 408,
                        error_body_excerpt=content.decode('utf-8', errors='replace').replace(api_key, '[REDACTED]')[:2000])
                else:
                    decoder = codecs.getincrementaldecoder('utf-8')()
                    collector = SSECollector(record)
                    ordinary, total, last = [], 0, None
                    for block in response.iter_bytes():
                        arrived = elapsed()
                        if not block:
                            continue
                        record.setdefault('first_body_bytes_ms', arrived)
                        record['body_chunks'] = record.get('body_chunks', 0) + 1
                        if last is not None:
                            record['max_body_chunk_gap_ms'] = max(record.get('max_body_chunk_gap_ms', 0), round(arrived - last, 3))
                        last, total = arrived, total + len(block)
                        if total > 1024 * 1024:
                            raise ValueError('Synthetic response exceeded the 1 MiB recording bound')
                        text = decoder.decode(block)
                        if body['stream']:
                            collector.feed(text, arrived)
                        else:
                            ordinary.append(text)
                        persist()
                    record['body_bytes_received'] = total
                    if body['stream']:
                        collector.feed(decoder.decode(b'', final=True), elapsed())
                        terminal = bool(record.get('finish_reason') or record.get('done_event_received'))
                        record['visible_json_matches_expected'] = collector.expected_json()
                    else:
                        value = json.loads(''.join(ordinary) + decoder.decode(b'', final=True))
                        record.update(response_id=value.get('id'), returned_model=value.get('model'))
                        usage = value.get('usage') or {}
                        record['usage'] = {key: usage[key] for key in ('prompt_tokens', 'completion_tokens', 'total_tokens') if isinstance(usage.get(key), int)}
                        choices = value.get('choices') or []
                        terminal = bool(choices and choices[0].get('finish_reason'))
                        if choices:
                            record['finish_reason'] = choices[0].get('finish_reason')
                            content = (choices[0].get('message') or {}).get('content') or ''
                            record['visible_content_chars'] = len(content)
                            try:
                                record['visible_json_matches_expected'] = json.loads(content) == {'ok': True}
                            except ValueError:
                                record['visible_json_matches_expected'] = False
                    known = terminal and not record.get('server_error_event')
                    record.update(state='completed' if known else 'incomplete_protocol', outcome_unknown=not known)
    except Exception as error:
        record.update(state='transport_or_protocol_error', outcome_unknown=True,
            error_type=type(error).__name__, error=str(error).replace(api_key, '[REDACTED]')[:500])
    record.update(finished_at=now(), total_elapsed_ms=elapsed())
    persist()
    return record


def authorized_plan():
    authorization = read(OUT / 'authorization.json')
    if (authorization.get('user_answer') != '允许2次客户端测试'
            or authorization.get('max_new_calls') != 2 or authorization.get('cumulative_limit') != 13):
        raise ValueError('Two synthetic client probes need the exact user authorization')
    plan = read(HERE / 'prepared_synthetic_probe_plan.json')
    assert plan['destination'] == 'https://43.143.151.41:3000/v1/chat/completions'
    assert len(plan['probes']) == 2
    for index, item in enumerate(plan['probes']):
        body = item['request_body']
        assert body['model'] == 'Gemma' and body['max_tokens'] == 32
        assert body['messages'] == [{'role': 'user', 'content': 'Return exactly this JSON object: {"ok": true}'}]
        assert body['temperature'] == 0 and body['response_format'] == {'type': 'json_object'}
        assert body['stream'] is (index == 0)
        assert set(body) == {'model', 'messages', 'temperature', 'max_tokens', 'response_format', 'stream'} | ({'stream_options'} if index == 0 else set())
    return plan


def worker(index, launch_id):
    assert index in (1, 2)
    plan = authorized_plan()
    intent = read(OUT / f'probe_{index}_intent.json')
    assert intent['launch_id'] == launch_id
    with (OUT / f'probe_{index}_dispatch.json').open('x', encoding='utf-8') as stream:
        json.dump({'at': now(), 'launch_id': launch_id, 'synthetic_only': True}, stream)
    from review_agent.cli import _configured_validators
    from review_agent.service import load_config
    client = next(v['client'] for v in _configured_validators(load_config()) if v['id'] == 'judge_b')
    assert client.base_url.rstrip('/') == 'https://43.143.151.41:3000/v1' and client.model_id == 'Gemma'
    body = plan['probes'][index - 1]['request_body']
    http_probe(body, url=plan['destination'], api_key=client.api_key, verify_ssl=client.verify_ssl,
               checkpoint=lambda result: write(OUT / f'probe_{index}_result.json', result))


def main():
    plan = authorized_plan()
    with (OUT / 'started.json').open('x', encoding='utf-8') as stream:
        json.dump({'at': now(), 'max_new_calls': 2, 'cumulative_limit': 13}, stream)
    results = []
    for index, item in enumerate(plan['probes'], 1):
        launch_id = uuid4().hex
        write(OUT / f'probe_{index}_intent.json', {'at': now(), 'launch_id': launch_id,
            'request_body': item['request_body'], 'hard_wait_seconds': item['hard_wait_seconds']})
        print(json.dumps({'phase': 'dispatch', 'probe': index, 'stream': item['request_body']['stream']}), flush=True)
        started = time.monotonic()
        process = subprocess.Popen([sys.executable, '-X', 'utf8', '-B', str(Path(__file__).resolve()),
            '--worker', str(index), launch_id], cwd=str(ROOT), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        while process.poll() is None and time.monotonic() - started < item['hard_wait_seconds']:
            time.sleep(.1)
        path = OUT / f'probe_{index}_result.json'
        result = read(path) if path.exists() else {'state': 'worker_failed', 'outcome_unknown': True}
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
            result.update(state='hard_wait_timeout', outcome_unknown=True,
                          error='Local helper stopped; server execution remains unknown', finished_at=now())
        elif result.get('state') == 'in_flight':
            result.update(state='worker_failed', outcome_unknown=True)
        write(path, result)
        results.append(result)
        print(json.dumps({'phase': 'received', 'probe': index, 'state': result['state'],
            'http_status': result.get('http_status'), 'elapsed_ms': result.get('total_elapsed_ms'),
            'first_content_ms': result.get('first_visible_content_ms')}), flush=True)
        if result['state'] != 'completed' or result['outcome_unknown']:
            break
    dispatched = len(list(OUT.glob('probe_*_dispatch.json')))
    assert dispatched <= 2
    write(OUT / 'summary.json', {'at': now(), 'new_model_calls': dispatched,
        'cumulative_call_count': 11 + dispatched, 'authorized_cumulative_limit': 13,
        'results': results, 'automatic_retries': 0, 'project_review_votes_modified': False,
        'project_materials_sent': False, 'stopped_on_first_unknown_or_error': len(results) < 2})


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--worker':
        worker(int(sys.argv[2]), sys.argv[3])
    elif sys.argv[1:] == ['--execute']:
        main()
    else:
        raise SystemExit('Use --execute only after authorization and offline verification')
