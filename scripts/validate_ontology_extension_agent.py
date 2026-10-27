"""Save an offline A->ontology review->publish->A re-review demonstration.

Uses the integration fixture and real tools/SQLite/release code. All model
responses are Mock; the production database is read only for migration rehearsal.
"""
import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--production-database", default="runs/v3/review.sqlite3")
    parser.add_argument("--skip-migration", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "tests"))
    from test_ontology_extension_agent import OntologyExtensionTests, action
    from ontology_review.publisher import OntologyPublisher
    from review_agent.orchestrator import ReviewOrchestrator
    from review_agent.ontology_guard import OntologyGuard, load_snapshot
    from review_agent.llm_clients import MockLLMClient
    from scripts.validate_review_upgrade_v4 import migration_rehearsal
    from review_agent.repository import MIGRATION_VERSION

    output = Path(args.output or root / "runs/review_validation" / ("ontology_extension_" + uuid4().hex)).resolve()
    if output.exists(): raise ValueError("validation output must be a new directory")
    fixture = OntologyExtensionTests()
    fixture.output_folder = output
    fixture.setUp()  # Intentionally retain this isolated fixture as the validation artifact.
    reviewed = fixture.review()
    publisher = OntologyPublisher(fixture.repo, fixture.root, fixture.config)
    preview = publisher.preview(fixture.extension_id)
    publication = publisher.publish(fixture.extension_id, fixture.publication_request())
    def plan(payload):
        if not payload["ontology_checked"]: return action("inspect_ontology", relation_id=payload["candidate"]["relation_id"], issue="new immutable release")
        if not payload["current_validation"]: return action("run_validation")
        return action("finish_review", stop_reason="new-release independent review completed", unresolved_issues=[], evidence_ids=[])
    result = ReviewOrchestrator(fixture.repo, OntologyGuard(load_snapshot(fixture.root / publication["ontology_release_id"] / "ontology_snapshot.json")),
        fixture.source_voters, config=fixture.config, planner_client=MockLLMClient(plan)).audit_candidate(fixture.candidate["candidate_id"], 2, fixture.index)
    with fixture.repo.connect() as connection:
        ontology_jobs = connection.execute("SELECT job_id FROM jobs WHERE job_type='ontology_extension'").fetchall()
    for row in ontology_jobs: fixture.repo.complete_job(row[0], reviewed)
    for job_id in publication["reaudit_jobs"]: fixture.repo.complete_job(job_id, result)
    source_audit = fixture.repo.get_audit(fixture.candidate["candidate_id"], fixture.source_result["audit_id"])
    after_audit = fixture.repo.get_audit(fixture.candidate["candidate_id"], result["audit_id"])
    for name, data in (("source_audit", source_audit), ("ontology_audit", fixture.audit), ("publication_preview", preview),
                       ("publication_result", publication), ("new_release_triple_audit", after_audit)):
        (output / (name + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    with fixture.repo.connect() as connection:
        outbox = connection.execute("SELECT COUNT(*) FROM import_outbox").fetchone()[0]
    report = {"migration_version": MIGRATION_VERSION, "real_model_calls": 0, "real_neo4j_calls": 0,
        "accuracy_evaluated": False, "reasoner_status": fixture.audit["proposals"][0]["check_records"]["reasoner"]["status"],
        "ontology_recommendation": reviewed["recommendation"], "publication_was_explicit_fixture_operator_action": True,
        "original_base_unchanged": fixture.base.ontology == load_snapshot(fixture.base.snapshot_path).ontology,
        "candidate_revision_before": 1, "candidate_revision_after": 2, "old_votes_reused": False,
        "old_triple_vote_release": source_audit["rounds"][0]["ontology_release_id"],
        "new_triple_vote_release": after_audit["rounds"][0]["ontology_release_id"],
        "new_triple_source_support": result["source_support_result"], "new_triple_admission_consensus": result["admission_consensus"],
        "new_triple_decision": result["decision"], "import_outbox_count": outbox,
        "shared_case_accounts": reviewed["budget"]["case_accounts"], "database": str(fixture.repo.database_path),
        "production_database_written": False, "production_release_published": False}
    assert reviewed["recommendation"] == "approve" and result["source_support_result"] == "supported" and outbox == 0
    assert report["old_triple_vote_release"] != report["new_triple_vote_release"]
    if not args.skip_migration:
        rehearsal = output / "migration_rehearsal"
        rehearsal.mkdir()
        report["migration"] = migration_rehearsal(Path(args.production_database), rehearsal)
    (output / "validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(output / "validation_report.json"), "ontology_review": reviewed["recommendation"],
                      "new_revision": 2, "new_triple_decision": result["decision"], "production_database_written": False}, ensure_ascii=False))


if __name__ == "__main__": main()
