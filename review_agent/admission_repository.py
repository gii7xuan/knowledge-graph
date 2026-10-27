"""Immutable admission evidence and versioned desired graph state in SQLite."""
import json
from uuid import uuid4
from .contracts import sha256_json, canonical_json
from .admission import validate_admission, load_admission_artifacts


class AdmissionRepository:
    def initialize_admission(self, connection):
        first_install = not connection.execute("SELECT 1 FROM sqlite_master WHERE name='graph_sync'").fetchone()
        for statement in (
            "CREATE TABLE IF NOT EXISTS admission_bundles(bundle_id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,revision INTEGER NOT NULL,bundle_hash TEXT NOT NULL,bundle_json TEXT NOT NULL,FOREIGN KEY(candidate_id,revision) REFERENCES candidate_revisions(candidate_id,revision))",
            "CREATE TABLE IF NOT EXISTS admission_authorizations(decision_id TEXT PRIMARY KEY REFERENCES decisions(decision_id),bundle_id TEXT NOT NULL REFERENCES admission_bundles(bundle_id),authorization_json TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS graph_sync(candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id),requested_version INTEGER NOT NULL DEFAULT 0,applied_version INTEGER NOT NULL DEFAULT 0,desired_hash TEXT,applied_hash TEXT,desired_json TEXT)",
            "CREATE TABLE IF NOT EXISTS graph_outbox(event_id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),projection_version INTEGER NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT,UNIQUE(candidate_id,projection_version))",
        ): connection.execute(statement)
        if first_install:
            ids = connection.execute("SELECT candidate_id FROM candidates WHERE import_status IN ('imported','pending') UNION SELECT candidate_id FROM decisions WHERE final_decision IN ('human_accept','auto_accept')").fetchall()
            for row in ids: self.mark_graph_dirty(connection, row[0], "legacy_admission_requires_reconciliation")

    def save_admission_bundle(self, bundle, *, expected_row_version=None, connection=None):
        from .repository import ConflictError
        def write(conn):
            row = conn.execute("SELECT current_revision,row_version FROM candidates WHERE candidate_id=?", (bundle["candidate_id"],)).fetchone()
            if row is None or row["current_revision"] != bundle["revision"] or (expected_row_version is not None and row["row_version"] != expected_row_version):
                raise ConflictError("candidate changed before evidence bundle persistence")
            conn.execute("INSERT OR IGNORE INTO admission_bundles VALUES(?,?,?,?,?)", (bundle["bundle_id"], bundle["candidate_id"], bundle["revision"], bundle["bundle_hash"], canonical_json(bundle)))
        if connection is not None: write(connection)
        else:
            with self.transaction(immediate=True) as conn: write(conn)

    def admission_bundle(self, bundle_id):
        from .repository import UnprocessableEntityError
        with self.connect() as connection:
            row = connection.execute("SELECT bundle_json FROM admission_bundles WHERE bundle_id=?", (bundle_id,)).fetchone()
        if row is None: raise UnprocessableEntityError("unknown evidence bundle ID")
        bundle = json.loads(row[0])
        if sha256_json({k: v for k, v in bundle.items() if k not in {"bundle_id", "bundle_hash"}}) != bundle["bundle_hash"]:
            raise UnprocessableEntityError("evidence bundle content hash mismatch")
        return bundle

    def prepare_human_admission(self, request):
        from .human_evidence import create_human_bundle
        from .repository import ConflictError, UnprocessableEntityError
        candidate = self.get_candidate(request["candidate_id"])
        if candidate["revision"] != request["revision"] or candidate["row_version"] != request["expected_row_version"]:
            raise ConflictError("candidate revision/row_version changed")
        bundle = self.admission_bundle(request["human_bundle_id"]) if request.get("human_bundle_id") else create_human_bundle(self, request)
        if (bundle["kind"] != "human" or bundle["candidate_id"] != candidate["candidate_id"] or bundle["revision"] != candidate["revision"]
                or bundle["input_row_version"] != candidate["row_version"] or bundle["candidate_hash"] != sha256_json(self.get_revision(candidate["candidate_id"], candidate["revision"]))):
            raise UnprocessableEntityError("manual evidence bundle is foreign or stale")
        try: release, document = load_admission_artifacts(self, candidate)
        except (ValueError, KeyError, OSError) as exc: raise UnprocessableEntityError(str(exc)) from exc
        decision = {"candidate_id": candidate["candidate_id"], "revision": candidate["revision"], "decision": "human_accept",
                    "source": "human_confirmed", "human_bundle_id": bundle["bundle_id"]}
        result = validate_admission(candidate, decision, release, document, bundle["evidence_bundle"], bundle["assessments"], "human")
        if not result.allowed: raise UnprocessableEntityError("admission blocked: " + ", ".join(result.reason_codes))
        return {"bundle": bundle, "result": result.to_dict(), "candidate_hash": bundle["candidate_hash"], "input_row_version": candidate["row_version"]}

    def prepare_automatic_admission(self, candidate_id, revision, decision):
        from .proposal_resolver import selected_proposal
        from .consensus import aggregate_votes, Committee
        from .repository import UnprocessableEntityError
        candidate = self.get_revision(candidate_id, revision)
        current = self.get_candidate(candidate_id)
        try:
            proposal, votes = selected_proposal(self, candidate, decision)
            audit = self.get_audit(candidate_id, decision["audit_id"])
            policy = audit["context"].get("admission_policy") or {}
            if proposal.get("admission_policy_hash") != sha256_json(policy): raise ValueError("frozen automatic policy binding missing/mismatched")
            committee = proposal["committee_policy"]
            members = [m["id"] for m in proposal["committee_members"]]
            members.extend("unconfigured_" + str(i + 1) for i in range(committee["committee_size"] - len(members)))
            critical = any(i.get("critical") and i.get("status", "open") != "resolved" for i in audit["context"].get("issue_history", []))
            consensus = aggregate_votes(votes, Committee(members, committee["required_votes"], committee["minimum_distinct_families"]), proposal=proposal, proposal_digest=proposal["proposal_digest"], admission_blocked=critical)
            assessments = []
            for vote in votes:
                for role, key in (("support", "supporting_evidence_ids"), ("counter", "counter_evidence_ids")):
                    for eid in vote.get(key, []):
                        assessments.append({"evidence_id": eid, "role": role, "task_type": "triple_review", "voter_id": vote["voter_id"],
                            "model_id": vote["model_id"], "model_identity": vote.get("model_identity"), "audit_id": proposal["audit_id"],
                            "round_id": proposal["round_id"], "proposal_digest": proposal["proposal_digest"],
                            "source_support": vote.get("source_support"), "schema_status": vote.get("schema_status"),
                            "checks": vote.get("checks", []), "note": "independent frozen proposal assessment"})
            release, document = load_admission_artifacts(self, candidate)
            effective = {**decision, "candidate_id": candidate_id, "revision": revision, "consensus": consensus, "admission_policy": policy}
            if current.get("pending_extension_id"): candidate["workflow_state"] = current["workflow_state"]
            result = validate_admission(candidate, effective, release, document, proposal["evidence_bundle"], assessments, audit["mode"])
            if not result.allowed: raise ValueError(", ".join(result.reason_codes))
        except (ValueError, KeyError, OSError) as exc: raise UnprocessableEntityError("automatic admission blocked: " + str(exc)) from exc
        bundle = {"kind": "automatic", "candidate_id": candidate_id, "revision": revision, "input_row_version": current["row_version"],
            "candidate_hash": sha256_json(self.get_revision(candidate_id, revision)), "evidence_bundle": proposal["evidence_bundle"],
            "assessments": assessments, "proposal_binding": {k: decision.get(k) for k in ("audit_id", "selected_round_id", "selected_proposal_digest", "selected_evidence_hash", "committee_version")},
            "admission_policy": policy}
        bundle["bundle_hash"] = sha256_json(bundle)
        bundle["bundle_id"] = "auto_" + bundle["bundle_hash"]
        return {"bundle": bundle, "result": result.to_dict(), "candidate_hash": bundle["candidate_hash"], "input_row_version": current["row_version"]}

    def record_admission(self, connection, decision_id, prepared):
        from .repository import ConflictError
        bundle = prepared["bundle"]
        row = connection.execute("SELECT row_version,current_revision FROM candidates WHERE candidate_id=?", (bundle["candidate_id"],)).fetchone()
        source = connection.execute("SELECT candidate_json FROM candidate_revisions WHERE candidate_id=? AND revision=?", (bundle["candidate_id"], bundle["revision"])).fetchone()
        if not row or row["row_version"] != prepared["input_row_version"] or row["current_revision"] != bundle["revision"] or sha256_json(json.loads(source[0])) != prepared["candidate_hash"]:
            raise ConflictError("candidate changed after unified admission validation")
        self.save_admission_bundle(bundle, connection=connection)
        authorization = {"version": "admission_authorization_v1", "decision_id": decision_id, "bundle_id": bundle["bundle_id"],
                         "bundle_hash": bundle["bundle_hash"], "candidate_hash": prepared["candidate_hash"], "result": prepared["result"]}
        connection.execute("INSERT INTO admission_authorizations VALUES(?,?,?)", (decision_id, bundle["bundle_id"], canonical_json(authorization)))

    def mark_graph_dirty(self, connection, candidate_id, reason):
        connection.execute("INSERT OR IGNORE INTO graph_sync(candidate_id) VALUES(?)", (candidate_id,))
        connection.execute("UPDATE graph_sync SET requested_version=requested_version+1,desired_hash=NULL WHERE candidate_id=?", (candidate_id,))
        version = connection.execute("SELECT requested_version FROM graph_sync WHERE candidate_id=?", (candidate_id,)).fetchone()[0]
        connection.execute("INSERT OR IGNORE INTO graph_outbox(event_id,candidate_id,projection_version,reason) VALUES(?,?,?,?)", ("graph_" + uuid4().hex, candidate_id, version, reason))
        return version

    def has_graph_history(self, connection, candidate_id):
        return bool(connection.execute("SELECT 1 FROM graph_sync WHERE candidate_id=? UNION SELECT 1 FROM decisions WHERE candidate_id=? AND final_decision IN ('human_accept','auto_accept') LIMIT 1", (candidate_id, candidate_id)).fetchone())

    def current_admitted_contributions(self, candidate_id):
        """Read current authority, revalidate all artifacts, then freeze a fenced state."""
        from .repository import ConflictError
        for _ in range(4):
            candidate = self.get_candidate(candidate_id)
            decision = self.latest_decision(candidate_id)
            result, bundle, desired, reasons = None, None, [], []
            authoritative = decision and decision.get("decision_id") == candidate.get("latest_decision_id") and decision.get("revision") == candidate["revision"]
            if authoritative and decision.get("decision") in {"auto_accept", "human_accept"}:
                try:
                    with self.connect() as connection:
                        row = connection.execute("SELECT authorization_json FROM admission_authorizations WHERE decision_id=?", (decision["decision_id"],)).fetchone()
                    if not row: raise ValueError("legacy acceptance has no verified admission authorization")
                    authorization = json.loads(row[0])
                    bundle = self.admission_bundle(authorization["bundle_id"])
                    if bundle["bundle_hash"] != authorization["bundle_hash"] or sha256_json(self.get_revision(candidate_id, candidate["revision"])) != authorization["candidate_hash"]: raise ValueError("admission authorization binding mismatch")
                    if decision["decision"] == "auto_accept": self.prepare_automatic_admission(candidate_id, candidate["revision"], decision)
                    release, document = load_admission_artifacts(self, candidate)
                    effective = {**decision, "human_bundle_id": bundle["bundle_id"], "admission_policy": bundle.get("admission_policy", {})}
                    result = validate_admission(candidate, effective, release, document, bundle["evidence_bundle"], bundle["assessments"], (self.get_audit(candidate_id, decision["audit_id"])["mode"] if decision["decision"] == "auto_accept" else "human"))
                    reasons = result.reason_codes
                    if result.allowed:
                        desired = [{"candidate": self.get_revision(candidate_id, candidate["revision"]), "decision": effective,
                                    "evidence_bundle": result.evidence_bundle, "assessments": result.assessments,
                                    "admission": result.to_dict(), "bundle_hash": bundle["bundle_hash"]}]
                except (ValueError, KeyError, OSError) as exc: reasons = ["ADMISSION_REVALIDATION_FAILED", str(exc)]
                except Exception as exc:
                    from .repository import RepositoryError
                    if not isinstance(exc, RepositoryError): raise
                    reasons = ["ADMISSION_REVALIDATION_FAILED", str(exc)]
            state = {"projection_schema": "fact_projection_v1", "candidate_id": candidate_id, "revision": candidate["revision"],
                     "decision_id": decision.get("decision_id") if authoritative else None, "desired": desired, "admission_reasons": reasons}
            state_hash = sha256_json(state)
            with self.transaction(immediate=True) as connection:
                current = connection.execute("SELECT current_revision,row_version,latest_decision_id FROM candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
                if current["row_version"] != candidate["row_version"] or current["current_revision"] != candidate["revision"] or current["latest_decision_id"] != candidate["latest_decision_id"]: continue
                connection.execute("INSERT OR IGNORE INTO graph_sync(candidate_id) VALUES(?)", (candidate_id,))
                sync = connection.execute("SELECT * FROM graph_sync WHERE candidate_id=?", (candidate_id,)).fetchone()
                if sync["desired_hash"] is not None and sync["desired_hash"] != state_hash:
                    self.mark_graph_dirty(connection, candidate_id, "desired_authority_or_artifacts_changed")
                elif sync["requested_version"] == 0:
                    self.mark_graph_dirty(connection, candidate_id, "initial_reconciliation")
                version = connection.execute("SELECT requested_version FROM graph_sync WHERE candidate_id=?", (candidate_id,)).fetchone()[0]
                state.update(projection_version=version, state_hash=state_hash)
                connection.execute("UPDATE graph_sync SET desired_hash=?,desired_json=? WHERE candidate_id=?", (state_hash, canonical_json(state), candidate_id))
                return state
        raise ConflictError("candidate kept changing during admission snapshot")

    def acknowledge_graph(self, candidate_id, version, state_hash):
        with self.transaction(immediate=True) as connection:
            connection.execute("UPDATE graph_sync SET applied_version=MAX(applied_version,?),applied_hash=CASE WHEN applied_version<=? THEN ? ELSE applied_hash END WHERE candidate_id=?", (version, version, state_hash, candidate_id))
            connection.execute("UPDATE graph_outbox SET status='completed',attempts=attempts+1,last_error=NULL WHERE candidate_id=? AND projection_version<=?", (candidate_id, version))
            current = connection.execute("SELECT requested_version,desired_hash FROM graph_sync WHERE candidate_id=?", (candidate_id,)).fetchone()
            if current and current["requested_version"] == version and current["desired_hash"] == state_hash:
                connection.execute("UPDATE candidates SET import_status=? WHERE candidate_id=?", ("imported" if json.loads(connection.execute("SELECT desired_json FROM graph_sync WHERE candidate_id=?", (candidate_id,)).fetchone()[0])["desired"] else "retracted", candidate_id))

    def graph_pending_candidates(self, run_id=None, *, scan=False):
        with self.connect() as connection:
            if scan:
                rows = connection.execute("SELECT candidate_id FROM candidates WHERE (? IS NULL OR run_id=?) ORDER BY candidate_id", (run_id, run_id)).fetchall()
            else:
                rows = connection.execute("SELECT DISTINCT c.candidate_id FROM candidates c LEFT JOIN graph_outbox g ON g.candidate_id=c.candidate_id LEFT JOIN import_outbox o ON o.candidate_id=c.candidate_id WHERE (? IS NULL OR c.run_id=?) AND (g.status IN ('pending','failed') OR o.status IN ('pending','failed')) ORDER BY c.candidate_id", (run_id, run_id)).fetchall()
        return [r[0] for r in rows]
