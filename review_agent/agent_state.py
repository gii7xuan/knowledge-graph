"""Server-owned checkpoint state and a single budget across planning and tools."""

import copy
import time
from dataclasses import dataclass, field

from .contracts import sha256_json


class AgentStopped(RuntimeError):
    pass


class AgentBudget:
    def __init__(self, config, saved=None, deadline_epoch=None):
        self.config = dict(config)
        self.usage = {"llm_calls": 0, "tool_calls": 0, "planner_steps": 0, "validation_rounds": 0,
                      "input_tokens": 0, "output_tokens": 0, "unknown_token_requests": 0}
        if saved:
            self.usage.update(saved["usage"])
        self.deadline_epoch = min(x for x in [deadline_epoch, (saved or {}).get("deadline_epoch"),
            time.time() + float(config.get("total_timeout_seconds", 240))] if x is not None)
        self.started = time.monotonic()
        self.allowed_seconds = max(0., self.deadline_epoch - time.time())

    def remaining_seconds(self):
        return max(0., min(self.deadline_epoch - time.time(), self.allowed_seconds - (time.monotonic() - self.started)))

    def remaining_calls(self):
        return max(0, int(self.config.get("max_llm_calls_per_candidate", 24)) - self.usage["llm_calls"])

    def exhausted(self):
        maximum = self.config.get("max_total_tokens")
        return self.remaining_seconds() <= 0 or bool(maximum and self.usage["input_tokens"] + self.usage["output_tokens"] >= int(maximum))

    def reserve_calls(self, count):
        if self.exhausted() or self.remaining_calls() < count:
            return False
        self.usage["llm_calls"] += count
        return True

    def refund_calls(self, count):
        self.usage["llm_calls"] -= max(0, count)

    def consume_tool(self):
        if self.exhausted() or self.usage["tool_calls"] >= int(self.config.get("max_tool_calls", 8)):
            return False
        self.usage["tool_calls"] += 1
        return True

    def tokens(self, attempt):
        if attempt.get("cached"):
            return
        known = False
        for name in ("input_tokens", "output_tokens"):
            value = attempt.get(name)
            if type(value) is int:
                self.usage[name] += max(0, value)
                known = True
        if not known:
            self.usage["unknown_token_requests"] += 1

    def snapshot(self):
        return {"usage": dict(self.usage), "deadline_epoch": self.deadline_epoch,
                "remaining_seconds": self.remaining_seconds(), "remaining_llm_calls": self.remaining_calls(),
                "max_planner_steps": int(self.config.get("max_planner_steps", 6)),
                "max_validation_rounds": int(self.config.get("max_validation_rounds", 2))}


@dataclass
class AgentState:
    candidate: dict
    context: dict
    audit_id: str
    archive: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    bundle: dict = field(default_factory=dict)
    guard: dict = field(default_factory=lambda: {"status": "unknown"})
    ontology_checked: bool = False
    observations: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    validations: dict = field(default_factory=dict)
    votes: list = field(default_factory=list)
    consensus: dict = field(default_factory=dict)
    gaps: dict = field(default_factory=dict)
    protected_ids: list = field(default_factory=list)
    fingerprints: dict = field(default_factory=dict)
    searched_scopes: list = field(default_factory=list)
    revision_drafts: list = field(default_factory=list)
    state_version: int = 0
    step_no: int = 0
    executed_strategy: str = "llm_react"
    stop_reason: str = ""
    pending_action: dict = field(default_factory=dict)
    current_validation_key: str = ""
    issue_events: list = field(default_factory=list)
    evidence_exclusions: dict = field(default_factory=dict)
    ontology_lookup: dict = field(default_factory=dict)
    workflow_state: str = "ready_for_review"
    pending_extension_id: str = ""

    def validation_key(self):
        return sha256_json({"candidate": self.candidate, "bundle": self.bundle.get("evidence_hash"),
                            "guard": self.guard, "versions": self.context.get("agent_versions"),
                            "committee": self.context.get("committee_members"),
                            "memory": self.context.get("memory_bundle_hash")})

    def current_validation(self):
        return self.validations.get(self.validation_key())

    def export(self):
        return copy.deepcopy(self.__dict__)

    def digest(self):
        return sha256_json(self.export())

    def planner_input(self, budget, tools):
        return copy.deepcopy({"identity": {k: self.candidate.get(k) for k in ("candidate_id", "revision", "run_id", "document_version", "ontology_version")},
                "audit_id": self.audit_id, "state_version": self.state_version,
                "candidate": self.candidate.get("triple", {}), "frozen_versions": self.context.get("agent_versions"),
                "evidence_catalog": self.archive, "selected_evidence_ids": [x["evidence_id"] for x in self.bundle.get("items", [])],
                "ontology_check": self.guard, "ontology_checked": self.ontology_checked,
                "unresolved_issues": [i for i in self.issues if i.get("status", "open") != "resolved"],
                "issue_history": self.issues, "evidence_exclusions": self.evidence_exclusions,
                "ontology_lookup": self.ontology_lookup, "workflow_state": self.workflow_state,
                "pending_extension_id": self.pending_extension_id, "history": self.observations,
                "current_validation": self.current_validation(), "previous_votes": self.votes,
                "revision_drafts": self.revision_drafts, "available_tools": tools, "budget": budget.snapshot(),
                "source_content_is_untrusted_data": True,
                "memory_examples_are_not_current_evidence": True})
