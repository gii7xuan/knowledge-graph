"""One real JSON-model action per step; observations are supplied by the runtime."""

import queue
import threading
import time

from .agent_state import AgentStopped
from .execution import api_error_fields
from .planner_contracts import SYSTEM_PROMPT, ActionError, validate_action
from .validators import _parse_content
from .contracts import sha256_json


def bounded_json(client, payload, timeout, *, should_stop=None, max_tokens=1024, system_prompt=SYSTEM_PROMPT):
    output = queue.Queue(maxsize=1)

    def invoke():
        try:
            output.put((client.complete_json(payload, system_prompt=system_prompt, max_tokens=max_tokens), None))
        except Exception as exc:
            output.put((None, exc))

    began = time.monotonic()
    threading.Thread(target=invoke, name="bounded-planner-request", daemon=True).start()
    while time.monotonic() - began < timeout:
        if should_stop and should_stop():
            raise AgentStopped("cancelled_or_revision_changed")
        try:
            item = output.get(timeout=min(.05, max(.001, timeout - (time.monotonic() - began))))
            if time.monotonic() - began < timeout:
                return item
        except queue.Empty:
            pass
    return None, TimeoutError("planner request exceeded hard deadline")


class LLMPlanner:
    def __init__(self, client, *, max_retries=1, max_output_tokens=1024, system_prompt=SYSTEM_PROMPT, action_validator=validate_action):
        self.client, self.max_retries = client, max(0, int(max_retries))
        self.max_output_tokens = int(max_output_tokens)
        self.system_prompt, self.action_validator = system_prompt, action_validator

    def next_action(self, state, budget, tools, *, should_stop=None, reserve_validation_calls=0, on_request=None):
        attempts, feedback = [], None
        for _ in range(self.max_retries + 1):
            if budget.remaining_calls() <= reserve_validation_calls or not budget.reserve_calls(1):
                break
            payload = state.planner_input(budget, tools)
            if feedback:
                payload["correction_feedback"] = feedback
            if on_request:
                on_request(payload, attempts)
            began = time.monotonic()
            response, error = bounded_json(self.client, payload,
                min(float(budget.config.get("per_call_timeout_seconds", 45)), budget.remaining_seconds()),
                should_stop=should_stop, max_tokens=self.max_output_tokens, system_prompt=self.system_prompt)
            attempt = {"attempt_no": len(attempts) + 1,
                       "latency_ms": int((time.monotonic() - began) * 1000),
                       "input_state": payload, "input_hash": sha256_json(payload)}
            action = None
            if response is not None:
                attempt.update(input_tokens=response.input_tokens, output_tokens=response.output_tokens)
                try:
                    action = self.action_validator(_parse_content(response.content))
                    attempt["response_status"] = "ok"
                except (ValueError, TypeError) as exc:
                    attempt.update(response_status="invalid_action", error=str(exc))
            else:
                timed_out = isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower()
                attempt.update(response_status="timeout" if timed_out else "api_error",
                               error=str(error), **api_error_fields(error))
                if timed_out:
                    attempt.update(retryable=False, outcome_unknown=True)
            budget.tokens(attempt)
            attempts.append(attempt)
            if action is not None:
                return {"action": action, "status": "ok", "attempts": attempts}
            feedback = {"error": attempt.get("error"), "instruction": "仅修正动作契约，不得编造工具观察。"}
            if attempt.get("retryable") is False:
                break
        return {"action": None, "status": "failed", "attempts": attempts,
                "error": attempts[-1].get("error") if attempts else "planner call budget exhausted"}
