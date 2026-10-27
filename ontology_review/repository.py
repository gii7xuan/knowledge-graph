"""Ontology task persistence shares the review DB, never the triple-vote FK."""
import json
import time
from uuid import uuid4

from review_agent.contracts import canonical_json, sha256_json
from review_agent.agent_state import AgentStopped
from review_agent.case_budget import DEFAULT_CASE_LIMITS


class OntologyReviewRepository:
    def initialize_ontology_review(self, connection):
        columns = {r[1] for r in connection.execute("PRAGMA table_info(ontology_extension_tasks)")}
        if "row_version" not in columns:
            connection.execute("ALTER TABLE ontology_extension_tasks ADD COLUMN row_version INTEGER NOT NULL DEFAULT 1")
        for statement in (
            "CREATE TABLE IF NOT EXISTS review_cases(case_id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL UNIQUE,limits_json TEXT NOT NULL,usage_json TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS case_cost_events(event_id TEXT PRIMARY KEY,task_type TEXT NOT NULL,task_id TEXT NOT NULL,case_ids_json TEXT NOT NULL,cost_json TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS ontology_audits(audit_id TEXT PRIMARY KEY,extension_id TEXT NOT NULL REFERENCES ontology_extension_tasks(extension_id),delta_revision INTEGER NOT NULL,status TEXT NOT NULL,context_json TEXT NOT NULL,checkpoint_json TEXT,result_json TEXT,lease_expires REAL NOT NULL)",
            "CREATE TABLE IF NOT EXISTS ontology_steps(audit_id TEXT NOT NULL REFERENCES ontology_audits(audit_id),step_no INTEGER NOT NULL,role TEXT NOT NULL,record_json TEXT NOT NULL,PRIMARY KEY(audit_id,step_no,role))",
            "CREATE TABLE IF NOT EXISTS ontology_proposals(audit_id TEXT NOT NULL REFERENCES ontology_audits(audit_id),round_id INTEGER NOT NULL,proposal_digest TEXT NOT NULL,proposal_json TEXT NOT NULL,PRIMARY KEY(audit_id,round_id))",
            "CREATE TABLE IF NOT EXISTS ontology_votes(audit_id TEXT NOT NULL,round_id INTEGER NOT NULL,voter_id TEXT NOT NULL,vote_json TEXT NOT NULL,PRIMARY KEY(audit_id,round_id,voter_id),FOREIGN KEY(audit_id,round_id) REFERENCES ontology_proposals(audit_id,round_id))",
            "CREATE TABLE IF NOT EXISTS ontology_publish_outbox(extension_id TEXT PRIMARY KEY,proposal_digest TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL,result_json TEXT)",
        ): connection.execute(statement)

    def _ensure_case(self, connection, candidate_id, limits=None):
        case_id = "case_" + sha256_json(candidate_id)[:32]
        if not connection.execute("SELECT 1 FROM review_cases WHERE case_id=?", (case_id,)).fetchone():
            usage = dict.fromkeys(DEFAULT_CASE_LIMITS, 0)
            votes = connection.execute("SELECT v.response_json FROM validator_attempts v JOIN audit_runs a ON a.audit_id=v.audit_id WHERE a.candidate_id=?", (candidate_id,)).fetchall()
            planner = connection.execute("SELECT p.record_json FROM planner_steps p JOIN audit_runs a ON a.audit_id=p.audit_id WHERE a.candidate_id=?", (candidate_id,)).fetchall()
            attempts = [json.loads(r[0]) for r in votes]
            attempts.extend(a for row in planner for a in json.loads(row[0]).get("attempts", []))
            usage["llm_calls"] = sum(not a.get("cached") for a in attempts)
            checkpoints = connection.execute("SELECT a.audit_id,c.checkpoint_json FROM agent_checkpoints c JOIN audit_runs a ON a.audit_id=c.audit_id WHERE a.candidate_id=?", (candidate_id,)).fetchall()
            for checkpoint in checkpoints:
                saved = json.loads(checkpoint["checkpoint_json"]).get("budget", {}).get("usage", {})
                known = connection.execute("SELECT COUNT(*) FROM validator_attempts WHERE audit_id=?", (checkpoint["audit_id"],)).fetchone()[0]
                known += sum(len(json.loads(row[0]).get("attempts", [])) for row in connection.execute("SELECT record_json FROM planner_steps WHERE audit_id=?", (checkpoint["audit_id"],)).fetchall())
                usage["llm_calls"] += max(0, saved.get("llm_calls", 0) - known)
            usage["tokens"] = sum((a.get("input_tokens") or 0) + (a.get("output_tokens") or 0) for a in attempts if not a.get("cached"))
            subscriptions = connection.execute("SELECT DISTINCT extension_id FROM ontology_extension_subscriptions WHERE candidate_id=?", (candidate_id,)).fetchall()
            usage["extension_requests"] = len(subscriptions)
            usage["rebinds"] = connection.execute("SELECT COUNT(*) FROM candidate_revisions WHERE candidate_id=? AND json_extract(candidate_json,'$.revision_reason')='ontology_rebind'", (candidate_id,)).fetchone()[0]
            connection.execute("INSERT INTO review_cases VALUES(?,?,?,?)", (case_id, candidate_id,
                canonical_json({**DEFAULT_CASE_LIMITS, **(limits or {})}), canonical_json(usage)))
            for subscription in subscriptions:
                event_id = "extension_subscription:" + case_id + ":" + subscription[0]
                connection.execute("INSERT OR IGNORE INTO case_cost_events VALUES(?,?,?,?,?)", (event_id, "historical_extension_request", candidate_id, canonical_json([case_id]), canonical_json({"extension_requests": 1})))
        return case_id

    def ensure_case(self, candidate_id, limits=None):
        with self.transaction(immediate=True) as connection:
            return self._ensure_case(connection, candidate_id, limits)

    def case_status(self, case_id):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM review_cases WHERE case_id=?", (case_id,)).fetchone()
        return {"case_id": case_id, "limits": json.loads(row["limits_json"]), "usage": json.loads(row["usage_json"])}

    def _charge_cases(self, connection, case_ids, cost, event_id, task_type, task_id):
        if connection.execute("SELECT 1 FROM case_cost_events WHERE event_id=?", (event_id,)).fetchone(): return
        updates = []
        for case_id in sorted(set(case_ids)):
            row = connection.execute("SELECT * FROM review_cases WHERE case_id=?", (case_id,)).fetchone()
            limits, usage = json.loads(row["limits_json"]), json.loads(row["usage_json"])
            next_usage = {key: max(0, usage.get(key, 0) + cost.get(key, 0)) for key in DEFAULT_CASE_LIMITS}
            if any(next_usage[key] > limits[key] for key in cost if key != "tokens"):
                raise AgentStopped("case_budget_exhausted")
            updates.append((canonical_json(next_usage), case_id))
        connection.executemany("UPDATE review_cases SET usage_json=? WHERE case_id=?", updates)
        connection.execute("INSERT INTO case_cost_events VALUES(?,?,?,?,?)", (event_id, task_type, task_id, canonical_json(sorted(set(case_ids))), canonical_json(cost)))

    def charge_cases(self, case_ids, cost, event_id, task_type, task_id):
        with self.transaction(immediate=True) as connection:
            self._charge_cases(connection, case_ids, cost, event_id, task_type, task_id)
        if cost.get("tokens") and any(self.case_status(c)["usage"]["tokens"] > self.case_status(c)["limits"]["tokens"] for c in case_ids):
            raise AgentStopped("case_token_budget_exhausted")

    def extension_update(self, extension_id, expected_row_version, *, changes=None, status=None):
        from review_agent.repository import ConflictError
        with self.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM ontology_extension_tasks WHERE extension_id=?", (extension_id,)).fetchone()
            if not row or row["row_version"] != expected_row_version: raise ConflictError("extension task changed")
            task = json.loads(row["task_json"])
            task.update(changes or {})
            next_status = status or row["status"]
            task["status"] = next_status
            connection.execute("UPDATE ontology_extension_tasks SET task_json=?,status=?,row_version=row_version+1 WHERE extension_id=?",
                               (canonical_json(task), next_status, extension_id))
            if next_status in {'rejected','needs_human'}:
                # Modeling rejection does not create a negative source truth label.
                connection.execute("UPDATE candidates SET workflow_state='schema_unresolved' WHERE pending_extension_id=? AND human_status='pending' AND EXISTS (SELECT 1 FROM ontology_extension_subscriptions s WHERE s.extension_id=? AND s.candidate_id=candidates.candidate_id AND s.revision=candidates.current_revision AND s.expected_row_version=candidates.row_version AND s.status='active')",(extension_id,extension_id))
        return self.extension_status(extension_id)

    def extensions(self, status=None, limit=50):
        with self.connect() as connection:
            rows = connection.execute("SELECT extension_id FROM ontology_extension_tasks WHERE (? IS NULL OR status=?) ORDER BY rowid DESC LIMIT ?", (status, status, min(100, max(1, int(limit))))).fetchall()
        return [self.extension_status(r[0]) for r in rows]

    def start_ontology_audit(self, extension_id, context, audit_id=None):
        from review_agent.repository import ConflictError
        audit_id = audit_id or "ontology_audit_" + uuid4().hex
        with self.transaction(immediate=True) as connection:
            existing = connection.execute("SELECT * FROM ontology_audits WHERE audit_id=?", (audit_id,)).fetchone()
            if existing:
                if existing["extension_id"] != extension_id or json.loads(existing["context_json"]) != context:
                    raise ConflictError("ontology checkpoint task/configuration mismatch")
                return audit_id
            active = connection.execute("SELECT 1 FROM ontology_audits WHERE extension_id=? AND status='running' AND lease_expires>?", (extension_id, time.time())).fetchone()
            if active: raise ConflictError("ontology extension already has an active worker")
            task = self.extension_status(extension_id)
            if task["status"] == "published": raise ConflictError("extension already published")
            connection.execute("INSERT INTO ontology_audits VALUES(?,?,?,'running',?,NULL,NULL,?)",
                (audit_id, extension_id, task["delta_revision"], canonical_json(context), time.time() + 360))
        return audit_id

    def ontology_checkpoint(self, audit_id, state, budget, phase):
        with self.transaction(immediate=True) as connection:
            connection.execute("UPDATE ontology_audits SET checkpoint_json=?,lease_expires=? WHERE audit_id=?",
                (canonical_json({"state": state, "budget": budget, "phase": phase}), time.time() + 360, audit_id))

    def ontology_step(self, audit_id, step_no, role, record):
        with self.transaction(immediate=True) as connection:
            connection.execute("INSERT INTO ontology_steps VALUES(?,?,?,?) ON CONFLICT(audit_id,step_no,role) DO UPDATE SET record_json=excluded.record_json",
                               (audit_id, step_no, role, canonical_json(record)))

    def ontology_proposal(self, proposal):
        with self.transaction(immediate=True) as connection:
            connection.execute("INSERT OR IGNORE INTO ontology_proposals VALUES(?,?,?,?)", (proposal["audit_id"], proposal["round_id"], proposal["proposal_digest"], canonical_json(proposal)))
            existing = connection.execute("SELECT proposal_digest FROM ontology_proposals WHERE audit_id=? AND round_id=?", (proposal["audit_id"], proposal["round_id"])).fetchone()
            if existing[0] != proposal["proposal_digest"]: raise ValueError("frozen ontology round is immutable")

    def ontology_vote(self, vote):
        with self.transaction(immediate=True) as connection:
            connection.execute("INSERT OR IGNORE INTO ontology_votes VALUES(?,?,?,?)", (vote["audit_id"], vote["round_id"], vote["voter_id"], canonical_json(vote)))

    def ontology_audit(self, audit_id):
        from review_agent.repository import NotFoundError
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM ontology_audits WHERE audit_id=?", (audit_id,)).fetchone()
            if not row: raise NotFoundError("ontology audit not found")
            steps = connection.execute("SELECT step_no,role,record_json FROM ontology_steps WHERE audit_id=? ORDER BY step_no,role", (audit_id,)).fetchall()
            proposals = connection.execute("SELECT proposal_json FROM ontology_proposals WHERE audit_id=? ORDER BY round_id", (audit_id,)).fetchall()
            votes = connection.execute("SELECT vote_json FROM ontology_votes WHERE audit_id=? ORDER BY round_id,voter_id", (audit_id,)).fetchall()
        return {**{k: row[k] for k in ("audit_id", "extension_id", "delta_revision", "status")},
                "context": json.loads(row["context_json"]), "checkpoint": json.loads(row["checkpoint_json"] or "null"),
                "result": json.loads(row["result_json"] or "null"), "steps": [{"step_no": s[0], "role": s[1], **json.loads(s[2])} for s in steps],
                "proposals": [json.loads(p[0]) for p in proposals], "votes": [json.loads(v[0]) for v in votes]}

    def ontology_counter_history(self, extension_id):
        from .validators import consensus
        evidence = {}
        with self.connect() as connection:
            rows = connection.execute("SELECT p.proposal_json FROM ontology_proposals p JOIN ontology_audits a ON a.audit_id=p.audit_id WHERE a.extension_id=? ORDER BY a.rowid,p.round_id", (extension_id,)).fetchall()
        for row in rows:
            proposal = json.loads(row[0])
            audit = self.ontology_audit(proposal["audit_id"])
            votes = [v for v in audit["votes"] if v["round_id"] == proposal["round_id"]]
            eligible = set(consensus(votes, proposal)["eligible_voter_ids"])
            ids = {eid for v in votes if v["voter_id"] in eligible for eid in v.get("counter_evidence_ids", [])}
            evidence.update({e["evidence_id"]: e for e in proposal["evidence"] if e["evidence_id"] in ids})
        return list(evidence.values())

    def finish_ontology_audit(self, audit_id, result, expected_task_version):
        from review_agent.repository import ConflictError
        audit = self.ontology_audit(audit_id)
        with self.transaction(immediate=True) as connection:
            task = connection.execute("SELECT * FROM ontology_extension_tasks WHERE extension_id=?", (audit["extension_id"],)).fetchone()
            if task["row_version"] != expected_task_version: raise ConflictError("ontology task changed before final decision")
            data = json.loads(task["task_json"])
            data.update(latest_audit_id=audit_id, review_recommendation=result["recommendation"],
                        ontology_review_executed=True, selected_proposal_digest=result.get("selected_proposal_digest"),
                        review_invalidated_by_human=not result.get("human_revision_satisfied", True), unresolved_counter_evidence_ids=result.get("counter_evidence_ids", []))
            selected = next((p for p in audit["proposals"] if p["round_id"] == result.get("selected_round_id")), None)
            if selected: data["review_evidence"] = selected["evidence"]
            connection.execute("UPDATE ontology_extension_tasks SET task_json=?,status=?,row_version=row_version+1 WHERE extension_id=?",
                (canonical_json(data), result["task_status"], audit["extension_id"]))
            connection.execute("UPDATE ontology_audits SET result_json=?,status='completed',lease_expires=0 WHERE audit_id=?", (canonical_json(result), audit_id))
            workflow = "awaiting_ontology_release" if result["task_status"] == "approved" else "schema_unresolved"
            connection.execute("UPDATE candidates SET workflow_state=? WHERE pending_extension_id=? AND human_status='pending' AND EXISTS (SELECT 1 FROM ontology_extension_subscriptions s WHERE s.extension_id=? AND s.candidate_id=candidates.candidate_id AND s.revision=candidates.current_revision AND s.expected_row_version=candidates.row_version AND s.status='active')",
                               (workflow, audit["extension_id"], audit["extension_id"]))
