"""Isolated planner behaviour samples, never writes the production review DB.

--live uses the configured real planner, with an explicitly MOCK committee.
Evidence/context/ontology/memory tools execute real functions against fixtures.
This is not a factual-accuracy or real-committee-availability evaluation.
"""

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from review_agent.cli import _document_index
from review_agent.llm_clients import MockLLMClient
from review_agent.model_settings import create_planner_client
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.repository import ReviewRepository
from review_agent.response_contract import CHECK_NAMES
from review_agent.service import load_config


def mock_vote(payload):
    eid = payload["allowed_evidence_ids"][0]
    conditional = any("仅在" in e["quote"] for e in payload["evidence"])
    return {"verdict": "uncertain" if conditional else "valid", "source_support": "insufficient" if conditional else "supported",
        "schema_status": "pass", "schema_issue": "none", "schema_change_hint": None,
        "supporting_evidence_ids": [] if conditional else [eid], "counter_evidence_ids": [],
        "reason_codes": ["CONDITION_MISSING"] if conditional else [],
        "checks": [{"check": name, "status": "unknown" if conditional and name == "qualifiers" else "pass",
                    "evidence_ids": [eid], "note": "MOCK committee fixture observation"} for name in CHECK_NAMES],
        "requested_context": {"scope": "current_chunk", "query": "仅在 条件X", "limit": 3} if conditional else None,
        "suggested_revision": None}


def mock_plan(payload):
    def emit(name, **args):
        return {"action": name, "arguments": args, "target_issue": "fixture", "rationale_summary": "Mock planning; not evidence of real autonomy"}
    if not payload["ontology_checked"]:
        return emit("inspect_ontology", relation_id="R_measures", issue="核查本体")
    if not payload["current_validation"]:
        return emit("run_validation")
    return emit("finish_review", stop_reason="fixture_finished", unresolved_issues=[], evidence_ids=[])


def run(args):
    root = Path(__file__).resolve().parents[1]
    output = Path(args.output or root / "runs/review_validation" / (
        "planner_agent_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:6]))
    output.mkdir(parents=True, exist_ok=False)
    repository = ReviewRepository(output / "fixture_review.sqlite3")
    repository.initialize()
    config = copy.deepcopy(load_config(args.config))
    config["planner"].update(enabled=True, strategy="llm_react", fallback="manual")
    config["mode"] = "shadow"
    config["budget"].update(max_planner_steps=6, total_timeout_seconds=180, max_retries_per_request=0)
    fixture = json.loads((root / "tests/fixtures/review_agent/sensor_demo.json").read_text(encoding="utf-8"))
    guard = OntologyGuard(load_snapshot(fixture["ontology"]))
    planner = create_planner_client(config) if args.live else MockLLMClient(mock_plan)
    if planner is None:
        raise ValueError("real planner is not configured")
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "live_planner": args.live,
        "planner_model": planner.model_id, "committee": "MOCK_FIXTURE_NOT_REAL_MODELS",
        "production_database_used": False, "quality_or_improvement_claim": False, "samples": []}
    for name, text in [("clear_support", "传感器A可以测量温度。"),
                       ("condition_omitted", "仅在条件X下，传感器A可以测量温度。不满足条件X时不能测量。")]:
        print(json.dumps({"case": name, "status": "starting", "live_planner": args.live}), flush=True)
        document = _document_index(name, text)
        repository.create_run({"run_id": name, "mode": "shadow"})
        candidate = {"candidate_id": name, "run_id": name, "revision": 1, "document_id": name,
            "document_version": document["document_version"], "ontology_version": guard.snapshot.ontology_version,
            "source_chunk_id": document["chunks"][0]["chunk_id"], "triple": fixture["scenarios"][0]["triple"],
            "extraction_quote": text.split("。")[0] + "。"}
        repository.add_candidate(candidate)
        voters = [{"id": slot["id"], "model_id": f"mock-{index}", "model_family": f"mock-family-{index}",
            "client": MockLLMClient(mock_vote)} for index, slot in enumerate(config["validators"])]
        result = ReviewOrchestrator(repository, guard, voters, config=config, planner_client=planner).audit_candidate(name, 1, document)
        audit = repository.get_audit(name, result["audit_id"])
        actual_planner_requests = sum(len(s.get("attempts", [])) for s in audit["planner_steps"])
        sample = {"case": name, "audit_id": result["audit_id"], "result": result,
            "actual_planner_requests": actual_planner_requests if args.live else 0,
            "planner_steps": audit["planner_steps"], "tool_calls": audit["tool_calls"],
            "state": audit["agent_state"]}
        report["samples"].append(sample)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"case": name, "executed_strategy": result.get("executed_strategy"),
            "actions": [s.get("action", {}).get("action") for s in audit["planner_steps"]],
            "stop_reason": result.get("stop_reason"), "budget": result.get("budget"),
            "report": str(output / "report.json")}, ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/review_agent.json")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output")
    run(parser.parse_args())
