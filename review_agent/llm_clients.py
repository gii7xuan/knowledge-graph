"""Explicit LLM clients used by validators and the bounded planner."""

from __future__ import annotations

import json
import copy
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional


@dataclass(frozen=True)
class LLMResponse:
    content: Any
    latency_ms: int = 0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cached: bool = False
    returned_model_id: Optional[str] = None
    response_id: Optional[str] = None
    finish_reason: Optional[str] = None
    reasoning_tokens: Optional[int] = None
    transport_metrics: Optional[Dict[str, Any]] = None


def response_metadata(response):
    """Preserve usage and transport diagnostics on rejected as well as valid output."""
    if response is None:
        return {}
    return {name: copy.deepcopy(getattr(response, name, None)) for name in (
        'latency_ms', 'input_tokens', 'output_tokens', 'returned_model_id', 'response_id',
        'finish_reason', 'reasoning_tokens', 'transport_metrics')}


class MockLLMClient:
    """Offline client with deterministic queued, keyed, or callable responses."""
    def __init__(
        self, responses: Any, *, model_id: str = "mock-model", model_family: str = "mock-family"
    ):
        self.model_id = model_id
        self.model_family = model_family
        self.calls: List[Dict[str, Any]] = []
        self._callable = responses if callable(responses) else None
        self._mapping = dict(responses) if isinstance(responses, Mapping) else None
        self._queue = list(responses) if isinstance(responses, (list, tuple)) else None

    def complete_json(self, payload: Mapping[str, Any], **_: Any) -> LLMResponse:
        self.calls.append(dict(payload))
        if self._callable:
            value = self._callable(payload)
        elif self._mapping is not None:
            key = str(payload.get("proposal_digest") or payload.get("action") or "default")
            value = self._mapping.get(key, self._mapping.get("default"))
        elif self._queue is not None and self._queue:
            value = self._queue.pop(0)
        else:
            raise RuntimeError("mock response unavailable")
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, LLMResponse) else LLMResponse(value)


class OpenAICompatibleClient:
    """Lazy OpenAI-compatible wrapper; importing the module performs no I/O."""
    def __init__(
        self, *, api_key: str, base_url: str, model_id: str, model_family: str,
        timeout_seconds: float = 45.0,
        verify_ssl: Optional[bool] = None,
        thinking_mode: Optional[str] = None,
        stream_responses: bool = False,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model_id = model_id
        self.model_family = model_family
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = verify_ssl
        if thinking_mode not in {None, 'enabled', 'disabled'}:
            raise ValueError('thinking_mode must be enabled, disabled or unset')
        self.thinking_mode = thinking_mode
        if not isinstance(stream_responses,bool): raise ValueError('stream_responses must be boolean')
        self.stream_responses = stream_responses
        self._client = None

    def _get_client(self):
        if self._client is None:
            import httpx
            from openai import OpenAI
            verify_ssl = os.environ.get("KG_LLM_VERIFY_SSL", "true").strip().lower() not in {
                "0", "false", "no", "off",
            }
            if self.verify_ssl is not None:
                verify_ssl = self.verify_ssl
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                max_retries=0,
                http_client=httpx.Client(trust_env=False, verify=verify_ssl),
            )
        return self._client

    def complete_json(self, payload: Mapping[str, Any], *, system_prompt: str = "", **kwargs: Any) -> LLMResponse:
        started = time.monotonic()
        user_suffix=kwargs.get('user_suffix','')
        if not isinstance(user_suffix,str):raise ValueError('user_suffix must be text')
        user_content=json.dumps(payload,ensure_ascii=False,**({'separators':(',',':')} if kwargs.get('compact_json') else {}))+(('\n\n'+user_suffix) if user_suffix else '')
        output_format=copy.deepcopy(kwargs.get('response_format',{'type':'json_object'}))
        if not isinstance(output_format,dict) or output_format.get('type') not in {'json_object','json_schema'}:
            raise ValueError('unsupported JSON response format')
        if output_format['type']=='json_schema':
            spec=output_format.get('json_schema')
            if not isinstance(spec,dict) or spec.get('strict') is not True or not isinstance(spec.get('schema'),dict) or not isinstance(spec.get('name'),str) or not spec['name']:
                raise ValueError('JSON Schema output requires a named strict schema')
        response = self._get_client().chat.completions.create(
            model=self.model_id,
            messages=[
                {"role": "system", "content": system_prompt + "\nReturn only a valid JSON object."},
                {"role": "user", "content": user_content},
            ],
            response_format=output_format,
            temperature=float(kwargs.get("temperature", 0.0)),
            **({'extra_body':{'thinking':{'type':self.thinking_mode}}} if self.thinking_mode else {}),
            **({'stream':True,'stream_options':{'include_usage':True}} if self.stream_responses else {}),
            **({'timeout':float(kwargs['timeout_seconds'])} if kwargs.get('timeout_seconds') else {}),
            **({"max_tokens": int(kwargs["max_tokens"])} if kwargs.get("max_tokens") else {}),
        )
        if self.stream_responses:
            parts=[];usage=None;finish=None;response_id=None;model=None
            first_chunk=None;first_content=None;terminal=None;last_chunk=None;max_gap=0;chunks=0;reasoning_chars=0
            def snapshot():
                return LLMResponse(''.join(parts),latency_ms=int((time.monotonic()-started)*1000),
                    input_tokens=getattr(usage,'prompt_tokens',None),output_tokens=getattr(usage,'completion_tokens',None),
                    returned_model_id=model,response_id=response_id,finish_reason=finish,
                    reasoning_tokens=getattr(getattr(usage,'completion_tokens_details',None),'reasoning_tokens',None),
                    transport_metrics={'stream':True,'first_chunk_ms':first_chunk,'first_content_ms':first_content,
                        'terminal_ms':terminal,'max_inter_chunk_gap_ms':max_gap if chunks>1 else None,
                        'chunk_count':chunks,'visible_chars':sum(map(len,parts)),'reasoning_chars':reasoning_chars})
            try:
                for chunk in response:
                    arrived=int((time.monotonic()-started)*1000)
                    if first_chunk is None:first_chunk=arrived
                    if last_chunk is not None:max_gap=max(max_gap,arrived-last_chunk)
                    last_chunk=arrived;chunks+=1
                    usage=getattr(chunk,'usage',None) or usage
                    response_id=getattr(chunk,'id',None) or response_id
                    model=getattr(chunk,'model',None) or model
                    for choice in getattr(chunk,'choices',[]) or []:
                        delta=getattr(choice,'delta',None)
                        content=getattr(delta,'content',None)
                        if content:
                            if first_content is None:first_content=arrived
                            parts.append(content)
                        # Count, but never retain or log, private reasoning text.
                        reasoning=getattr(delta,'reasoning_content',None)
                        if isinstance(reasoning,str):reasoning_chars+=len(reasoning)
                        finish=getattr(choice,'finish_reason',None) or finish
                        if finish is not None and terminal is None:terminal=arrived
                if finish is None:raise ConnectionError('stream ended without a terminal completion; provider outcome unknown')
            except Exception as exc:
                exc.llm_response=snapshot()
                raise
            finally:
                close=getattr(response,'close',None)
                if close: close()
            return snapshot()
        usage = getattr(response, "usage", None)
        return LLMResponse(
            response.choices[0].message.content,
            latency_ms=int((time.monotonic() - started) * 1000),
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            returned_model_id=getattr(response, "model", None), response_id=getattr(response, "id", None),
            finish_reason=getattr(response.choices[0], 'finish_reason', None),
            reasoning_tokens=getattr(getattr(usage, 'completion_tokens_details', None), 'reasoning_tokens', None),
        )
