"""Idempotently inventory and migrate legacy JSON candidates into v3 SQLite."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from review_agent.contracts import candidate_id_for, fact_key_for
from review_agent.repository import NotFoundError, ReviewRepository


ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    "triplets/*.json", "rejected_triplets/*.json", "review_logs/*.json",
    "runs/v2/candidates/*.json", "runs/v2/accepted/*.json",
    "runs/v2/review_queue/*.json", "runs/v2/rejected/*.json",
    "evidence/*_evidence.md",
]


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover(root: Path) -> List[Path]:
    result = []
    for pattern in PATTERNS:
        result.extend(root.glob(pattern))
    return sorted(set(result))


def _records(path: Path) -> List[Dict[str, Any]]:
    if path.suffix.lower() != ".json":
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def inventory(root: Path) -> Dict[str, Any]:
    files = []
    total = duplicates = conflicts = missing_evidence = ontology_version_uncertain = 0
    seen = set()
    for path in discover(root):
        rows = _records(path)
        if "review_logs" in path.parts:
            for item in rows:
                annotation = item.get("annotation") or {}
                if item.get("decision") == "defer" and annotation.get("triple_correct") in (0, 1):
                    conflicts += 1
                if item.get("decision") == "reject" and annotation.get("triple_correct") == 1:
                    conflicts += 1
        else:
            for item in rows:
                key = (item.get("head"), item.get("relation"), item.get("tail"), item.get("source_id"))
                duplicates += key in seen
                seen.add(key)
                missing_evidence += not bool(item.get("evidence") or item.get("evidence_items"))
                ontology_version_uncertain += not bool(item.get("ontology_version"))
        total += len(rows)
        files.append({"path": str(path.relative_to(root)).replace("\\", "/"), "hash": _hash(path),
                      "records": len(rows)})
    return {"source_files": files, "records": total, "duplicates": duplicates,
            "legacy_label_conflicts": conflicts, "missing_evidence": missing_evidence,
            "ontology_version_uncertain": ontology_version_uncertain}


def migrate(root: Path, database_path: Path) -> Dict[str, Any]:
    repository = ReviewRepository(database_path)
    repository.initialize()
    report = inventory(root)
    imported = skipped = 0
    for file_info in report["source_files"]:
        relative = file_info["path"]
        path = root / relative
        if "review_logs" in path.parts or path.suffix.lower() != ".json":
            continue
        source_hash = file_info["hash"]
        run_id = "legacy_" + source_hash[:16]
        try:
            repository.get_run(run_id)
        except Exception:
            repository.create_run({
                "run_id": run_id, "schema_version": "triple_review_v3", "mode": "legacy",
                "source_hash": source_hash, "ontology_hash": "legacy_unknown",
                "source_path": relative, "migration_version": 1,
            })
        for index, item in enumerate(_records(path)):
            migration_key = hashlib.sha256(f"{source_hash}:{index}".encode("utf-8")).hexdigest()
            with repository.connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM migration_items WHERE migration_key=?", (migration_key,)
                ).fetchone()
            if exists:
                skipped += 1
                continue
            candidate_id = candidate_id_for(run_id, str(item.get("document_id") or path.stem),
                                            str(item.get("source_id") or "unknown"), index)
            triple = {
                "head": item.get("head"), "surface_relation": item.get("surface_relation") or item.get("relation"),
                "relation_id": item.get("relation_id") or item.get("relation"),
                "relation_label": item.get("relation"), "tail": item.get("tail"),
                "head_type": item.get("head_type"), "tail_type": item.get("tail_type"),
                "tail_kind": item.get("tail_kind", "entity"),
                "qualifiers": item.get("qualifiers") or {"polarity": "positive"},
            }
            candidate = {
                "schema_version": "triple_review_v3", "run_id": run_id,
                "candidate_id": candidate_id, "revision": 1,
                "document_id": str(item.get("document_id") or path.stem),
                "document_version": str(item.get("document_version") or source_hash),
                "ontology_version": str(item.get("ontology_version") or "legacy_unknown"),
                "domain": item.get("domain") or "legacy",
                "fact_namespace": item.get("fact_namespace") or item.get("domain") or "legacy",
                "source_chunk_id": item.get("source_id"), "section_id": item.get("section_id"),
                "triple": triple, "extraction_quote": item.get("evidence") or item.get("evidence_quote"),
                "evidence_items": item.get("evidence_items") or [],
                "evidence_status": "located" if item.get("evidence_items") else "unavailable",
                "legacy_fact_key": item.get("fact_key"),
                "legacy": item, "legacy_accept_unverified": item.get("decision") in {"auto_accept", "human_accept"},
                "migration": {"source_path": relative, "source_hash": source_hash, "record_index": index},
            }
            candidate["fact_key"] = fact_key_for(candidate)
            try:
                repository.get_candidate(candidate_id)
                repository.mark_migration(migration_key, relative, source_hash,
                                          {"candidate_id": candidate_id, "revision": 1})
                skipped += 1
                continue
            except NotFoundError:
                repository.add_candidate(candidate)
            repository.mark_migration(migration_key, relative, source_hash,
                                      {"candidate_id": candidate_id, "revision": 1})
            imported += 1
    report.update({"applied": True, "imported": imported, "skipped": skipped,
                   "database_path": str(database_path)})
    return report


def main():
    parser = argparse.ArgumentParser(description="Migrate legacy review JSON into v3 SQLite.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--database", default=str(ROOT / "runs" / "v3" / "review.sqlite3"))
    args = parser.parse_args()
    root = Path(args.root)
    result = inventory(root) if args.dry_run else migrate(root, Path(args.database))
    result.setdefault("applied", False)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
