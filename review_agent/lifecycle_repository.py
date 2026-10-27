"""Additive candidate workflow, issue events and extension-request subscriptions."""

import json
import sqlite3
from uuid import uuid4

from .contracts import canonical_json, sha256_json

WAITING_STATES = {"awaiting_ontology_review", "awaiting_ontology_release", "reaudit_required", "schema_unresolved"}


class LifecycleRepository:
    def ensure_lifecycle_backup(self):
        if not self.database_path.exists() or not self.database_path.stat().st_size:
            return
        source = sqlite3.connect(self.database_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            columns = {row[1] for row in source.execute("PRAGMA table_info(candidates)")}
            if not columns:
                return
            has_agent = source.execute("SELECT 1 FROM sqlite_master WHERE name='ontology_audits'").fetchone()
            has_admission = source.execute("SELECT 1 FROM sqlite_master WHERE name='graph_sync'").fetchone()
            if "workflow_state" in columns and has_agent and has_admission: return
            folder = self.database_path.parent / "migration_backups"
            folder.mkdir(parents=True, exist_ok=True)
            version = "v6" if has_agent and "workflow_state" in columns else "v5" if "workflow_state" in columns else "v4"
            target = folder / (self.database_path.stem + ".pre_" + version + "." + uuid4().hex + ".sqlite3")
            destination = sqlite3.connect(target)
            try: source.backup(destination)
            finally: destination.close()
        finally:
            source.close()

    def initialize_lifecycle(self, connection):
        if not connection.in_transaction: connection.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(candidates)")}
        for name, ddl in (("workflow_state", "TEXT NOT NULL DEFAULT 'ready_for_review'"),
                          ("pending_extension_id", "TEXT")):
            if name not in columns: connection.execute(f"ALTER TABLE candidates ADD COLUMN {name} {ddl}")
        statements = [
            "CREATE TABLE IF NOT EXISTS issue_events(event_id TEXT PRIMARY KEY,audit_id TEXT NOT NULL REFERENCES audit_runs(audit_id),issue_id TEXT NOT NULL,event_json TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS ontology_extension_tasks(extension_id TEXT PRIMARY KEY,gap_key TEXT NOT NULL UNIQUE,base_release_id TEXT NOT NULL,base_ontology_version TEXT NOT NULL,base_profile_hash TEXT NOT NULL,status TEXT NOT NULL,task_json TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS ontology_extension_subscriptions(extension_id TEXT NOT NULL REFERENCES ontology_extension_tasks(extension_id),candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),revision INTEGER NOT NULL,expected_row_version INTEGER NOT NULL,source_proposal_digest TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',PRIMARY KEY(extension_id,candidate_id,revision))",
        ]
        for statement in statements: connection.execute(statement)

    def record_issue_events(self, audit_id, events):
        if not events: return
        with self.transaction(immediate=True) as connection:
            for event in events:
                connection.execute("INSERT OR IGNORE INTO issue_events(event_id,audit_id,issue_id,event_json) VALUES(?,?,?,?)",
                                   (event["event_id"], audit_id, event["issue_id"], canonical_json(event)))

    def create_extension_request(self, candidate, *, expected_row_version, proposal, change_kind,
                                 description, proposed_changes, evidence_ids, full_lookup):
        from .repository import ConflictError, ValidationError
        base = {key: candidate.get(key) for key in ("ontology_release_id", "ontology_version", "profile_hash")}
        if not all(base.values()): raise ValidationError("extension requires an immutable release/profile binding")
        if full_lookup.get("scope") != "full_frozen_ontology" or full_lookup.get("candidate_revision") != candidate["revision"]:
            raise ValidationError("extension requires a current full-schema lookup")
        if any(full_lookup.get(key) != base[key] for key in ("ontology_version", "ontology_release_id", "profile_hash")):
            raise ValidationError("extension full-schema lookup release/profile mismatch")
        # Aggregate actual missing expressions, not model prose or a fabricated ID.
        triple = candidate["triple"]
        gap_key = sha256_json([base, change_kind, " ".join(str(triple.get("surface_relation") or triple.get("relation_id") or "").split()).casefold(),
                               triple.get("head_type"), triple.get("tail_type")])
        trigger = {"candidate_id": candidate["candidate_id"], "revision": candidate["revision"],
                   "audit_id": proposal["audit_id"], "round_id": proposal["round_id"],
                   "source_proposal_digest": proposal["proposal_digest"], "evidence_hash": proposal["evidence_bundle"]["evidence_hash"],
                   "evidence_ids": list(evidence_ids), "proposed_changes": proposed_changes}
        with self.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM candidates WHERE candidate_id=?", (candidate["candidate_id"],)).fetchone()
            if not row or row["row_version"] != expected_row_version or row["current_revision"] != candidate["revision"] or row["human_status"] != "pending":
                raise ConflictError("candidate changed before ontology subscription")
            if row["pending_extension_id"]:
                raise ConflictError("candidate already has an extension subscription; inspect that task instead")
            stored = connection.execute("SELECT * FROM ontology_extension_tasks WHERE gap_key=?", (gap_key,)).fetchone()
            if stored:
                task = json.loads(stored["task_json"])
                task["status"] = stored["status"]
                if trigger not in task.get("trigger_candidates", []):
                    task.setdefault("trigger_candidates", []).append(trigger)
                    task["evidence_ids"] = sorted(set(task.get("evidence_ids", [])) | set(evidence_ids))
                    connection.execute("UPDATE ontology_extension_tasks SET task_json=?,row_version=row_version+1 WHERE extension_id=?", (canonical_json(task), task["extension_id"]))
            else:
                extension_id = "ext_" + uuid4().hex
                task = {"extension_id": extension_id, "contract_version": "ontology_delta_v1", "delta_revision": 1,
                        "base_release_id": base["ontology_release_id"], "base_ontology_version": base["ontology_version"],
                        "base_profile_hash": base["profile_hash"], "change_kind": change_kind, "description": description,
                        "proposed_changes": proposed_changes, "evidence_ids": list(evidence_ids),
                        "existing_schema_search": full_lookup, "status": "proposed", "auto_publish_enabled": False,
                        "trigger_candidates": [trigger],
                        "risk_category": "high_impact" if change_kind in {"revise_constraint", "revise_definition", "merge_terms"} else "additive_draft",
                        "ontology_review_executed": False}
                connection.execute("INSERT INTO ontology_extension_tasks(extension_id,gap_key,base_release_id,base_ontology_version,base_profile_hash,status,task_json) VALUES(?,?,?,?,?,?,?)", (extension_id, gap_key,
                    base["ontology_release_id"], base["ontology_version"], base["profile_hash"], "proposed", canonical_json(task)))
            extension_id = task["extension_id"]
            case_id = self._ensure_case(connection, candidate["candidate_id"])
            self._charge_cases(connection, [case_id], {"extension_requests": 1}, "extension_subscription:" + case_id + ":" + extension_id,
                               "triple_review", candidate["candidate_id"])
            next_version = int(row["row_version"]) + 1
            connection.execute("INSERT INTO ontology_extension_subscriptions(extension_id,candidate_id,revision,expected_row_version,source_proposal_digest) VALUES(?,?,?,?,?) "
                               "ON CONFLICT(extension_id,candidate_id,revision) DO UPDATE SET expected_row_version=excluded.expected_row_version,source_proposal_digest=excluded.source_proposal_digest,status='active'",
                               (extension_id, candidate["candidate_id"], candidate["revision"], next_version, proposal["proposal_digest"]))
            state = ("schema_unresolved" if task["status"] in {"rejected", "needs_human"} else "reaudit_required" if task["status"] == "published"
                     else "awaiting_ontology_release" if task["status"] == "approved" else "awaiting_ontology_review")
            connection.execute("UPDATE candidates SET workflow_state=?,pending_extension_id=?,row_version=? WHERE candidate_id=?",
                               (state, extension_id, next_version, candidate["candidate_id"]))
            if self.has_graph_history(connection, candidate["candidate_id"]):
                self.mark_graph_dirty(connection, candidate["candidate_id"], "awaiting_ontology_withdraws_old_admission")
        return {"task": task, "workflow_state": state, "row_version": next_version}

    def extension_status(self, extension_id, candidate_id=None):
        from .repository import NotFoundError
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM ontology_extension_tasks WHERE extension_id=?", (extension_id,)).fetchone()
            if row is None: raise NotFoundError("ontology extension task not found")
            subscriptions = connection.execute("SELECT * FROM ontology_extension_subscriptions WHERE extension_id=?", (extension_id,)).fetchall()
        if candidate_id and not any(s["candidate_id"] == candidate_id and s["status"] == "active" for s in subscriptions):
            raise NotFoundError("candidate is not subscribed to this extension")
        return {**json.loads(row["task_json"]), "status": row["status"], "row_version": row["row_version"], "subscriptions": [dict(s) for s in subscriptions]}
