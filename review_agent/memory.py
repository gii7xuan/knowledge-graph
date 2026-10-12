"""Retrieval over published, human-confirmed review cases."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .repository import ReviewRepository


class ReviewMemory:
    def __init__(self, repository: ReviewRepository):
        self.repository = repository

    def current_version(self) -> int:
        with self.repository.connect() as connection:
            return int(connection.execute(
                "SELECT COALESCE(MAX(memory_version), 0) FROM memory_cases WHERE active=1"
            ).fetchone()[0])

    def lookup(
        self, *, relation_id: Optional[str] = None, issue_type: Optional[str] = None,
        domain: Optional[str] = None, snapshot_id: Optional[int] = None,
        exclude_candidate_id: Optional[str] = None, max_positive: int = 3, max_negative: int = 3,
    ) -> Dict[str, Any]:
        clauses = ["active=1"]
        values: List[Any] = []
        if snapshot_id is not None:
            clauses.append("memory_version <= ?")
            values.append(int(snapshot_id))
        if relation_id:
            clauses.append("relation_id=?")
            values.append(relation_id)
        if issue_type:
            clauses.append("issue_type=?")
            values.append(issue_type)
        if domain:
            clauses.append("domain=?")
            values.append(domain)
        with self.repository.connect() as connection:
            rows = connection.execute(
                f"SELECT case_id, memory_version, case_json FROM memory_cases WHERE {' AND '.join(clauses)} "
                "ORDER BY memory_version DESC LIMIT 50", values,
            ).fetchall()
        positive: List[Dict[str, Any]] = []
        negative: List[Dict[str, Any]] = []
        for row in rows:
            case = json.loads(row["case_json"])
            if exclude_candidate_id and (case.get("candidate") or {}).get("candidate_id") == exclude_candidate_id:
                continue
            case.update({"case_id": row["case_id"], "memory_version": row["memory_version"]})
            label = (case.get("labels") or {}).get("valid_for_kg")
            target = positive if label == 1 else negative if label == 0 else None
            if target is not None:
                target.append(case)
        return {"snapshot_id": snapshot_id if snapshot_id is not None else self.current_version(),
                "positive": positive[:max_positive], "negative": negative[:max_negative]}
