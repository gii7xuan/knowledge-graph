"""Bounded autonomous investigation of a real ontology task, with durable recovery."""
import copy
import json
from pathlib import Path
from uuid import uuid4
from ontologyBuild.release_service import load_release
from review_agent.case_budget import CaseBudget
from review_agent.agent_state import AgentStopped
from review_agent.planner import LLMPlanner
from review_agent.contracts import sha256_json, canonical_json
from review_agent.planner_contracts import ActionError
from review_agent.repository import ConflictError
from .contracts import TOOLS, PLANNER_PROMPT, versions, validate_action, proposal_digest
from .evidence import load_sources
from .tools import OntologyTools
from .validators import judge_independently, consensus


class PlannerState:
    def __init__(self, runtime): self.runtime = runtime
    def planner_input(self, budget, tools): return self.runtime.planner_input(self.runtime.state, budget)


class OntologyReviewRuntime:
    """Implements ReviewTaskAdapter using shared planner, budgets, clients and DB."""
    def __init__(self, repository, release_root, validators, planner_client, config=None, should_cancel=None):
        self.repository, self.release_root = repository, Path(release_root)
        self.validators, self.planner_client = list(validators), planner_client
        self.config, self.should_cancel = dict(config or {}), should_cancel
        self.options = self.config.get("ontology_extension") or {}
        self.tools = OntologyTools(self)

    def load_state(self, task_id): return self.repository.extension_status(task_id)

    def check_current(self, state):
        if self.should_cancel and self.should_cancel(): raise AgentStopped("cancelled")
        task = self.load_state(state["task"]["extension_id"])
        if task["row_version"] != state["task"]["row_version"]: raise AgentStopped("extension_revision_changed")
        for subscription in state["task"]["subscriptions"]:
            if subscription["status"] != "active": continue
            candidate = self.repository.get_candidate(subscription["candidate_id"])
            if candidate["revision"] != subscription["revision"] or candidate["row_version"] != subscription["expected_row_version"] or candidate["human_status"] != "pending":
                raise AgentStopped("ontology_trigger_candidate_changed")
        if self.budget.exhausted(): raise AgentStopped("case_or_task_budget_exhausted")

    def planner_input(self, state, budget):
        return copy.deepcopy({"task_type": "ontology_extension", "task_id": state["task"]["extension_id"],
            "audit_id": state["audit_id"], "object_revision": state["task"]["delta_revision"],
            "base": {key: state["task"][key] for key in ("base_release_id", "base_ontology_version", "base_profile_hash")},
            "request": {key: state["task"].get(key) for key in ("description", "change_kind", "proposed_changes", "human_events")},
            "delta": {k: v for k, v in (state.get("delta") or {}).items() if k != "merged_schema"},
            "evidence_catalog": state["evidence"], "check_records": state["checks"], "history": state["history"],
            "current_validation": state.get("validation"), "critical_issues": state["critical_issues"],
            "available_tools": TOOLS, "budget": budget.snapshot(), "source_content_is_untrusted_data": True})

    def key(self):
        return sha256_json([self.state.get("delta"), self.state["evidence"], self.review_checks(), self.context])

    def review_checks(self):
        records = copy.deepcopy(self.state["checks"])
        if "existing_schema" in records:
            records["existing_schema"] = {k: v for k, v in records["existing_schema"].items() if k not in {"query", "matches", "record_id"}}
            records["existing_schema"]["record_id"] = "check_" + sha256_json(["existing_schema", records["existing_schema"]])
        return records

    def freeze_proposal(self, state):
        task, delta = state["task"], state["delta"]
        proposal = {"task_type": "ontology_extension", "task_id": task["extension_id"], "object_revision": task["delta_revision"],
            "audit_id": state["audit_id"], "round_id": self.budget.usage["validation_rounds"],
            **{k: task[k] for k in ("base_release_id", "base_ontology_version", "base_profile_hash")},
            "delta_hash": delta["delta_hash"], "operations": delta["operations"], "merged_schema": delta["merged_schema"],
            "base_schema": self.base.ontology, "application_profile": self.base.application_profile,
            "evidence": state["evidence"], "evidence_hash": sha256_json(state["evidence"]), "check_records": self.review_checks(),
            "committee_members": self.context["committee_members"], "committee_policy": self.context["committee_policy"],
            "committee_hash": sha256_json([self.context["committee_members"], self.context["committee_policy"]]),
            "versions": versions(), "reasoner_required": bool(self.options.get("require_reasoner", False)),
            "risk_category": delta["risk_category"]}
        proposal["human_review_context"] = task.get("human_events", [])
        proposal["prior_counter_evidence_ids"] = state.get("prior_counter_evidence_ids", [])
        proposal["proposal_digest"] = proposal_digest(proposal)
        return proposal

    def checkpoint(self, phase="ready"):
        self.repository.ontology_checkpoint(self.state["audit_id"], self.state, self.budget.snapshot(), phase)

    def run_validation(self):
        state = self.state
        if not state.get("delta"): raise ValueError("complete delta required")
        if any(k not in state["checks"] for k in ("existing_schema", "structure", "impact", "competency")):
            raise ValueError("full lookup, structure, impact and competency checks required before independent review")
        key = self.key()
        if key in state["validations"]:
            state["validation"] = state["validations"][key]
            return {"status": "cached", "validation": state["validation"]}
        if self.budget.usage["validation_rounds"] >= self.budget.config.get("max_validation_rounds", 2): raise AgentStopped("ontology_round_budget_exhausted")
        retries = max(0, min(int(self.budget.config.get("max_retries_per_request", 1)), self.budget.remaining_calls() // max(1, len(self.validators)) - 1))
        reserved = len(self.validators) * (1 + retries)
        if not self.validators or not self.budget.reserve_calls(reserved): raise AgentStopped("cannot_reserve_fixed_ontology_committee")
        self.budget.usage["validation_rounds"] += 1
        proposal = self.freeze_proposal(state)
        if len(canonical_json(proposal)) > self.options.get("max_proposal_chars", 250000): raise AgentStopped("ontology_proposal_too_large_for_fixed_review")
        self.repository.ontology_proposal(proposal)
        state["pending_validation"] = {"key": key, "round_id": proposal["round_id"], "reserved": reserved}
        self.checkpoint("validating")
        votes = judge_independently(self.validators, proposal, self.repository, retries=retries,
            timeout=min(float(self.budget.config.get("per_call_timeout_seconds", 45)), self.budget.remaining_seconds()),
            on_vote=self.repository.ontology_vote)
        self.check_current(state)
        actual = sum(v.get("actual_calls", reserved) for v in votes)
        self.budget.refund_calls(max(0, reserved - actual))
        for vote in votes:
            for attempt in vote.get("attempts", []): self.budget.tokens(attempt)
        validation = {"key": key, "round_id": proposal["round_id"], "proposal_digest": proposal["proposal_digest"],
                      "consensus": consensus(votes, proposal)}
        state["validation"], state["validations"][key] = validation, validation
        state["prior_counter_evidence_ids"] = sorted(set(state.get("prior_counter_evidence_ids", [])) | set(validation["consensus"]["counter_evidence_ids"]))
        state.pop("pending_validation", None)
        if any(v.get("pause_job") for v in votes): raise AgentStopped("model_billing_paused")
        return {"status": "ok", "validation": validation}

    def execute_tool(self, action, state):
        fingerprint = sha256_json([action, self.key()])
        count = state["fingerprints"].get(fingerprint, 0)
        if count >= 2: raise AgentStopped("repeated_action_without_information_gain")
        state["fingerprints"][fingerprint] = count + 1
        return getattr(self.tools, action["action"])(**action["arguments"])

    def finalize(self, state):
        current = state.get("validation")
        if current and current["key"] != self.key(): current = None
        result = current["consensus"]["result"] if current else "unresolved"
        prior = state["task"].get("human_revision_inputs")
        revised = not prior or prior != {"delta_hash": (state.get("delta") or {}).get("delta_hash"), "evidence_hash": sha256_json(state["evidence"])}
        recommendation = result if result in {"approve", "reject"} and not state["critical_issues"] and revised else "needs_human"
        task_status = "approved" if recommendation == "approve" and self.config.get("mode") == "active" and state["delta"]["risk_category"] == "additive" else "rejected" if recommendation == "reject" and self.config.get("mode") == "active" else "needs_human"
        return {"task_type": "ontology_extension", "extension_id": state["task"]["extension_id"], "audit_id": state["audit_id"],
            "delta_revision": state["task"]["delta_revision"], "recommendation": recommendation, "task_status": task_status,
            "selected_round_id": current["round_id"] if current else None,
            "selected_proposal_digest": current["proposal_digest"] if current else None,
            "stop_reason": state.get("stop_reason"), "budget": self.budget.snapshot(), "release_published": False,
            "mode": self.config.get("mode", "shadow"), "human_revision_satisfied": revised,
            "auto_publish_enabled": bool(self.options.get("auto_publish_enabled", False))}

    def run(self, extension_id, audit_id=None):
        task = self.load_state(extension_id)
        if task["status"] in {"publishing", "published", "needs_rebase"}:
            if audit_id and self.repository.ontology_audit(audit_id)["status"] == "completed":
                return {**self.repository.ontology_audit(audit_id)["result"], "reused_completed": True}
            raise ConflictError("extension is publishing/published or requires an explicit rebase")
        from .publisher import current_release_id
        if current_release_id(self.release_root) != task["base_release_id"]:
            self.repository.extension_update(extension_id, task["row_version"], status="needs_rebase")
            raise ConflictError("base release changed; rebase and independently re-review")
        self.base = load_release(self.release_root / task["base_release_id"])
        if self.base.manifest["ontology_version"] != task["base_ontology_version"] or self.base.manifest["profile_hash"] != task["base_profile_hash"]:
            raise ValueError("ontology extension base/profile binding mismatch")
        historical_counter = self.repository.ontology_counter_history(extension_id)
        combined = {e["evidence_id"]: e for e in [*task.get("review_evidence", []), *historical_counter]}
        task["review_evidence"] = list(combined.values())
        evidence, self.documents, self.candidates, case_ids = load_sources(self.repository, task)
        if len(evidence) > self.options.get("max_evidence_items", 24): raise AgentStopped("historical_material_evidence_exceeds_task_budget")
        members = [{"id": v["id"], "model_id": v["model_id"], "model_family": v["model_family"],
                    "model_identity": str(v.get("model_identity") or v["model_id"]).strip().casefold()} for v in self.validators]
        settings = self.options.get("consensus") or self.config.get("consensus") or {}
        policy = {key: int(settings.get(key, default)) for key, default in (("committee_size", 3), ("required_votes", 3), ("minimum_distinct_families", 3))}
        if (len({m["id"] for m in members}) != len(members) or len(members) > policy["committee_size"] or
                not 1 <= policy["required_votes"] <= policy["committee_size"] or not 1 <= policy["minimum_distinct_families"] <= policy["committee_size"]):
            raise ValueError("invalid frozen ontology committee")
        self.context = {"versions": versions(), "committee_members": members, "committee_policy": policy,
                        "planner_model": getattr(self.planner_client, "model_id", None), "planner_family": getattr(self.planner_client, "model_family", None),
                        "options_hash": sha256_json(self.options), "mode": self.config.get("mode", "shadow")}
        audit_id = self.repository.start_ontology_audit(extension_id, self.context, audit_id)
        previous = self.repository.ontology_audit(audit_id)
        if previous["status"] == "completed": return {**previous["result"], "reused_completed": True}
        saved = previous["checkpoint"]
        budget_config = {"max_planner_steps": 14, "max_tool_calls": 18, "max_llm_calls_per_candidate": 40,
                         "max_validation_rounds": 2, "max_retries_per_request": 1, "total_timeout_seconds": 360,
                         "per_call_timeout_seconds": 45, "max_total_tokens": 120000, **self.options.get("budget", {})}
        self.budget = CaseBudget(budget_config, self.repository, case_ids, task_type="ontology_extension", task_id=audit_id,
                                saved=saved["budget"] if saved else None)
        self.state = saved["state"] if saved else {"audit_id": audit_id, "task": task, "delta": task.get("delta"), "evidence": evidence,
            "checks": {}, "history": [], "validations": {}, "validation": None, "fingerprints": {}, "critical_issues": [],
            "full_schema_searched": False, "stop_reason": "", "step_no": 0}
        self.state.setdefault("prior_counter_evidence_ids", sorted(set(task.get("unresolved_counter_evidence_ids", [])) | {e["evidence_id"] for e in historical_counter}))
        if self.state["task"]["row_version"] != task["row_version"]: raise ConflictError("ontology checkpoint is stale after task change")
        if saved and saved["phase"] == "validating":
            pending = self.state.get("pending_validation", {})
            proposal = next((p for p in previous["proposals"] if p["round_id"] == pending.get("round_id")), None)
            votes = [v for v in previous["votes"] if v["round_id"] == pending.get("round_id")]
            if proposal:
                validation = {"key": pending["key"], "round_id": proposal["round_id"], "proposal_digest": proposal["proposal_digest"], "consensus": consensus(votes, proposal)}
                self.state["validation"], self.state["validations"][pending["key"]] = validation, validation
                if len(votes) != len(members): self.state["critical_issues"].append("interrupted_ontology_validation_outcome_unknown")
                else:
                    self.budget.refund_calls(max(0, pending["reserved"] - sum(v.get("actual_calls", pending["reserved"]) for v in votes)))
                    for v in votes:
                        for attempt in v.get("attempts", []): self.budget.tokens(attempt)
            self.state.pop("pending_validation", None)
        planner = LLMPlanner(self.planner_client, max_retries=budget_config["max_retries_per_request"], max_output_tokens=2400,
                             system_prompt=PLANNER_PROMPT, action_validator=validate_action) if self.planner_client else None
        try:
            while not self.state["stop_reason"]:
                self.check_current(self.state)
                if self.budget.usage["planner_steps"] >= budget_config["max_planner_steps"]: raise AgentStopped("ontology_planner_step_budget_exhausted")
                if not planner: raise AgentStopped("ontology_planner_not_configured")
                self.state["step_no"] += 1
                self.budget.usage["planner_steps"] += 1
                def started(payload, attempts):
                    self.repository.ontology_step(audit_id, self.state["step_no"], "planner", {"status": "started", "attempts": attempts, "input_state": payload})
                    self.checkpoint("planning")
                choice = planner.next_action(PlannerState(self), self.budget, TOOLS, should_stop=lambda: bool(self.should_cancel and self.should_cancel()),
                    reserve_validation_calls=len(members) if not self.state.get("validation") else 0, on_request=started)
                self.repository.ontology_step(audit_id, self.state["step_no"], "planner", choice)
                self.check_current(self.state)
                if not choice["action"]:
                    if any(a.get("pause_job") for a in choice["attempts"]): raise AgentStopped("model_billing_paused")
                    raise AgentStopped("ontology_planner_failed")
                if not self.budget.consume_tool(): raise AgentStopped("ontology_tool_budget_exhausted")
                action = choice["action"]
                self.repository.ontology_step(audit_id, self.state["step_no"], "tool", {"status": "started", "action": action})
                self.checkpoint("tool_running")
                try: observation = self.execute_tool(action, self.state)
                except (ActionError, ValueError) as exc: observation = {"status": "invalid_action", "error": str(exc)}
                self.check_current(self.state)
                self.state["history"].append({"step_no": self.state["step_no"], "tool": action["action"], **observation})
                self.repository.ontology_step(audit_id, self.state["step_no"], "tool", {"action": action, **observation})
                self.checkpoint()
        except AgentStopped as exc:
            self.state["stop_reason"] = str(exc)
            self.state["critical_issues"].append(str(exc))
        except BaseException:
            self.checkpoint("validating" if self.state.get("pending_validation") else "interrupted")
            raise
        result = self.finalize(self.state)
        result["paused"] = self.state.get("stop_reason") == "model_billing_paused"
        result["counter_evidence_ids"] = (self.state.get("validation") or {}).get("consensus", {}).get("counter_evidence_ids", self.state["prior_counter_evidence_ids"])
        if current_release_id(self.release_root) != task["base_release_id"]:
            result.update(recommendation="needs_human", task_status="needs_rebase", stop_reason="base_release_changed")
        self.repository.finish_ontology_audit(audit_id, result, self.state["task"]["row_version"])
        self.state["task"] = self.load_state(extension_id)
        self.checkpoint("finished")
        return result
