"""Explicit LLM clients used by validators and the bounded planner."""

from __future__ import annotations

import json
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
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model_id = model_id
        self.model_family = model_family
        self.timeout_seconds = timeout_seconds
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout_seconds)
        return self._client

    def complete_json(self, payload: Mapping[str, Any], *, system_prompt: str = "", **kwargs: Any) -> LLMResponse:
        started = time.monotonic()
        response = self._get_client().chat.completions.create(
            model=self.model_id,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            temperature=float(kwargs.get("temperature", 0.0)),
        )
        usage = getattr(response, "usage", None)
        return LLMResponse(
            response.choices[0].message.content,
            latency_ms=int((time.monotonic() - started) * 1000),
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
        )
