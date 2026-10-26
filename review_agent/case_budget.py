"""Durable, conservative full-cost charging across both agents and revisions."""
from uuid import uuid4
from .agent_state import AgentBudget, AgentStopped

DEFAULT_CASE_LIMITS = {"llm_calls": 96, "tokens": 240000, "tool_calls": 64, "extension_requests": 1, "rebinds": 2}


class CaseBudget(AgentBudget):
    def __init__(self, config, repository, case_ids, *, task_type, task_id, saved=None, deadline_epoch=None):
        super().__init__(config, saved, deadline_epoch)
        self.repository, self.case_ids = repository, sorted(set(case_ids))
        self.task_type, self.task_id = task_type, task_id
        self.reservations = []

    def charge(self, cost, event_id=None):
        self.repository.charge_cases(self.case_ids, cost, event_id or uuid4().hex, self.task_type, self.task_id)

    def reserve_calls(self, count):
        if self.exhausted() or self.remaining_calls() < count: return False
        try: self.charge({"llm_calls": count})
        except AgentStopped: return False
        return super().reserve_calls(count)

    def refund_calls(self, count):
        count = max(0, count)
        if count: self.charge({"llm_calls": -count})
        super().refund_calls(count)

    def consume_tool(self):
        if self.exhausted() or self.usage["tool_calls"] >= int(self.config.get("max_tool_calls", 8)): return False
        try: self.charge({"tool_calls": 1})
        except AgentStopped: return False
        return super().consume_tool()

    def tokens(self, attempt):
        super().tokens(attempt)
        if not attempt.get("cached"):
            total = sum(max(0, attempt.get(k) or 0) for k in ("input_tokens", "output_tokens"))
            if total: self.charge({"tokens": total})

    def snapshot(self):
        return {**super().snapshot(), "case_ids": self.case_ids,
                "shared_cost_policy": "full_cost_charged_to_each_subscribed_case",
                "case_accounts": [self.repository.case_status(case_id) for case_id in self.case_ids]}
