"""CLI and compatibility entry point for the unified admission reconciler."""
import argparse
import json
import os
from pathlib import Path
from review_agent.repository import ReviewRepository
from .fact_projection import Neo4jImporter
from .reconciler import authoritative_item, apply_outbox, dry_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--all-runs", action="store_true")
    parser.add_argument("--scan", action="store_true", help="Reconcile even when an outbox wakeup was lost")
    parser.add_argument("--database", default=str(Path(__file__).resolve().parents[1] / "runs/v3/review.sqlite3"))
    parser.add_argument("--neo4j-database", default="neo4j")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.run_id and not args.all_runs: parser.error("choose --run-id or --all-runs explicitly")
    repo = ReviewRepository(args.database)
    repo.initialize()
    if args.dry_run: result = dry_run(repo, run_id=args.run_id, scan=args.scan)
    else:
        importer = Neo4jImporter(os.environ.get("NEO4J_URI", "bolt://localhost:7687"), os.environ.get("NEO4J_USER", "neo4j"),
                                 os.environ.get("NEO4J_PASSWORD", ""), database=args.neo4j_database, repository=repo)
        try: result = apply_outbox(repo, importer, run_id=args.run_id, scan=args.scan)
        finally: importer.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
