"""Offline phase-C smoke and v3->v4 migration/restore rehearsal on copies only."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from review_agent.cli import _document_index
from review_agent.llm_clients import MockLLMClient
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.repository import ReviewRepository
from review_agent.response_contract import CHECK_NAMES, CONTRACT_VERSION, PROMPT_VERSION


def fingerprint(connection):
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    digest = hashlib.sha256()
    counts = {}
    for table in tables:
        escaped = table.replace('"', '""')
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{escaped}")')]
        rows = sorted(json.dumps(row, ensure_ascii=False, default=str) for row in connection.execute(f'SELECT * FROM "{escaped}"'))
        digest.update(json.dumps([table, columns, rows], ensure_ascii=False).encode("utf-8"))
        counts[table] = len(rows)
    return {"logical_sha256": digest.hexdigest(), "row_counts": counts}


def backup(source, destination):
    with closing(sqlite3.connect(destination)) as target:
        source.backup(target)


def migration_rehearsal(source_path, output):
    copied, restored = output / "migration_copy.sqlite3", output / "restored_v3.sqlite3"
    with closing(sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        before = fingerprint(source)
        backup(source, copied)
    repository = ReviewRepository(copied)
    repository.initialize()
    repository.initialize()
    with repository.connect() as migrated:
        after = fingerprint(migrated)
        integrity = migrated.execute("PRAGMA integrity_check").fetchone()[0]
        columns = {r[1] for r in migrated.execute("PRAGMA table_info(candidates)")}
    snapshots = list((output / "migration_backups").glob("*.sqlite3"))
    if len(snapshots) != 1:
        raise AssertionError("expected one pre-v4 backup; use a v3 source database for this rehearsal")
    with closing(sqlite3.connect(snapshots[0])) as prior:
        backup(prior, restored)
    with closing(sqlite3.connect(restored)) as rollback:
        rollback_state = fingerprint(rollback)
        rollback_integrity = rollback.execute("PRAGMA integrity_check").fetchone()[0]
    with closing(sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True)) as original:
        original_after = fingerprint(original)
    preserved = all(after["row_counts"].get(t) == count for t, count in before["row_counts"].items() if t != "schema_migrations")
    assert integrity == rollback_integrity == "ok" and preserved
    assert {"workflow_state", "pending_extension_id"} <= columns
    assert before == rollback_state == original_after
    return {"source": str(source_path.resolve()), "source_opened_read_only": True,
            "source_unchanged": before == original_after,
            "before": before, "migrated": after, "existing_rows_preserved": preserved,
            "integrity_check": integrity, "migration_idempotent": True,
            "pre_v4_backup": str(snapshots[0]), "restored_copy": str(restored),
            "rollback_matches_original": rollback_state == before, "original_overwritten": False}


def offline_smoke(root, output):
    fixture = json.loads((root / "tests/fixtures/review_agent/sensor_demo.json").read_text(encoding="utf-8"))
    guard = OntologyGuard(load_snapshot(fixture["ontology"]))
    document = _document_index("fixture_doc", "传感器A可以测量温度。")
    repository = ReviewRepository(output / "smoke.sqlite3")
    repository.initialize()
    repository.create_run({"run_id": "phase_c_offline", "mode": "shadow"})

    def vote(payload):
        eid = payload["allowed_evidence_ids"][0]
        gap = not payload["ontology_check"].get("relation_known")
        return {"verdict": "uncertain" if gap else "valid", "source_support": "supported",
            "schema_status": "unknown" if gap else "pass", "schema_issue": "not_covered" if gap else "none",
            "schema_change_hint": None, "supporting_evidence_ids": [eid], "counter_evidence_ids": [],
            "reason_codes": ["SCHEMA_GAP"] if gap else [],
            "checks": [{"check": name, "status": "unknown" if gap and name == "ontology" else "pass",
                        "evidence_ids": [eid], "note": "MOCK independent contract fixture"} for name in CHECK_NAMES],
            "requested_context": None, "suggested_revision": None}

    def plan(payload):
        def action(name, **args):
            return {"action": name, "arguments": args, "target_issue": "offline fixture", "rationale_summary": "MOCK planning; no real model evaluated"}
        if not payload["ontology_checked"]:
            return action("inspect_ontology", relation_id=payload["candidate"].get("relation_id"), issue="full ontology lookup")
        if not payload["current_validation"]:
            return action("run_validation")
        if not payload["ontology_check"].get("relation_known"):
            return action("request_ontology_extension", change_kind="add_object_property", description="MOCK gap for queue verification",
                          proposed_changes={"label": "调节"}, evidence_ids=payload["selected_evidence_ids"][:1])
        return action("finish_review", stop_reason="offline_finished", unresolved_issues=[], evidence_ids=[])

    config = {"mode": "shadow", "planner": {"enabled": True, "strategy": "llm_react", "model": "mock_planner", "family": "mock_planner"},
              "consensus": {"committee_size": 3, "required_votes": 3, "minimum_distinct_families": 3},
              "budget": {"max_planner_steps": 6, "max_tool_calls": 6, "max_validation_rounds": 2,
                         "max_llm_calls_per_candidate": 16, "max_retries_per_request": 0, "total_timeout_seconds": 60}}
    validators = [{"id": letter, "model_id": "mock_" + letter, "model_family": letter,
                   "client": MockLLMClient(vote, model_id="mock_" + letter, model_family=letter)} for letter in "abc"]
    results = []
    for name in ("supported", "gap"):
        triple = dict(fixture["scenarios"][0]["triple"])
        if name == "gap": triple.update(relation_id=None, surface_relation="调节")
        candidate = {"candidate_id": name, "revision": 1, "run_id": "phase_c_offline", "document_id": "fixture_doc",
            "document_version": document["document_version"], "ontology_version": guard.snapshot.ontology_version,
            "ontology_release_id": "MOCK_release_fixture", "profile_hash": "MOCK_profile_fixture",
            "source_chunk_id": document["chunks"][0]["chunk_id"], "extraction_quote": "传感器A可以测量温度。", "triple": triple}
        repository.add_candidate(candidate)
        runner = ReviewOrchestrator(repository, guard, validators, config=config,
            planner_client=MockLLMClient(plan, model_id="mock_planner", model_family="mock_planner"))
        result = runner.audit_candidate(name, 1, document)
        audit = repository.get_audit(name, result["audit_id"])
        (output / ("audit_" + name + ".json")).write_text(json.dumps({"result": result, "audit": audit}, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(result)
    assert results[0]["source_support_result"] == results[1]["source_support_result"] == "supported"
    assert results[1]["workflow_state"] == "awaiting_ontology_review"
    with repository.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM import_outbox").fetchone()[0] == 0
    task = repository.extension_status(results[1]["pending_extension_id"])
    (output / "pending_extension.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"real_model_calls": 0, "real_neo4j_calls": 0, "ontology_review_executed": False,
            "ontology_release_published": False, "actual_accuracy_evaluated": False,
            "prompt_version": PROMPT_VERSION, "response_contract_version": CONTRACT_VERSION,
            "results": [{k: result[k] for k in ("candidate_id", "decision", "source_support_result", "workflow_state", "selected_round_id", "selected_proposal_digest", "pending_extension_id")} for result in results]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="runs/v3/review.sqlite3")
    parser.add_argument("--output")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = Path(args.output or root / "runs/review_validation" / ("upgrade_v4_" + uuid4().hex))
    output.mkdir(parents=True, exist_ok=False)
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "migration": migration_rehearsal(Path(args.database), output),
              "offline_smoke": offline_smoke(root, output)}
    path = output / "validation_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), "source_unchanged": report["migration"]["source_unchanged"], "rollback_matches_original": report["migration"]["rollback_matches_original"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
