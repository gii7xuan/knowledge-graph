"""Read-only replay of frozen candidates; optional bounded real-model smoke test.

This writes a report only. It never changes candidates, votes, labels or decisions.
Credentials use the same KG_JUDGE_* environment variables as the review CLI.
"""

import argparse
import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from document.hierarchy_index import load_document_index
from evidence.hierarchical_retriever import retrieve_initial
from evidence.verification import verify_evidence_bundle
from review_agent.cli import _configured_validators
from review_agent.contracts import EVIDENCE_BUNDLE_VERSION, canonical_triple, proposal_digest, sha256_json
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.response_contract import CONTRACT_VERSION, PROMPT_VERSION
from review_agent.service import load_config
from review_agent.validators import judge_independently


def run(args):
    config = load_config(args.config)
    database = Path(config.get("database_path", "runs/v3/review.sqlite3")).resolve()
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        manifest = json.loads(connection.execute("SELECT manifest_json FROM runs WHERE run_id=?", (args.run_id,)).fetchone()[0])
        rows = connection.execute("""SELECT r.candidate_json FROM candidates c
            JOIN candidate_revisions r ON r.candidate_id=c.candidate_id AND r.revision=c.current_revision
            WHERE c.run_id=? AND EXISTS (SELECT 1 FROM audit_runs a WHERE a.candidate_id=c.candidate_id)
            ORDER BY c.candidate_id LIMIT ?""", (args.run_id, args.sample_size)).fetchall()
        candidates = [json.loads(row[0]) for row in rows]
        baseline = [dict(row) for row in connection.execute("""SELECT c.candidate_id,v.voter_id,v.response_status
            FROM validator_votes v JOIN audit_runs a ON a.audit_id=v.audit_id
            JOIN candidates c ON c.candidate_id=a.candidate_id WHERE c.run_id=?""", (args.run_id,))
                    if row["candidate_id"] in {c["candidate_id"] for c in candidates}]
    document = load_document_index(manifest["document_path"])
    guard = OntologyGuard(load_snapshot(manifest["ontology_path"], manifest.get("ontology_hash")))
    validators = _configured_validators(config) if args.live else []
    if args.live and not validators:
        raise ValueError("no configured validators; set KG_JUDGE_* before --live")
    versions = {"prompt_version": PROMPT_VERSION, "response_contract_version": CONTRACT_VERSION,
                "evidence_bundle_version": EVIDENCE_BUNDLE_VERSION,
                "committee_version": (config.get("consensus") or {}).get("version", "committee_v1")}
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "run_id": args.run_id,
              "database_unchanged": True, "mode": "smoke_only", "live": args.live,
              "review_versions": versions, "model_configuration": [
                  {k: v[k] for k in ("id", "model_id", "model_family")} for v in validators],
              "budget": config.get("budget", {}), "committee": config.get("consensus", {}),
              "baseline_final_statuses": dict(Counter(x["response_status"] for x in baseline)),
              "comparison_controls": "same candidate set; prompt/contract/evidence selection intentionally changed",
              "candidates": []}
    started = time.monotonic()
    for candidate in candidates:
        bundle = verify_evidence_bundle(retrieve_initial(candidate, document), document, candidate)
        seed = {**versions, "candidate_id": candidate["candidate_id"], "revision": candidate["revision"],
                "canonical_triple": canonical_triple(candidate), "document_version": candidate["document_version"],
                "ontology_version": candidate["ontology_version"], "evidence_bundle_hash": bundle["evidence_hash"], "round_id": 1}
        proposal = {**seed, "audit_id": "smoke_" + sha256_json(seed)[:20], "proposal_digest": proposal_digest(seed),
                    "candidate": candidate, "ontology_check": guard.check(candidate), "evidence_bundle": bundle}
        votes = judge_independently(validators, proposal, max_retries=1) if bundle["items"] else []
        report["candidates"].append({"candidate_id": candidate["candidate_id"], "revision": candidate["revision"],
            "evidence_count": len(bundle["items"]), "rejected_anchors": bundle.get("rejected_items", []),
            "evidence_hash": bundle["evidence_hash"], "proposal_digest": proposal["proposal_digest"], "votes": votes})
        print(json.dumps({"candidate": candidate["candidate_id"], "evidence": len(bundle["items"]),
                          "statuses": [v["response_status"] for v in votes]}), flush=True)
    votes = [v for c in report["candidates"] for v in c["votes"]]
    attempts = [a for v in votes for a in v.get("attempts", [])]
    total_references, valid_references = 0, 0
    for item, candidate in zip(report["candidates"], candidates):
        available = {i["evidence_id"] for i in verify_evidence_bundle(retrieve_initial(candidate, document), document, candidate)["items"]}
        for vote in item["votes"]:
            for attempt in vote.get("attempts", []):
                references = list(attempt.get("supporting_evidence_ids") or []) + list(attempt.get("counter_evidence_ids") or [])
                references += [eid for check in attempt.get("checks") or [] for eid in check.get("evidence_ids") or []]
                total_references += len(references)
                valid_references += sum(eid in available for eid in references)
    def ratio(a, b):
        return a / b if b else None
    report["metrics"] = {"candidates": len(candidates), "candidates_with_verified_evidence": sum(c["evidence_count"] > 0 for c in report["candidates"]),
        "model_tasks": len(votes), "actual_requests": len(attempts),
        "interface_success_rate": ratio(sum(a["response_status"] not in {"api_error", "timeout"} for a in attempts), len(attempts)),
        "first_contract_pass_rate": ratio(sum(v["attempts"][0]["response_status"] in {"ok", "invalid_reference"} for v in votes), len(votes)),
        "first_contract_and_reference_pass_rate": ratio(sum(v["attempts"][0]["response_status"] == "ok" for v in votes), len(votes)),
        "final_response_pass_rate": ratio(sum(v["response_status"] == "ok" for v in votes), len(votes)),
        "reference_occurrences": total_references, "valid_reference_occurrences": valid_references,
        "reference_validity_rate": ratio(valid_references, total_references),
        "final_statuses": dict(Counter(v["response_status"] for v in votes)),
        "input_tokens": sum(a.get("input_tokens") or 0 for a in attempts),
        "output_tokens": sum(a.get("output_tokens") or 0 for a in attempts),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "fact_accuracy": None, "uncertain_fraction": ratio(sum(v.get("verdict") == "uncertain" for v in votes), len(votes)),
        "auto_accept_precision": None, "manual_review_fraction": None,
        "evaluation_note": "No human truth labels; no routing/automatic admission in this smoke test."}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/review_agent.json")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sample-size", type=int, default=3)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", default="runs/review_validation/review_v2_smoke.json")
    run(parser.parse_args())
