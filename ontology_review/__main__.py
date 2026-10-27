"""Local CLI for real extension tasks; default configuration remains shadow."""
import argparse
import json
from pathlib import Path
from uuid import uuid4
from review_agent.repository import ReviewRepository
from review_agent.service import ReviewService, load_config
from .service import OntologyReviewService, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list", "show", "review", "preview", "publish", "rebase", "reject", "revise"])
    parser.add_argument("--config")
    parser.add_argument("--database")
    parser.add_argument("--extension-id")
    parser.add_argument("--audit-id")
    parser.add_argument("--request", help="JSON file with exact expected versions/approval/reason")
    args = parser.parse_args()
    config = load_config(args.config)
    database = Path(args.database or config.get("database_path", "runs/v3/review.sqlite3"))
    if not database.is_absolute(): database = ROOT / database
    repository = ReviewRepository(database)
    repository.initialize()
    service = OntologyReviewService(ReviewService(repository, config=config))
    request = json.loads(Path(args.request).read_text(encoding="utf-8")) if args.request else {}
    if args.command == "list": result = service.list()
    elif not args.extension_id: parser.error("--extension-id is required")
    elif args.command == "show": result = service.detail(args.extension_id)
    elif args.command == "review":
        if not (config.get("ontology_extension") or {}).get("enabled"): parser.error("ontology agent is disabled in configuration")
        result = service.execute({"extension_id": args.extension_id, "audit_id": args.audit_id or "ontology_audit_" + uuid4().hex})
    elif args.command == "preview": result = service.publisher.preview(args.extension_id)
    elif args.command == "publish": result = service.publish(args.extension_id, request)
    elif args.command == "rebase": result = service.publisher.rebase(args.extension_id, request.get("expected_row_version"), request.get("expected_base_release_id"))
    else: result = service.decide(args.extension_id, {**request, "action": args.command})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
