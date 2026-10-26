"""Transactional ontology release head and append-only publication events.

This catalog contains ontology release metadata, never candidate facts. SQLite
BEGIN IMMEDIATE provides cross-process compare-and-swap for concurrent publishers.
"""

import json
import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .release_service import artifact_files, load_release, publish_release


class ReleaseConflictError(ValueError):
    pass


class ReleaseCatalog:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.database = self.root / "release_catalog.sqlite3"

    def connect(self):
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS ontology_release_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id TEXT NOT NULL UNIQUE,
                base_release_id TEXT,
                event_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ontology_release_head (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                release_id TEXT NOT NULL REFERENCES ontology_release_events(release_id),
                row_version INTEGER NOT NULL
            );
        """)
        return connection

    def current(self):
        if not self.database.exists():
            return None
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT release_id FROM ontology_release_head WHERE singleton=1").fetchone()
        return load_release(self.root / row["release_id"]) if row else None

    def history(self):
        if not self.database.exists():
            return []
        with closing(self.connect()) as connection:
            rows = connection.execute("SELECT event_json FROM ontology_release_events ORDER BY sequence").fetchall()
        return [json.loads(row[0]) for row in rows]

    def publish(self, artifact, *, approval, expected_base_release_id=None):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM ontology_release_head WHERE singleton=1").fetchone()
            current_id = row["release_id"] if row else None
            bound_approval = {**approval, "base_release_id": expected_base_release_id}
            if current_id != expected_base_release_id:
                if current_id:
                    current = load_release(self.root / current_id)
                    requested_hashes = {name: hashlib.sha256(data).hexdigest()
                                        for name, data in artifact_files(artifact, bound_approval).items()}
                    if requested_hashes == current.manifest["artifacts"]:
                        connection.execute("ROLLBACK")
                        return current  # Retry of the exact same already-committed action.
                raise ReleaseConflictError(f"base release changed: expected {expected_base_release_id}, current {current_id}; rebase and re-review")
            if expected_base_release_id:
                load_release(self.root / expected_base_release_id)
            release = publish_release(artifact, self.root, approval=bound_approval)
            event = {**release.binding(), "base_release_id": expected_base_release_id,
                     "event_type": "ontology_release_published",
                     "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "row_version": (row["row_version"] + 1) if row else 1,
                     "impact_status": "initial_baseline" if not row else "reaudit_required",
                     "candidate_rebind_applied": False}
            connection.execute("INSERT INTO ontology_release_events(release_id,base_release_id,event_json) VALUES(?,?,?)",
                               (release.manifest["release_id"], expected_base_release_id, json.dumps(event, ensure_ascii=False)))
            connection.execute("INSERT INTO ontology_release_head(singleton,release_id,row_version) VALUES(1,?,?) "
                               "ON CONFLICT(singleton) DO UPDATE SET release_id=excluded.release_id,row_version=excluded.row_version",
                               (release.manifest["release_id"], event["row_version"]))
            connection.execute("COMMIT")
            return release
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()


def select_release(path=None, *, root=None):
    default_root = Path(__file__).resolve().parents[1] / "runs" / "ontology_releases"
    if path and str(path) != "current":
        return load_release(path)
    release = ReleaseCatalog(root or default_root).current()
    if release is None:
        raise ValueError("no published ontology release; publish a reviewed baseline first")
    return release
