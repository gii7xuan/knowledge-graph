"""Source and schema tools execute real deterministic functions, never LLM claims."""
import json
from copy import deepcopy
from difflib import SequenceMatcher
from ontologyBuild.ontology_adapter import normalize_name
from review_agent.contracts import sha256_json
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from evidence.hierarchical_retriever import retrieve_more
from evidence.verification import verify_evidence_bundle
from schema.relation_registry import RelationRegistry
from .delta import apply_delta, catalog, check_structure


def remap(candidate, schema):
    candidate = deepcopy(candidate)
    triple = candidate["triple"]
    registry = RelationRegistry(schema)
    retired = schema.get("retired_schema_ids", {})
    value = retired.get(triple.get("relation_id"), triple.get("relation_id"))
    lookup = registry.resolve(value or triple.get("surface_relation") or triple.get("relation") or "")
    if not lookup["relation_id"]:
        lookup = registry.resolve(triple.get("surface_relation") or triple.get("relation") or "")
    triple["relation_id"] = lookup["relation_id"]
    triple["mapping_status"] = lookup["status"]
    if lookup["relation_id"]: triple["relation_label"] = registry.properties[lookup["relation_id"]].get("label", lookup["relation_id"])
    guard = OntologyGuard(load_snapshot(schema))
    for field in ("head_type", "tail_type"):
        value = retired.get(triple.get(field), triple.get(field))
        if value: triple[field] = guard.class_index.get(normalize_name(value)) or value
    return candidate


class OntologyTools:
    def __init__(self, runtime): self.runtime = runtime
    @property
    def state(self): return self.runtime.state

    def check_record(self, name, result):
        result = {**result, "delta_hash": (self.state.get("delta") or {}).get("delta_hash"),
                  "evidence_hash": sha256_json(self.state["evidence"])}
        result["record_id"] = "check_" + sha256_json([name, result])
        self.state["checks"][name] = result
        self.state["validation"] = None
        return result

    def search_existing_schema(self, query):
        terms = {normalize_name(query)}
        for op in (self.state.get("delta") or {}).get("operations", []):
            if op["changes"].get("label"): terms.add(normalize_name(op["changes"]["label"]))
        items, matches = catalog(self.runtime.base.ontology), []
        for identity, item in items.items():
            names = [identity, item.get("label", ""), *(item.get("aliases") or [])]
            exact = any(normalize_name(n) in terms for n in names if n)
            score = max((SequenceMatcher(None, t, normalize_name(n)).ratio() for t in terms for n in names if n), default=0)
            if exact or score >= .45: matches.append({**item, "exact": exact, "recall_score": score, "semantic_equivalence_confirmed": False})
        self.state["full_schema_searched"] = True
        result = {"status": "pass", "scope": "full_base_release", "base_release_id": self.state["task"]["base_release_id"],
                  "query": query, "items_scanned": len(items), "matches": sorted(matches, key=lambda i: -i["recall_score"])[:16],
                  "search_is_not_gap_or_equivalence_proof": True}
        return self.check_record("existing_schema", result)

    def retrieve_definition_evidence(self, query, scope, limit):
        new, seen = [], {e["evidence_id"] for e in self.state["evidence"]}
        trace = []
        for candidate_id, document in self.runtime.documents.items():
            candidate = self.runtime.candidates[candidate_id]
            found = verify_evidence_bundle(retrieve_more(candidate, document, {"scope": scope, "query": query, "limit": limit}, list(seen)), document, candidate)
            trace.append({"candidate_id": candidate_id, "scope": scope, "returned": len(found["items"]), "rejected": found.get("rejected_items", [])})
            for item in found["items"]:
                if item["evidence_id"] not in seen: new.append(item); seen.add(item["evidence_id"])
        if len(self.state["evidence"]) + len(new) > self.runtime.options.get("max_evidence_items", 24):
            self.state["critical_issues"].append("definition_evidence_budget_exhausted")
            return {"status": "budget_exhausted", "trace": trace, "omitted_count": len(new)}
        if new:
            self.state["evidence"].extend(new)
            self.state["checks"], self.state["validation"] = {}, None
        return {"status": "ok" if new else "empty", "items": new, "trace": trace, "semantic_status": "unknown"}

    def inspect_schema_neighborhood(self, schema_id):
        schema = (self.state.get("delta") or {}).get("merged_schema") or self.runtime.base.ontology
        items = catalog(schema)
        if schema_id not in items: raise ValueError("schema ID does not exist")
        related = [i for i in items.values() if schema_id in [i.get("parent"), *(i.get("domain") or []), *(i.get("range") or []), *(i.get("subclasses") or [])]]
        return {"status": "ok", "item": items[schema_id], "incoming_references": related}

    def propose_schema_delta(self, operations):
        if not self.state["full_schema_searched"]: raise ValueError("search complete base schema before drafting")
        delta = apply_delta(self.runtime.base, operations, {e["evidence_id"] for e in self.state["evidence"]})
        if (self.state.get("delta") or {}).get("delta_hash") == delta["delta_hash"]: return {"status": "unchanged", "delta_hash": delta["delta_hash"]}
        task = self.state["task"]
        revision = task["delta_revision"] + (1 if task.get("delta") else 0)
        task = self.runtime.repository.extension_update(task["extension_id"], task["row_version"],
            changes={"delta": delta, "delta_revision": revision, "latest_audit_id": self.state["audit_id"]}, status="in_review")
        self.state.update(task=task, delta=delta, checks={}, validation=None)
        self.search_existing_schema(task["description"])
        return {"status": "draft", "delta_revision": revision, "delta_hash": delta["delta_hash"], "operations": delta["operations"], "risk_category": delta["risk_category"]}

    def validate_schema_structure(self):
        delta = self.require_delta()
        return self.check_record("structure", check_structure(delta["merged_schema"], self.runtime.base.application_profile, self.runtime.base.ontology))

    def require_delta(self):
        if not self.state.get("delta"): raise ValueError("draft complete ontology delta first")
        return self.state["delta"]

    def run_reasoner_checks(self):
        import subprocess
        delta = self.require_delta()
        settings = self.runtime.options.get("reasoner") or {}
        command = settings.get("command")
        if not command:
            return self.check_record("reasoner", {"status": "not_run", "executed": False,
                "reason": "No configured OWL reasoner adapter. Project structure checks do not establish OWL consistency."})
        if not isinstance(command, list) or any(not isinstance(p, str) or not p for p in command): raise ValueError("reasoner command must be a trusted configuration argv list")
        binding = {"base_release_id": self.state["task"]["base_release_id"], "delta_hash": delta["delta_hash"],
                   "ontology_version": sha256_json(delta["merged_schema"]), "profile_hash": self.state["task"]["base_profile_hash"]}
        try:
            completed = subprocess.run(command, input=json.dumps({**binding, "ontology": delta["merged_schema"]}, ensure_ascii=False),
                capture_output=True, text=True, encoding="utf-8", shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=min(float(settings.get("timeout_seconds", 30)), self.runtime.budget.remaining_seconds()))
            report = json.loads(completed.stdout)
            bound = isinstance(report, dict) and all(report.get(k) == v for k, v in binding.items())
            valid = bound and report.get("executed") is True and isinstance(report.get("engine"), str) and bool(report["engine"])
            status = ("pass" if valid and completed.returncode == 0 and report.get("status") == "pass" and report.get("consistent") is True
                      else "fail" if valid and report.get("consistent") is False else "unknown")
            return self.check_record("reasoner", {"status": status, "executed": True, "command_hash": sha256_json(command),
                "report_binding_valid": bool(bound), "report": report, "exit_code": completed.returncode})
        except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
            return self.check_record("reasoner", {"status": "unknown", "executed": False, "error": str(exc), "command_hash": sha256_json(command)})

    def analyze_schema_impact(self):
        delta = self.require_delta()
        from .impact import analyze_impact
        impact = analyze_impact(self.runtime.repository,self.state["task"]["base_release_id"],self.runtime.base.ontology,delta["merged_schema"],remap)
        return self.check_record("impact", {**impact, "risk_category": delta["risk_category"],
            "historical_candidates_auto_accepted": False, "manual_review_required": delta["risk_category"] == "high_impact"})

    def run_competency_checks(self):
        schema = self.require_delta()["merged_schema"]
        guard = OntologyGuard(load_snapshot(schema))
        results = []
        for candidate_id, candidate in self.runtime.candidates.items():
            check = guard.check(remap(candidate, schema))
            passed = check.get("domain_status") == check.get("range_status") == "pass" and not check.get("hard_failure")
            results.append({"id": "trigger_" + candidate_id, "status": "pass" if passed else "fail", "guard": check})
        questions = json.loads((self.runtime.base.path / "competency_questions.json").read_text(encoding="utf-8"))
        if not isinstance(questions, list): questions = [questions] if questions else []
        for number, question in enumerate(questions):
            result = {"id": str(question.get("id", number)) if isinstance(question, dict) else str(number), "status": "unknown"}
            if isinstance(question, dict) and question.get("type") == "schema_reference":
                present = question.get("schema_id") in catalog(schema)
                result["status"] = "pass" if present == (question.get("expected", "present") == "present") else "fail"
            elif isinstance(question, dict) and question.get("type") == "guard" and isinstance(question.get("candidate"), dict):
                check = guard.check(question["candidate"])
                result.update(status="pass" if check["status"] == question.get("expected_status", "pass") else "fail", guard=check)
            results.append(result)
        status = "fail" if any(r["status"] == "fail" for r in results) else "unknown" if any(r["status"] == "unknown" for r in results) else "pass" if results else "unknown"
        return self.check_record("competency", {"status": status, "checks": results, "configured_questions": len(questions),
            "empty_instance_data_is_not_failure": True, "natural_language_questions_not_falsely_marked_executed": True})

    def run_ontology_validation(self): return self.runtime.run_validation()

    def finish_extension_review(self, stop_reason):
        self.state["stop_reason"] = stop_reason
        return {"status": "finish_requested", "final_decision_by": "server_policy"}
