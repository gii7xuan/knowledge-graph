"""Retrieval over published, human-confirmed review cases."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .repository import ReviewRepository
from .memory_scope import normalize_scope, scope_allows, scope_hash


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
        scope=None,
    ) -> Dict[str, Any]:
        scope=normalize_scope(scope)
        if any(type(v) is not int or v<0 or v>50 for v in (max_positive,max_negative)): raise ValueError('invalid memory limit')
        snapshot_id=self.current_version() if snapshot_id is None else snapshot_id
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
        positive: List[Dict[str, Any]] = []
        negative: List[Dict[str, Any]] = []
        with self.repository.connect() as connection:
            rows = connection.execute(
                f"SELECT case_id, memory_version, case_json FROM memory_cases WHERE {' AND '.join(clauses)} "
                "ORDER BY memory_version DESC,case_id", values)
            # Filter before limiting, so recent disallowed cases cannot hide an
            # older permitted example. Nothing outside the scope is returned.
            for row in rows:
                if len(positive)>=max_positive and len(negative)>=max_negative: break
                case = json.loads(row["case_json"])
                candidate=case.get('candidate') or {}
                if not scope_allows(scope,candidate) or (exclude_candidate_id and candidate.get('candidate_id')==exclude_candidate_id): continue
                case.update({"case_id": row["case_id"], "memory_version": row["memory_version"]})
                label = (case.get("labels") or {}).get("valid_for_kg")
                if type(label) is int and label==1 and len(positive)<max_positive: positive.append(case)
                elif type(label) is int and label==0 and len(negative)<max_negative: negative.append(case)
        return {"snapshot_id": snapshot_id, 'scope':scope,'scope_hash':scope_hash(scope),
                "positive": positive[:max_positive], "negative": negative[:max_negative]}
