"""SQLite authority for v3 review state.

Connections are short lived, WAL-backed, and never held while an LLM or Neo4j
call is in progress.  All mutable review state is changed using optimistic row
versions and explicit transactions.
"""

from __future__ import annotations

import json
import time
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional
from uuid import uuid4

from .contracts import canonical_json, fact_key_for, sha256_json
from .lifecycle_repository import LifecycleRepository


MIGRATION_VERSION = 4
EDITABLE_TRIPLE_FIELDS = {
    "head", "surface_relation", "relation_id", "relation_label", "tail",
    "head_type", "tail_type", "tail_kind", "datatype", "value", "unit", "qualifiers",
    "language", "qualifier_status",
}


class RepositoryError(RuntimeError):
    pass


class NotFoundError(RepositoryError):
    pass


class ConflictError(RepositoryError):
    pass


class ValidationError(RepositoryError):
    pass


class UnprocessableEntityError(ValidationError):
    """The request is valid, but a required deterministic check failed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return canonical_json(value if value is not None else {})


def _loads(value: Optional[str], default: Any = None) -> Any:
    if value in (None, ""):
        return default
    return json.loads(value)


class _ClosingConnection(sqlite3.Connection):
    """sqlite3's default context manager commits but does not close on Windows."""
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class ReviewRepository(LifecycleRepository):
    def __init__(self, database_path: Any = "runs/v3/review.sqlite3", *, busy_timeout_ms: int = 5000):
        self.database_path = Path(database_path)
        self.busy_timeout_ms = int(busy_timeout_ms)

    def connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self.database_path), timeout=self.busy_timeout_ms / 1000, isolation_level=None,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.ensure_lifecycle_backup()
        with self.transaction(immediate=True) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    migration_version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    source_hash TEXT,
                    ontology_hash TEXT,
                    manifest_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    current_revision INTEGER NOT NULL,
                    row_version INTEGER NOT NULL DEFAULT 1,
                    human_status TEXT NOT NULL DEFAULT 'pending',
                    import_status TEXT NOT NULL DEFAULT 'not_queued',
                    latest_decision_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidate_revisions (
                    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
                    revision INTEGER NOT NULL,
                    document_id TEXT NOT NULL,
                    document_version TEXT NOT NULL,
                    ontology_version TEXT NOT NULL,
                    source_chunk_id TEXT,
                    section_id TEXT,
                    fact_key TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(candidate_id, revision)
                );
                CREATE TABLE IF NOT EXISTS audit_runs (
                    audit_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    feature_schema_version TEXT,
                    features_json TEXT,
                    calibrated_probability REAL,
                    calibration_status TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(candidate_id, revision) REFERENCES candidate_revisions(candidate_id, revision)
                );
                CREATE TABLE IF NOT EXISTS proposals (
                    audit_id TEXT NOT NULL REFERENCES audit_runs(audit_id),
                    round_id INTEGER NOT NULL,
                    proposal_digest TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    consensus_json TEXT,
                    gaps_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(audit_id, round_id),
                    UNIQUE(audit_id, round_id, proposal_digest)
                );
                CREATE TABLE IF NOT EXISTS validator_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    audit_id TEXT NOT NULL,
                    round_id INTEGER NOT NULL,
                    voter_id TEXT NOT NULL,
                    response_status TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    response_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(audit_id, round_id) REFERENCES proposals(audit_id, round_id)
                );
                CREATE TABLE IF NOT EXISTS validator_votes (
                    audit_id TEXT NOT NULL,
                    round_id INTEGER NOT NULL,
                    voter_id TEXT NOT NULL,
                    proposal_digest TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    model_family TEXT NOT NULL,
                    response_status TEXT NOT NULL,
                    verdict TEXT,
                    vote_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(audit_id, round_id, voter_id),
                    FOREIGN KEY(audit_id, round_id) REFERENCES proposals(audit_id, round_id)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    audit_id TEXT,
                    decision_version INTEGER NOT NULL,
                    route TEXT NOT NULL,
                    final_decision TEXT NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    supersedes_decision_id TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    UNIQUE(candidate_id, decision_version),
                    FOREIGN KEY(candidate_id, revision) REFERENCES candidate_revisions(candidate_id, revision)
                );
                CREATE TABLE IF NOT EXISTS review_events (
                    event_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    audit_id TEXT,
                    action TEXT NOT NULL,
                    reason_code TEXT,
                    labels_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    supersedes_event_id TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(candidate_id, revision) REFERENCES candidate_revisions(candidate_id, revision)
                );
                CREATE TABLE IF NOT EXISTS memory_cases (
                    case_id TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL UNIQUE REFERENCES review_events(event_id),
                    memory_version INTEGER NOT NULL,
                    relation_id TEXT,
                    domain TEXT,
                    issue_type TEXT,
                    case_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    progress_json TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS import_outbox (
                    event_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    decision_id TEXT NOT NULL,
                    decision_version INTEGER NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(decision_id) REFERENCES decisions(decision_id)
                );
                CREATE TABLE IF NOT EXISTS migration_items (
                    migration_key TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS planner_steps (
                    audit_id TEXT NOT NULL REFERENCES audit_runs(audit_id),
                    step_no INTEGER NOT NULL,
                    state_version INTEGER NOT NULL,
                    state_hash TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(audit_id, step_no)
                );
                CREATE TABLE IF NOT EXISTS agent_tool_calls (
                    tool_call_id TEXT PRIMARY KEY,
                    audit_id TEXT NOT NULL REFERENCES audit_runs(audit_id),
                    step_no INTEGER NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(audit_id, step_no)
                );
                CREATE TABLE IF NOT EXISTS agent_checkpoints (
                    audit_id TEXT PRIMARY KEY REFERENCES audit_runs(audit_id),
                    phase TEXT NOT NULL,
                    state_hash TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS validation_requests (
                    request_key TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    status TEXT NOT NULL,
                    vote_json TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_candidates_run_status
                    ON candidates(run_id, human_status, current_revision);
                CREATE INDEX IF NOT EXISTS idx_events_candidate ON review_events(candidate_id, revision, active);
                CREATE INDEX IF NOT EXISTS idx_outbox_status ON import_outbox(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
                """
            )
            self.initialize_lifecycle(connection)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(migration_version, applied_at) VALUES (?, ?)",
                (MIGRATION_VERSION, utc_now()),
            )

    init_db = initialize

    def claim_validation_request(self, request_key, owner):
        """Only a known failed request may be retried; unknown/in-flight work stays fenced."""
        with self.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM validation_requests WHERE request_key=?", (request_key,)).fetchone()
            if row is None or row["status"] == "failed":
                connection.execute("""INSERT INTO validation_requests VALUES(?,?,'running',NULL,?)
                    ON CONFLICT(request_key) DO UPDATE SET owner=excluded.owner,status='running',
                    vote_json=NULL,updated_at=excluded.updated_at""", (request_key, owner, utc_now()))
                return {"status": "claimed"}
            return {"status": row["status"], "vote": _loads(row["vote_json"], {})}

    def validation_request(self, request_key):
        with self.connect() as connection:
            row = connection.execute("SELECT status,vote_json FROM validation_requests WHERE request_key=?", (request_key,)).fetchone()
        return None if row is None else {"status": row["status"], "vote": _loads(row["vote_json"], {})}

    def finish_validation_request(self, request_key, owner, vote):
        status = "completed" if vote.get("response_status") == "ok" else (
            "unknown" if vote.get("outcome_unknown") else "failed")
        with self.transaction(immediate=True) as connection:
            changed = connection.execute("""UPDATE validation_requests SET status=?,vote_json=?,updated_at=?
                WHERE request_key=? AND owner=? AND status='running'""",
                (status, _json(vote), utc_now(), request_key, owner)).rowcount
            if changed != 1:
                raise ConflictError("validation request reservation is no longer owned")

    def update_audit_context(self, audit_id, updates):
        with self.transaction(immediate=True) as connection:
            row = connection.execute("SELECT context_json FROM audit_runs WHERE audit_id=?", (audit_id,)).fetchone()
            if row:
                context = {**_loads(row["context_json"], {}), **dict(updates)}
                connection.execute("UPDATE audit_runs SET context_json=? WHERE audit_id=?", (_json(context), audit_id))

    def save_agent_checkpoint(self, audit_id, state, budget, phase="ready"):
        checkpoint = {"state": state, "budget": budget}
        with self.transaction(immediate=True) as connection:
            connection.execute("""INSERT INTO agent_checkpoints VALUES(?,?,?,?,?)
                ON CONFLICT(audit_id) DO UPDATE SET phase=excluded.phase,state_hash=excluded.state_hash,
                checkpoint_json=excluded.checkpoint_json,updated_at=excluded.updated_at""",
                (audit_id, phase, sha256_json(state), _json(checkpoint), utc_now()))

    def get_agent_checkpoint(self, audit_id):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM agent_checkpoints WHERE audit_id=?", (audit_id,)).fetchone()
        return None if row is None else {"phase": row["phase"], **_loads(row["checkpoint_json"], {})}

    def record_planner_step(self, audit_id, step_no, state_version, state_hash, record, checkpoint=None):
        with self.transaction(immediate=True) as connection:
            connection.execute("""INSERT INTO planner_steps VALUES(?,?,?,?,?,?)
                ON CONFLICT(audit_id,step_no) DO UPDATE SET record_json=excluded.record_json
                WHERE json_extract(planner_steps.record_json,'$.status')='started'""",
                (audit_id, step_no, state_version, state_hash, _json(record), utc_now()))
            if checkpoint:
                state, budget, phase = checkpoint
                connection.execute("""INSERT INTO agent_checkpoints VALUES(?,?,?,?,?)
                    ON CONFLICT(audit_id) DO UPDATE SET phase=excluded.phase,state_hash=excluded.state_hash,
                    checkpoint_json=excluded.checkpoint_json,updated_at=excluded.updated_at""",
                    (audit_id, phase, sha256_json(state), _json({"state": state, "budget": budget}), utc_now()))

    def record_agent_tool(self, audit_id, step_no, tool_name, status, record, checkpoint=None):
        call_id = f"{audit_id}:tool:{step_no}"
        with self.transaction(immediate=True) as connection:
            connection.execute("""INSERT INTO agent_tool_calls VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(tool_call_id) DO UPDATE SET status=excluded.status,record_json=excluded.record_json""",
                (call_id, audit_id, step_no, tool_name, status, _json(record), utc_now()))
            if checkpoint:
                state, budget, phase = checkpoint
                connection.execute("""INSERT INTO agent_checkpoints VALUES(?,?,?,?,?)
                    ON CONFLICT(audit_id) DO UPDATE SET phase=excluded.phase,state_hash=excluded.state_hash,
                    checkpoint_json=excluded.checkpoint_json,updated_at=excluded.updated_at""",
                    (audit_id, phase, sha256_json(state), _json({"state": state, "budget": budget}), utc_now()))
        return call_id

    def resumable_agent_audit(self, candidate_id, revision, versions, members=None):
        with self.connect() as connection:
            rows = connection.execute("""SELECT a.audit_id,a.context_json FROM audit_runs a
                JOIN agent_checkpoints s ON s.audit_id=a.audit_id
                WHERE a.candidate_id=? AND a.revision=? AND a.status='interrupted'
                AND s.phase!='finished' ORDER BY a.created_at DESC,a.rowid DESC""", (candidate_id, revision)).fetchall()
        for row in rows:
            context = _loads(row["context_json"], {})
            deadline = context.get("execution_deadline_epoch")
            if deadline is not None and float(deadline) <= time.time():
                continue  # Restart under a fresh audit; the request cache still fences old paid calls.
            if (all(context.get(k) == v for k, v in versions.items())
                    and (members is None or context.get("committee_members") == members)):
                return {"audit_id": row["audit_id"], "context": context}
        return None

    def resume_agent_audit(self, audit_id):
        with self.transaction(immediate=True) as connection:
            connection.execute("UPDATE audit_runs SET status='running',completed_at=NULL WHERE audit_id=? AND status='interrupted'", (audit_id,))

    def create_run(self, manifest: Mapping[str, Any]) -> Dict[str, Any]:
        run_id = str(manifest.get("run_id") or f"run_{uuid4().hex[:16]}")
        payload = dict(manifest)
        payload["run_id"] = run_id
        payload.setdefault("schema_version", "triple_review_v3")
        payload.setdefault("mode", "shadow")
        payload.setdefault("created_at", utc_now())
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO runs(run_id, schema_version, mode, source_hash, ontology_hash,
                   manifest_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, payload["schema_version"], payload["mode"], payload.get("source_hash"),
                 payload.get("ontology_hash") or payload.get("ontology_version"), _json(payload), payload["created_at"]),
            )
        return payload

    register_run = create_run

    def register_v4_run(self, manifest, candidates):
        """Publish one intake pool atomically; no successful partial candidate pool."""
        now = utc_now()
        payload = {"schema_version": "triple_review_v3", "mode": "shadow", "created_at": now, **dict(manifest)}
        with self.transaction(immediate=True) as connection:
            connection.execute("INSERT INTO runs(run_id,schema_version,mode,source_hash,ontology_hash,manifest_json,created_at) VALUES(?,?,?,?,?,?,?)",
                               (payload["run_id"], payload["schema_version"], payload["mode"], payload.get("source_hash"),
                                payload.get("ontology_hash"), _json(payload), payload["created_at"]))
            for candidate in candidates:
                if (candidate.get("contract_version") != "extraction_candidate_v4" or candidate.get("run_id") != payload["run_id"]
                        or candidate.get("source_index_hash") != payload.get("source_index_hash")
                        or candidate.get("ontology_version") != payload.get("ontology_hash")
                        or candidate.get("document_version") != payload.get("source_hash")
                        or candidate.get("fact_key") != fact_key_for(candidate)):
                    raise ValidationError("candidate v4 pool binding mismatch")
                connection.execute("INSERT INTO candidates(candidate_id,run_id,current_revision,row_version,human_status,import_status,created_at,updated_at) "
                                   "VALUES(?,?,1,1,'pending','not_queued',?,?)", (candidate["candidate_id"], payload["run_id"], now, now))
                connection.execute("INSERT INTO candidate_revisions(candidate_id,revision,document_id,document_version,ontology_version,source_chunk_id,section_id,fact_key,candidate_json,created_at) "
                                   "VALUES(?,1,?,?,?,?,?,?,?,?)", (candidate["candidate_id"], candidate["document_id"], candidate["document_version"],
                                   candidate["ontology_version"], candidate.get("source_chunk_id"), candidate.get("section_id"), candidate["fact_key"], _json(candidate), now))
        return payload

    def get_run(self, run_id: str) -> Dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT manifest_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"run not found: {run_id}")
        return _loads(row["manifest_json"], {})

    def list_runs(self, *, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT manifest_json FROM runs ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [_loads(row["manifest_json"], {}) for row in rows]

    def add_candidate(self, candidate: Mapping[str, Any]) -> Dict[str, Any]:
        payload = dict(candidate)
        required = ("candidate_id", "run_id", "document_id", "document_version", "ontology_version")
        missing = [key for key in required if not payload.get(key)]
        if missing:
            raise ValidationError("candidate missing: " + ", ".join(missing))
        revision = int(payload.get("revision") or 1)
        payload["revision"] = revision
        payload.setdefault("schema_version", "triple_review_v3")
        payload.setdefault("fact_key", fact_key_for(payload, str(payload.get("domain") or "default")))
        now = utc_now()
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO candidates(candidate_id, run_id, current_revision, row_version,
                   human_status, import_status, created_at, updated_at)
                   VALUES (?, ?, ?, 1, 'pending', 'not_queued', ?, ?)""",
                (payload["candidate_id"], payload["run_id"], revision, now, now),
            )
            connection.execute(
                """INSERT INTO candidate_revisions(candidate_id, revision, document_id,
                   document_version, ontology_version, source_chunk_id, section_id, fact_key,
                   candidate_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (payload["candidate_id"], revision, payload["document_id"], payload["document_version"],
                 payload["ontology_version"], payload.get("source_chunk_id") or payload.get("source_id"),
                 payload.get("section_id"), payload["fact_key"], _json(payload), now),
            )
        return self.get_candidate(payload["candidate_id"])

    upsert_candidate = add_candidate

    def get_candidate(self, candidate_id: str) -> Dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT c.*, r.candidate_json FROM candidates c JOIN candidate_revisions r
                   ON r.candidate_id=c.candidate_id AND r.revision=c.current_revision
                   WHERE c.candidate_id=?""", (candidate_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"candidate not found: {candidate_id}")
        payload = _loads(row["candidate_json"], {})
        payload.update({
            "revision": row["current_revision"], "row_version": row["row_version"],
            "human_status": row["human_status"], "import_status": row["import_status"],
            "latest_decision_id": row["latest_decision_id"],
            "workflow_state": row["workflow_state"], "pending_extension_id": row["pending_extension_id"],
        })
        return payload

    def get_revision(self, candidate_id: str, revision: Optional[int] = None) -> Dict[str, Any]:
        with self.connect() as connection:
            if revision is None:
                current = connection.execute(
                    "SELECT current_revision FROM candidates WHERE candidate_id=?", (candidate_id,)
                ).fetchone()
                if current is None:
                    raise NotFoundError(f"candidate not found: {candidate_id}")
                revision = int(current["current_revision"])
            row = connection.execute(
                "SELECT candidate_json FROM candidate_revisions WHERE candidate_id=? AND revision=?",
                (candidate_id, int(revision)),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"candidate revision not found: {candidate_id}@{revision}")
        return _loads(row["candidate_json"], {})

    def list_candidates(
        self, run_id: str, *, human_status: Optional[str] = None,
        relation_id: Optional[str] = None, workflow_state: Optional[str] = None, limit: int = 20, offset: int = 0,
    ) -> List[Dict[str, Any]]:
        clauses = ["c.run_id=?"]
        values: List[Any] = [run_id]
        if workflow_state:
            clauses.append("c.workflow_state=?")
            values.append(workflow_state)
        if human_status:
            clauses.append("c.human_status=?")
            values.append(human_status)
        if relation_id:
            clauses.append("COALESCE(json_extract(r.candidate_json, '$.triple.relation_id'), json_extract(r.candidate_json, '$.relation_id'))=?")
            values.append(relation_id)
        values.extend([limit, offset])
        query = f"""SELECT c.candidate_id FROM candidates c
                    JOIN candidate_revisions r ON r.candidate_id=c.candidate_id AND r.revision=c.current_revision
                    WHERE {' AND '.join(clauses)} ORDER BY c.created_at, c.candidate_id LIMIT ? OFFSET ?"""
        with self.connect() as connection:
            ids = [row[0] for row in connection.execute(query, values).fetchall()]
        return [self.get_candidate(value) for value in ids]

    def count_candidates(
        self, run_id: str, *, human_status: Optional[str] = None,
        relation_id: Optional[str] = None, workflow_state: Optional[str] = None,
    ) -> int:
        clauses = ["c.run_id=?"]
        values: List[Any] = [run_id]
        if workflow_state:
            clauses.append("c.workflow_state=?")
            values.append(workflow_state)
        if human_status:
            clauses.append("c.human_status=?")
            values.append(human_status)
        if relation_id:
            clauses.append("COALESCE(json_extract(r.candidate_json, '$.triple.relation_id'), json_extract(r.candidate_json, '$.relation_id'))=?")
            values.append(relation_id)
        with self.connect() as connection:
            row = connection.execute(
                f"""SELECT COUNT(*) FROM candidates c
                    JOIN candidate_revisions r ON r.candidate_id=c.candidate_id AND r.revision=c.current_revision
                    WHERE {' AND '.join(clauses)}""",
                values,
            ).fetchone()
        return int(row[0])

    def revise_candidate(
        self, candidate_id: str, expected_row_version: int, changes: Mapping[str, Any]
    ) -> Dict[str, Any]:
        if set(changes) - {"triple"} or not isinstance(changes.get("triple"), Mapping):
            raise ValidationError("candidate revisions may only change triple fields")
        if set(changes["triple"]) - EDITABLE_TRIPLE_FIELDS:
            raise ValidationError("modifications contain protected or unknown triple fields")
        now = utc_now()
        with self.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"candidate not found: {candidate_id}")
            if int(row["row_version"]) != int(expected_row_version):
                raise ConflictError("candidate row_version changed")
            old_revision = int(row["current_revision"])
            source_row = connection.execute(
                "SELECT candidate_json FROM candidate_revisions WHERE candidate_id=? AND revision=?",
                (candidate_id, old_revision),
            ).fetchone()
            source = _loads(source_row["candidate_json"], {})
            updated = dict(source)
            if isinstance(changes.get("triple"), Mapping):
                updated["triple"] = {**dict(source.get("triple") or {}), **dict(changes["triple"])}
            new_revision = old_revision + 1
            updated.update({"revision": new_revision, "supersedes_revision": old_revision})
            if updated.get("contract_version") == "extraction_candidate_v4":
                from extract_triplets.candidate_contract import refresh_revision
                refresh_revision(updated)
            updated["fact_key"] = fact_key_for(updated)
            connection.execute(
                """INSERT INTO candidate_revisions(candidate_id, revision, document_id,
                   document_version, ontology_version, source_chunk_id, section_id, fact_key,
                   candidate_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (candidate_id, new_revision, updated["document_id"], updated["document_version"],
                 updated["ontology_version"], updated.get("source_chunk_id") or updated.get("source_id"),
                 updated.get("section_id"), updated["fact_key"], _json(updated), now),
            )
            changed = connection.execute(
                """UPDATE candidates SET current_revision=?, row_version=row_version+1,
                   human_status='pending', latest_decision_id=NULL, workflow_state='ready_for_review',pending_extension_id=NULL,updated_at=?
                   WHERE candidate_id=? AND row_version=?""",
                (new_revision, now, candidate_id, expected_row_version),
            ).rowcount
            if changed != 1:
                raise ConflictError("candidate was concurrently changed")
            connection.execute("UPDATE ontology_extension_subscriptions SET status='revoked' WHERE candidate_id=? AND status='active'", (candidate_id,))
        return self.get_candidate(candidate_id)

    def start_audit(self, candidate_id: str, revision: int, context: Mapping[str, Any]) -> str:
        audit_id = str(context.get("audit_id") or f"audit_{uuid4().hex}")
        with self.transaction(immediate=True) as connection:
            exists = connection.execute(
                "SELECT 1 FROM candidate_revisions WHERE candidate_id=? AND revision=?", (candidate_id, revision)
            ).fetchone()
            if exists is None:
                raise NotFoundError(f"candidate revision not found: {candidate_id}@{revision}")
            connection.execute(
                """INSERT INTO audit_runs(audit_id, candidate_id, revision, status, mode,
                   context_json, feature_schema_version, created_at) VALUES (?, ?, ?, 'running', ?, ?, ?, ?)""",
                (audit_id, candidate_id, revision, context.get("mode", "shadow"), _json(context),
                 context.get("feature_schema_version"), utc_now()),
            )
        return audit_id

    def append_round(
        self, audit_id: str, proposal: Mapping[str, Any], votes: Iterable[Mapping[str, Any]],
        consensus: Mapping[str, Any], gaps: Mapping[str, Any],
    ) -> None:
        round_id = int(proposal["round_id"])
        digest = str(proposal["proposal_digest"])
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT OR IGNORE INTO proposals(audit_id, round_id, proposal_digest,
                   evidence_hash, proposal_json, consensus_json, gaps_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (audit_id, round_id, digest, proposal.get("evidence_bundle_hash", ""), _json(proposal),
                 _json(consensus), _json(gaps), utc_now()),
            )
            for vote in votes:
                inserted = connection.execute(
                    """INSERT OR IGNORE INTO validator_votes(audit_id, round_id, voter_id,
                       proposal_digest, model_id, model_family, response_status, verdict,
                       vote_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (audit_id, round_id, vote["voter_id"], digest, vote.get("model_id", ""),
                     vote.get("model_family", ""), vote.get("response_status", "invalid_contract"),
                     vote.get("verdict"), _json(vote), utc_now()),
                ).rowcount
                if not inserted:
                    continue
                for attempt in vote.get("attempts") or []:
                    connection.execute(
                        """INSERT INTO validator_attempts(attempt_id, audit_id, round_id, voter_id,
                           response_status, attempt_no, response_json, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (f"attempt_{uuid4().hex}", audit_id, round_id, vote["voter_id"],
                         attempt.get("response_status", "invalid_contract"),
                         int(attempt.get("attempt_no", 1)), _json(attempt), utc_now()),
                    )

    def update_round_gaps(self, audit_id: str, round_id: int, gaps: Mapping[str, Any]) -> None:
        with self.transaction(immediate=True) as connection:
            connection.execute("UPDATE proposals SET gaps_json=? WHERE audit_id=? AND round_id=?",
                               (_json(gaps), audit_id, round_id))

    def finish_incomplete_audit(self, audit_id: str, status: str, detail: Mapping[str, Any]) -> None:
        """Close a killed/paused audit without creating a semantic decision."""
        if status not in {"paused", "timeout", "interrupted", "failed"}:
            raise ValidationError("invalid incomplete audit status")
        with self.transaction(immediate=True) as connection:
            row = connection.execute("SELECT context_json FROM audit_runs WHERE audit_id=? AND status='running'",
                                     (audit_id,)).fetchone()
            if row:
                context = {**_loads(row["context_json"], {}), "execution_failure": dict(detail)}
                connection.execute("UPDATE audit_runs SET status=?,context_json=?,completed_at=? WHERE audit_id=? AND status='running'",
                                   (status, _json(context), utc_now(), audit_id))

    def record_attempt(self, attempt: Mapping[str, Any]) -> str:
        attempt_id = str(attempt.get("attempt_id") or f"attempt_{uuid4().hex}")
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO validator_attempts(attempt_id, audit_id, round_id, voter_id,
                   response_status, attempt_no, response_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (attempt_id, attempt["audit_id"], int(attempt["round_id"]), attempt["voter_id"],
                 attempt["response_status"], int(attempt.get("attempt_no", 1)), _json(attempt), utc_now()),
            )
        return attempt_id

    def commit_audit_if_current(
        self, candidate_id: str, revision: int, audit_id: str, result: Mapping[str, Any]
    ) -> bool:
        now = utc_now()
        with self.transaction(immediate=True) as connection:
            audit = connection.execute("SELECT context_json,status FROM audit_runs WHERE audit_id=?", (audit_id,)).fetchone()
            context = _loads(audit["context_json"], {}) if audit else {}
            job_id = context.get("job_id")
            if job_id:
                job = connection.execute("SELECT cancel_requested,status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if job and (job["cancel_requested"] or job["status"] == "cancelled"):
                    connection.execute("UPDATE audit_runs SET status='interrupted',completed_at=? WHERE audit_id=? AND status='running'", (now, audit_id))
                    return False
            deadline = context.get("execution_deadline_epoch")
            if deadline and time.time() >= float(deadline):
                context["execution_failure"] = {"reason_code": "HARD_CANDIDATE_TIMEOUT"}
                connection.execute("UPDATE audit_runs SET status='timeout',context_json=?,completed_at=? WHERE audit_id=? AND status='running'",
                                   (_json(context), now, audit_id))
                return False
            if audit and audit["status"] != "running":
                return False
            current = connection.execute(
                "SELECT current_revision, import_status,row_version FROM candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            is_current = current is not None and int(current["current_revision"]) == int(revision)
            if context.get("execution_row_version") is not None and (
                current is None or current["row_version"] != context["execution_row_version"]):
                connection.execute("UPDATE audit_runs SET status='interrupted',completed_at=? WHERE audit_id=? AND status='running'", (now, audit_id))
                return False
            connection.execute(
                """UPDATE audit_runs SET status='completed', features_json=?, calibrated_probability=?,
                   calibration_status=?, completed_at=? WHERE audit_id=?""",
                (_json(result.get("features", {})), result.get("calibrated_probability"),
                 result.get("calibration_status", "unavailable"), now, audit_id),
            )
            if not is_current:
                return False
            old = connection.execute(
                """SELECT decision_id, decision_version, final_decision, revision FROM decisions
                   WHERE candidate_id=? AND active=1 ORDER BY decision_version DESC LIMIT 1""",
                (candidate_id,),
            ).fetchone()
            version = (int(old["decision_version"]) + 1) if old else 1
            decision_id = str(result.get("decision_id") or f"decision_{uuid4().hex}")
            if old:
                connection.execute("UPDATE decisions SET active=0 WHERE decision_id=?", (old["decision_id"],))
            route = str(result.get("decision") or result.get("route") or "manual_review")
            from .response_contract import CONTRACT_VERSION
            if route in {"auto_accept", "auto_reject"} and result.get("review_versions", {}).get("response_contract_version") == CONTRACT_VERSION:
                from .proposal_resolver import selected_proposal
                candidate = self.get_revision(candidate_id, revision)
                selected_proposal(self, candidate, result)
            decision_payload = {
                **dict(result), "decision_id": decision_id, "candidate_id": candidate_id,
                "revision": revision, "audit_id": audit_id, "decision_version": version,
                "decision": route,
            }
            connection.execute(
                """INSERT INTO decisions(decision_id, candidate_id, revision, audit_id,
                   decision_version, route, final_decision, reason_codes_json, decision_json,
                   supersedes_decision_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (decision_id, candidate_id, revision, audit_id, version, route, route,
                 _json(result.get("reason_codes", [])), _json(decision_payload),
                 old["decision_id"] if old else None, now),
            )
            operation = None
            outbox_revision = revision
            waiting = result.get("workflow_state") in {"awaiting_ontology_review", "awaiting_ontology_release", "reaudit_required", "schema_unresolved"}
            if waiting:
                operation = None
            elif route == "auto_accept":
                operation = "upsert"
            elif (old and old["final_decision"] in {"human_accept", "auto_accept"}) or (
                current and current["import_status"] in {"pending", "imported", "failed"}
            ):
                operation = "retract"
                outbox_revision = int(old["revision"])
            if operation:
                outbox_event_id = f"audit_outbox_{audit_id}"
                outbox_payload = {
                    "candidate_id": candidate_id, "revision": outbox_revision,
                    "decision_id": decision_id, "audit_id": audit_id,
                }
                connection.execute(
                    """INSERT INTO import_outbox(event_id, candidate_id, revision, decision_id,
                       decision_version, operation, status, payload_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                    (outbox_event_id, candidate_id, outbox_revision, decision_id, version,
                     operation, _json(outbox_payload), now, now),
                )
            connection.execute(
                """UPDATE candidates SET latest_decision_id=?, row_version=row_version+1,
                   import_status=?, updated_at=? WHERE candidate_id=?""",
                (decision_id, "pending" if operation else "not_queued", now, candidate_id),
            )
            connection.execute("UPDATE candidates SET workflow_state=?,pending_extension_id=? WHERE candidate_id=?",
                               (result.get("workflow_state", "manual_review"), result.get("pending_extension_id"), candidate_id))
            if waiting and result.get("pending_extension_id"):
                connection.execute("UPDATE ontology_extension_subscriptions SET expected_row_version=? WHERE extension_id=? AND candidate_id=? AND revision=? AND status='active'",
                                   (int(current["row_version"])+1, result["pending_extension_id"], candidate_id, revision))
        return True

    def apply_human_feedback(self, request: Mapping[str, Any]) -> Dict[str, Any]:
        """Atomically record a human event, revision/decision, memory, and outbox item."""
        payload = dict(request)
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise ValidationError("idempotency_key is required")
        request_hash = sha256_json(payload)
        action = str(payload.get("action") or "")
        if action not in {"accept", "reject", "defer", "modify", "accept_corrected"}:
            raise ValidationError("unsupported review action")
        candidate_id = str(payload.get("candidate_id") or "")
        revision = int(payload.get("revision") or 0)
        expected_row_version = int(payload.get("expected_row_version") or 0)
        now = utc_now()
        with self.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT request_hash, event_json FROM review_events WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise ConflictError("idempotency key already used for different content")
                return _loads(existing["event_json"], {})

            candidate = connection.execute(
                "SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if candidate is None:
                raise NotFoundError(f"candidate not found: {candidate_id}")
            if int(candidate["row_version"]) != expected_row_version:
                raise ConflictError("candidate row_version changed")
            if int(candidate["current_revision"]) != revision:
                raise ConflictError("candidate revision is stale")
            displayed_decision = connection.execute(
                "SELECT * FROM decisions WHERE decision_id=? AND active=1",
                (candidate["latest_decision_id"],),
            ).fetchone()
            audit_id = payload.get("audit_id")
            if not audit_id and displayed_decision and int(displayed_decision["revision"]) == revision:
                audit_id = displayed_decision["audit_id"]
            if audit_id:
                if not isinstance(audit_id, str):
                    raise ValidationError("audit_id must be a string")
                audit = connection.execute("SELECT * FROM audit_runs WHERE audit_id=?", (audit_id,)).fetchone()
                if (audit is None or audit["candidate_id"] != candidate_id
                        or int(audit["revision"]) != revision or audit["status"] != "completed"):
                    raise ValidationError("audit_id must identify a completed audit of this candidate revision")
            payload["audit_id"] = audit_id
            revision_row = connection.execute(
                "SELECT * FROM candidate_revisions WHERE candidate_id=? AND revision=?",
                (candidate_id, revision),
            ).fetchone()
            source = _loads(revision_row["candidate_json"], {})
            event_id = str(payload.get("event_id") or f"review_event_{uuid4().hex}")
            labels = dict(payload.get("labels") or {})
            evidence_ids = list(payload.get("evidence_ids") or [])
            new_revision = revision
            new_candidate = None

            if action == "modify":
                changes = payload.get("modifications")
                if not isinstance(changes, Mapping) or not changes:
                    raise ValidationError("modify requires modifications")
                unknown_change_keys = set(changes) - ({"triple"} | EDITABLE_TRIPLE_FIELDS)
                if unknown_change_keys:
                    raise ValidationError("modifications contain protected or unknown fields")
                if isinstance(changes.get("triple"), Mapping) and (
                    set(changes["triple"]) - EDITABLE_TRIPLE_FIELDS
                ):
                    raise ValidationError("modifications contain protected or unknown triple fields")
                if isinstance(changes.get("triple"), Mapping) and set(changes) & EDITABLE_TRIPLE_FIELDS:
                    raise ValidationError("use either nested or flat triple modifications, not both")
                new_candidate = dict(source)
                triple_changes = changes.get("triple") if isinstance(changes.get("triple"), Mapping) else {
                    key: changes[key] for key in EDITABLE_TRIPLE_FIELDS if key in changes
                }
                if triple_changes:
                    base_triple = dict(source.get("triple") or {
                        key: source.get(key) for key in ("head", "relation", "surface_relation", "tail")
                    })
                    new_candidate["triple"] = {**base_triple, **dict(triple_changes)}
                revised_triple = new_candidate.get("triple") or {}
                if not str(revised_triple.get("head") or "").strip() or not str(
                    revised_triple.get("tail") or ""
                ).strip():
                    raise ValidationError("modified triple requires non-empty head and tail")
                if not str(
                    revised_triple.get("relation_id") or revised_triple.get("surface_relation")
                    or revised_triple.get("relation_label") or ""
                ).strip():
                    raise ValidationError("modified triple requires a relation")
                new_revision = revision + 1
                new_candidate.update({"revision": new_revision, "supersedes_revision": revision})
                if new_candidate.get("contract_version") == "extraction_candidate_v4":
                    from extract_triplets.candidate_contract import refresh_revision
                    refresh_revision(new_candidate)
                new_candidate["fact_key"] = fact_key_for(new_candidate)
                connection.execute(
                    """INSERT INTO candidate_revisions(candidate_id, revision, document_id,
                       document_version, ontology_version, source_chunk_id, section_id, fact_key,
                       candidate_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (candidate_id, new_revision, new_candidate["document_id"], new_candidate["document_version"],
                     new_candidate["ontology_version"], new_candidate.get("source_chunk_id") or new_candidate.get("source_id"),
                     new_candidate.get("section_id"), new_candidate["fact_key"], _json(new_candidate), now),
                )

            old_decision = connection.execute(
                "SELECT * FROM decisions WHERE candidate_id=? AND active=1 ORDER BY decision_version DESC LIMIT 1",
                (candidate_id,),
            ).fetchone()
            old_was_accepted = bool(
                old_decision and old_decision["final_decision"] in {"human_accept", "auto_accept"}
            )
            needs_retract = old_was_accepted or candidate["import_status"] in {
                "pending", "imported", "failed",
            }
            decision_version = int(old_decision["decision_version"]) + 1 if old_decision else 1
            decision_id = f"decision_{uuid4().hex}"
            if old_decision:
                connection.execute("UPDATE decisions SET active=0 WHERE decision_id=?", (old_decision["decision_id"],))
            final_decision = {
                "accept": "human_accept", "accept_corrected": "human_accept",
                "reject": "human_reject", "defer": "manual_review", "modify": "manual_review",
            }[action]
            human_status = {
                "accept": "accepted", "accept_corrected": "accepted", "reject": "rejected",
                "defer": "deferred", "modify": "pending",
            }[action]
            reason_codes = [payload.get("reason_code")] if payload.get("reason_code") else []
            decision_payload = {
                "decision_id": decision_id, "candidate_id": candidate_id,
                "revision": new_revision if action == "modify" else revision,
                "decision_version": decision_version, "decision": final_decision,
                "reason_codes": reason_codes, "source": "human_confirmed",
                "review_event_id": event_id,
                "evidence_ids": evidence_ids, "labels": labels,
            }
            connection.execute(
                """INSERT INTO decisions(decision_id, candidate_id, revision, audit_id,
                   decision_version, route, final_decision, reason_codes_json, decision_json,
                   supersedes_decision_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (decision_id, candidate_id, decision_payload["revision"], None if action == "modify" else audit_id,
                 decision_version, final_decision, final_decision, _json(reason_codes),
                 _json(decision_payload), old_decision["decision_id"] if old_decision else None, now),
            )
            event = {
                "event_id": event_id, "candidate_id": candidate_id, "revision": revision,
                "new_revision": new_revision if action == "modify" else None,
                "audit_id": payload.get("audit_id"), "action": action,
                "audit_snapshot_status": "bound" if audit_id else "missing",
                "reason_code": payload.get("reason_code"), "labels": labels,
                "reviewer": payload.get("reviewer", "human"), "label_source": "human_confirmed",
                "evidence_ids": evidence_ids, "comment": payload.get("comment"),
                "active_review_seconds": payload.get("active_review_seconds"),
                "supersedes_event_id": payload.get("supersedes_event_id"),
                "decision_id": decision_id, "created_at": now,
            }
            connection.execute(
                """INSERT INTO review_events(event_id, idempotency_key, request_hash,
                   candidate_id, revision, audit_id, action, reason_code, labels_json,
                   evidence_ids_json, event_json, supersedes_event_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_id, idempotency_key, request_hash, candidate_id, revision, payload.get("audit_id"),
                 action, payload.get("reason_code"), _json(labels), _json(evidence_ids), _json(event),
                 payload.get("supersedes_event_id"), now),
            )
            connection.execute(
                """UPDATE candidates SET current_revision=?, row_version=row_version+1,
                   human_status=?, latest_decision_id=?, import_status=?, updated_at=?
                   WHERE candidate_id=? AND row_version=?""",
                (new_revision, human_status, decision_id,
                 "pending" if action in {"accept", "accept_corrected"} or needs_retract else "not_queued",
                 now, candidate_id, expected_row_version),
            )

            connection.execute("UPDATE ontology_extension_subscriptions SET status='revoked' WHERE candidate_id=? AND status='active'", (candidate_id,))
            connection.execute("UPDATE candidates SET workflow_state=?,pending_extension_id=NULL WHERE candidate_id=?",
                               ("ready_for_review" if action == "modify" else "human_reviewed", candidate_id))
            label = labels.get("valid_for_kg")
            if label in (0, 1) and action in {"accept", "accept_corrected", "reject"}:
                memory_version = int(connection.execute(
                    "SELECT COALESCE(MAX(memory_version), 0) + 1 FROM memory_cases"
                ).fetchone()[0])
                triple = source.get("triple") or source
                case = {"source_event_id": event_id, "candidate": source, "labels": labels,
                        "reason_code": payload.get("reason_code"), "evidence_ids": evidence_ids}
                connection.execute(
                    """INSERT INTO memory_cases(case_id, source_event_id, memory_version,
                       relation_id, domain, issue_type, case_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (f"case_{uuid4().hex}", event_id, memory_version,
                     triple.get("relation_id") or triple.get("relation"), source.get("domain"),
                     payload.get("reason_code"), _json(case), now),
                )

            operation = None
            outbox_payload: Dict[str, Any] = {}
            if action in {"accept", "accept_corrected"}:
                operation = "upsert"
                outbox_payload = {"candidate_id": candidate_id, "revision": revision,
                                  "decision_id": decision_id, "event_id": event_id}
            elif action in {"reject", "defer", "modify"} and needs_retract:
                operation = "retract"
                outbox_payload = {"candidate_id": candidate_id, "revision": revision,
                                  "decision_id": decision_id, "event_id": event_id}
            if operation:
                connection.execute(
                    """INSERT INTO import_outbox(event_id, candidate_id, revision, decision_id,
                       decision_version, operation, status, payload_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                    (event_id, candidate_id, revision, decision_id, decision_version, operation,
                     _json(outbox_payload), now, now),
                )
        return event

    def withdraw_review_event(
        self, event_id: str, *, idempotency_key: str, expected_row_version: int,
        reviewer: str = "human", comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT event_json FROM review_events WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"review event not found: {event_id}")
        old = _loads(row["event_json"], {})
        request = {
            "candidate_id": old["candidate_id"], "revision": old["revision"],
            "expected_row_version": expected_row_version, "idempotency_key": idempotency_key,
            "action": "defer", "reason_code": "REVIEW_WITHDRAWN", "labels": {"valid_for_kg": None},
            "reviewer": reviewer, "comment": comment, "supersedes_event_id": event_id,
        }
        result = self.apply_human_feedback(request)
        with self.transaction(immediate=True) as connection:
            connection.execute("UPDATE review_events SET active=0 WHERE event_id=?", (event_id,))
            connection.execute("UPDATE memory_cases SET active=0 WHERE source_event_id=?", (event_id,))
        return result

    def list_outbox(
        self, *, status: str = "pending", run_id: Optional[str] = None, limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            if run_id:
                rows = connection.execute(
                    """SELECT o.* FROM import_outbox o
                       JOIN candidates c ON c.candidate_id=o.candidate_id
                       WHERE o.status=? AND c.run_id=? ORDER BY o.created_at LIMIT ?""",
                    (status, run_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM import_outbox WHERE status=? ORDER BY created_at LIMIT ?", (status, limit)
                ).fetchall()
        return [{**dict(row), "payload": _loads(row["payload_json"], {})} for row in rows]

    def mark_outbox(self, event_id: str, status: str, error: Optional[str] = None) -> None:
        if status not in {"pending", "running", "completed", "failed", "retracted"}:
            raise ValidationError("invalid outbox status")
        with self.transaction(immediate=True) as connection:
            event = connection.execute(
                """SELECT o.operation, o.decision_id, o.candidate_id, c.latest_decision_id
                   FROM import_outbox o JOIN candidates c ON c.candidate_id=o.candidate_id
                   WHERE o.event_id=?""",
                (event_id,),
            ).fetchone()
            if event is None:
                raise NotFoundError(f"outbox event not found: {event_id}")
            changed = connection.execute(
                """UPDATE import_outbox SET status=?, attempts=attempts+1,
                   last_error=?, updated_at=? WHERE event_id=?""",
                (status, error, utc_now(), event_id),
            ).rowcount
            if changed != 1:
                raise NotFoundError(f"outbox event not found: {event_id}")
            if event["decision_id"] == event["latest_decision_id"]:
                import_status = None
                if status == "completed":
                    import_status = "imported" if event["operation"] == "upsert" else "retracted"
                elif status == "failed":
                    import_status = "failed"
                elif status in {"pending", "running"}:
                    import_status = "pending"
                elif status == "retracted" and event["operation"] == "retract":
                    import_status = "retracted"
                if import_status:
                    connection.execute(
                        "UPDATE candidates SET import_status=?, updated_at=? WHERE candidate_id=?",
                        (import_status, utc_now(), event["candidate_id"]),
                    )

    def latest_decision(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM decisions WHERE candidate_id=? AND active=1 ORDER BY decision_version DESC LIMIT 1",
                (candidate_id,),
            ).fetchone()
        return None if row is None else _loads(row["decision_json"], {})

    def has_healthy_audit(self, candidate_id, revision, versions, members) -> bool:
        """Skip completed semantic reviews; retry unfinished or infrastructure-broken ones."""
        if not members:
            return False
        with self.connect() as connection:
            audit = connection.execute("""SELECT audit_id,context_json FROM audit_runs
                WHERE candidate_id=? AND revision=? AND status='completed'
                ORDER BY created_at DESC,rowid DESC LIMIT 1""", (candidate_id, revision)).fetchone()
            if not audit or any(_loads(audit["context_json"], {}).get(key) != value for key, value in versions.items()):
                return False
            rows = connection.execute("""SELECT vote_json FROM validator_votes WHERE audit_id=?
                AND round_id=(SELECT MAX(round_id) FROM proposals WHERE audit_id=?)""",
                (audit["audit_id"], audit["audit_id"])).fetchall()
            checkpoint = connection.execute("SELECT phase,checkpoint_json FROM agent_checkpoints WHERE audit_id=?",
                                            (audit["audit_id"],)).fetchone()
        votes = {_loads(row["vote_json"], {}).get("voter_id"): _loads(row["vote_json"], {}) for row in rows}
        if not votes and checkpoint and checkpoint["phase"] == "finished":
            context = _loads(audit["context_json"], {})
            state = _loads(checkpoint["checkpoint_json"], {}).get("state", {})
            last = (state.get("observations") or [{}])[-1]
            # An intentional human handoff is completed work, not a failed committee
            # request.  Do not pay the planner again on every batch restart.
            return bool(context.get("executed_strategy") == "llm_react"
                and context.get("committee_members") == members
                and last.get("tool") == "finish_review" and last.get("status") == "finish_requested")
        return all(votes.get(m["id"], {}).get("response_status") == "ok" and
                   votes[m["id"]].get("model_id") == m["model_id"] and
                   votes[m["id"]].get("model_family") == m["model_family"] for m in members)

    def feedback_rows(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT e.event_json, r.candidate_json, a.features_json, a.context_json,
                          a.status AS audit_status, a.feature_schema_version
                   FROM review_events e
                   JOIN candidate_revisions r ON r.candidate_id=e.candidate_id AND r.revision=e.revision
                   LEFT JOIN audit_runs a ON a.audit_id=e.audit_id
                   WHERE e.active=1 ORDER BY e.created_at"""
            ).fetchall()
        result = []
        for row in rows:
            event = _loads(row["event_json"], {})
            label = (event.get("labels") or {}).get("valid_for_kg")
            if label not in (0, 1):
                continue
            result.append({
                "event_id": event["event_id"], "candidate_id": event["candidate_id"],
                "revision": event["revision"], "candidate": _loads(row["candidate_json"], {}),
                "features": _loads(row["features_json"], {}), "label": int(label),
                "labels": event.get("labels", {}), "reason_code": event.get("reason_code"),
                "label_source": "human_confirmed",
                "document_id": _loads(row["candidate_json"], {}).get("document_id"),
                "audit_id": event.get("audit_id"),
                "audit_snapshot_status": "bound" if event.get("audit_id") and row["audit_status"] == "completed" else "missing",
                "feature_schema_version": row["feature_schema_version"],
                "review_versions": {key: value for key, value in _loads(row["context_json"], {}).items()
                    if key in ("prompt_version", "prompt_hash", "response_contract_hash", "committee_policy_hash", "response_contract_version", "evidence_bundle_version", "committee_version",
                        "planner_prompt_hash",
                        "planner_prompt_version", "planner_contract_version", "planner_strategy_version", "planner_model_id",
                        "planner_model_family", "configured_strategy", "executed_strategy")},
            })
        return result

    def previous_revision_audit(self, candidate_id, revision, exclude_audit_id):
        with self.connect() as connection:
            row = connection.execute("SELECT audit_id FROM audit_runs WHERE candidate_id=? AND revision=? AND audit_id<>? AND status IN ('completed','paused','interrupted') ORDER BY created_at DESC,rowid DESC LIMIT 1",
                                     (candidate_id, revision, exclude_audit_id)).fetchone()
        return self.get_audit(candidate_id, row[0]) if row else None

    def get_audit(self, candidate_id: str, audit_id: Optional[str] = None) -> Dict[str, Any]:
        with self.connect() as connection:
            if audit_id:
                audit = connection.execute(
                    "SELECT * FROM audit_runs WHERE audit_id=? AND candidate_id=?",
                    (audit_id, candidate_id),
                ).fetchone()
            else:
                audit = connection.execute(
                    "SELECT * FROM audit_runs WHERE candidate_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                    (candidate_id,),
                ).fetchone()
            if audit is None:
                raise NotFoundError("audit not found")
            proposals = connection.execute(
                "SELECT * FROM proposals WHERE audit_id=? ORDER BY round_id", (audit["audit_id"],)
            ).fetchall()
            votes = connection.execute(
                "SELECT vote_json FROM validator_votes WHERE audit_id=? ORDER BY round_id, voter_id",
                (audit["audit_id"],),
            ).fetchall()
            attempts = connection.execute(
                """SELECT response_json FROM validator_attempts WHERE audit_id=?
                   ORDER BY round_id, voter_id, attempt_no""",
                (audit["audit_id"],),
            ).fetchall()
            planner_steps = connection.execute("SELECT step_no,state_version,state_hash,record_json,created_at FROM planner_steps WHERE audit_id=? ORDER BY step_no", (audit["audit_id"],)).fetchall()
            tool_calls = connection.execute("SELECT tool_call_id,step_no,tool_name,status,record_json,created_at FROM agent_tool_calls WHERE audit_id=? ORDER BY step_no", (audit["audit_id"],)).fetchall()
            checkpoint = connection.execute("SELECT phase,checkpoint_json FROM agent_checkpoints WHERE audit_id=?", (audit["audit_id"],)).fetchone()
            issue_events = connection.execute("SELECT event_json FROM issue_events WHERE audit_id=? ORDER BY rowid", (audit["audit_id"],)).fetchall()
        return {
            "audit_id": audit["audit_id"], "candidate_id": audit["candidate_id"],
            "revision": audit["revision"], "status": audit["status"], "mode": audit["mode"],
            "context": _loads(audit["context_json"], {}), "features": _loads(audit["features_json"], {}),
            "calibrated_probability": audit["calibrated_probability"],
            "calibration_status": audit["calibration_status"],
            "rounds": [{**_loads(row["proposal_json"], {}),
                        "consensus": _loads(row["consensus_json"], {}),
                        "gaps": _loads(row["gaps_json"], {})} for row in proposals],
            "votes": [_loads(row["vote_json"], {}) for row in votes],
            "attempts": [_loads(row["response_json"], {}) for row in attempts],
            "issue_events": [_loads(row["event_json"], {}) for row in issue_events],
            "planner_steps": [{"step_no": r["step_no"], "state_version": r["state_version"],
                               "state_hash": r["state_hash"], "created_at": r["created_at"], **_loads(r["record_json"], {})} for r in planner_steps],
            "tool_calls": [{"tool_call_id": r["tool_call_id"], "step_no": r["step_no"], "tool": r["tool_name"],
                            "created_at": r["created_at"], **_loads(r["record_json"], {}), "status": r["status"]} for r in tool_calls],
            "agent_state": None if checkpoint is None else {"phase": checkpoint["phase"], **_loads(checkpoint["checkpoint_json"], {})},
        }

    def create_job(self, job_type: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        payload = dict(payload)
        if job_type == "audit" and payload.get("candidate_ids"):
            payload["candidate_ids"] = sorted(set(payload["candidate_ids"]))
        job_id = f"job_{uuid4().hex}"
        now = utc_now()
        with self.transaction(immediate=True) as connection:
            existing = connection.execute("""SELECT job_id FROM jobs WHERE job_type='audit'
                AND payload_json=? AND status IN ('queued','running') AND cancel_requested=0
                ORDER BY created_at,rowid LIMIT 1""", (_json(payload),)).fetchone() if job_type == "audit" else None
            if existing:
                job_id = existing["job_id"]
            else:
                connection.execute(
                    """INSERT INTO jobs(job_id, job_type, status, payload_json, progress_json,
                       created_at, updated_at) VALUES (?, ?, 'queued', ?, '{}', ?, ?)""",
                    (job_id, job_type, _json(payload), now, now),
                )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> Dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"job not found: {job_id}")
        return {**dict(row), "payload": _loads(row["payload_json"], {}),
                "progress": _loads(row["progress_json"], {})}

    def request_cancel(self, job_id: str) -> Dict[str, Any]:
        with self.transaction(immediate=True) as connection:
            changed = connection.execute(
                """UPDATE jobs SET cancel_requested=1,
                   status=CASE WHEN status IN ('queued', 'running', 'paused') THEN 'cancelled' ELSE status END,
                   lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE job_id=?""",
                (utc_now(), job_id),
            ).rowcount
            if changed != 1:
                raise NotFoundError(f"job not found: {job_id}")
        return self.get_job(job_id)

    def claim_job(self, owner: str, *, lease_seconds: int = 60) -> Optional[Dict[str, Any]]:
        now = utc_now()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        with self.transaction(immediate=True) as connection:
            row = connection.execute(
                """SELECT job_id FROM jobs WHERE cancel_requested=0 AND
                   (status='queued' OR (status='running' AND lease_expires_at < ?))
                   ORDER BY created_at LIMIT 1""", (now,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE jobs SET status='running', lease_owner=?, lease_expires_at=?, updated_at=? WHERE job_id=?",
                (owner, expires, now, row["job_id"]),
            )
            job_id = row["job_id"]
        return self.get_job(job_id)

    def heartbeat_job(self, job_id: str, progress: Mapping[str, Any], lease_seconds: int = 210) -> None:
        expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.transaction(immediate=True) as connection:
            connection.execute("""UPDATE jobs SET progress_json=?,lease_expires_at=?,updated_at=?
                WHERE job_id=? AND status='running' AND cancel_requested=0""",
                (_json(progress), expires, utc_now(), job_id))

    def complete_job(
        self, job_id: str, progress: Mapping[str, Any], *, failed: bool = False,
        cancelled: bool = False, paused: bool = False,
    ) -> None:
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """UPDATE jobs SET status=?, progress_json=?, lease_owner=NULL,
                   lease_expires_at=NULL, updated_at=? WHERE job_id=?""",
                ("cancelled" if cancelled else "paused" if paused else "failed" if failed else "completed",
                 _json(progress), utc_now(), job_id),
            )

    def mark_migration(self, key: str, source_path: str, source_hash: str, result: Mapping[str, Any]) -> bool:
        with self.transaction(immediate=True) as connection:
            changed = connection.execute(
                """INSERT OR IGNORE INTO migration_items(migration_key, source_path, source_hash,
                   result_json, created_at) VALUES (?, ?, ?, ?, ?)""",
                (key, source_path, source_hash, _json(result), utc_now()),
            ).rowcount
        return changed == 1


Repository = ReviewRepository
