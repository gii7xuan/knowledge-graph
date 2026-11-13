"""Content-free HTTP/SSE diagnostics for the synchronous model client."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import time
import httpx

_active = ContextVar('review_transport_diagnostics', default=None)
SAFE_HEADERS = frozenset({
    'server', 'date', 'content-type', 'content-length', 'content-encoding',
    'transfer-encoding', 'connection', 'via', 'x-request-id',
    'x-oneapi-request-id', 'traceparent', 'x-accel-buffering',
})


class TransportDiagnostics:
    def __init__(self, *, secrets=(), clock=None):
        self.clock = clock or time.perf_counter
        self.started = self.clock()
        self.secrets = tuple(value for value in secrets if isinstance(value, str) and value)
        self.metrics = {'trace': []}
        self.headers = {}
        self.last_body = None

    def elapsed(self):
        return round((self.clock() - self.started) * 1000, 3)

    def redact(self, value):
        text = str(value)
        for secret in self.secrets:
            text = text.replace(secret, '[REDACTED]')
        return text

    def trace(self, name, info):
        # info may contain Authorization, request bodies, or TLS internals.
        if len(self.metrics['trace']) < 256:
            self.metrics['trace'].append({'event': str(name), 'elapsed_ms': self.elapsed()})

    def response(self, response):
        self.metrics.update(response_headers_ms=self.elapsed())
        if getattr(response, 'status_code', None) is not None:
            self.metrics['http_status'] = response.status_code
        headers = getattr(response, 'headers', {})
        self.headers = {str(name).lower(): self.redact(value).replace('\r', '').replace('\n', '')[:512]
                        for name, value in headers.items() if str(name).lower() in SAFE_HEADERS}

    def body(self, size):
        if not size:
            return
        arrived = self.elapsed()
        self.metrics.setdefault('first_body_byte_ms', arrived)
        self.metrics['body_chunk_count'] = self.metrics.get('body_chunk_count', 0) + 1
        self.metrics['body_bytes_received'] = self.metrics.get('body_bytes_received', 0) + size
        if self.last_body is not None:
            self.metrics['max_inter_body_chunk_gap_ms'] = max(
                self.metrics.get('max_inter_body_chunk_gap_ms', 0), round(arrived - self.last_body, 3))
        self.last_body = arrived

    @property
    def gateway_request_id(self):
        return self.headers.get('x-oneapi-request-id') or self.headers.get('x-request-id')

    def snapshot(self):
        return {**deepcopy(self.metrics), 'response_headers': deepcopy(self.headers),
                'gateway_request_id': self.gateway_request_id, 'transport_elapsed_ms': self.elapsed()}


class _ObservedStream(httpx.SyncByteStream):
    def __init__(self, original, diagnostics):
        self.original, self.diagnostics = original, diagnostics

    def __iter__(self):
        for block in self.original:
            self.diagnostics.body(len(block))
            yield block

    def close(self):
        self.original.close()


@contextmanager
def active_transport(diagnostics):
    token = _active.set(diagnostics)
    try:
        yield
    finally:
        _active.reset(token)


def request_hook(request):
    diagnostics = _active.get()
    if diagnostics is None:
        return
    previous = request.extensions.get('trace')
    def trace(name, info):
        diagnostics.trace(name, info)
        if previous:
            previous(name, info)
    request.extensions['trace'] = trace


def response_hook(response):
    diagnostics = _active.get()
    if diagnostics is None:
        return
    diagnostics.response(response)
    if not response.is_stream_consumed:
        response.stream = _ObservedStream(response.stream, diagnostics)


def exception_metadata(error):
    """Keep partial counts/timings, never partial text or private reasoning."""
    result = {}
    partial = getattr(error, 'llm_response', None)
    for name in ('latency_ms', 'input_tokens', 'output_tokens', 'returned_model_id',
                 'response_id', 'finish_reason', 'reasoning_tokens'):
        value = getattr(partial, name, None)
        if value is not None:
            result[name] = deepcopy(value)
    metrics = getattr(error, 'transport_metrics', None) or getattr(partial, 'transport_metrics', None)
    if isinstance(metrics, dict):
        result['transport_metrics'] = deepcopy(metrics)
    identity = getattr(error, 'gateway_request_id', None) or getattr(partial, 'gateway_request_id', None)
    if identity:
        result['gateway_request_id'] = identity
    return result


def safe_error_text(error, client):
    text = str(error)
    key = getattr(client, 'api_key', None)
    return text.replace(key, '[REDACTED]') if isinstance(key, str) and key else text
