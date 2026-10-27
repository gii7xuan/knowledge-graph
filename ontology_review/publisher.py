"""Operator/policy publication, CAS release head, durable intent and guarded wakeup."""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from ontologyBuild.release_catalog import ReleaseCatalog, ReleaseConflictError
from ontologyBuild.release_service import load_release, canonical_bytes, digest
from ontologyBuild.providers.base import OntologyArtifact
from review_agent.contracts import canonical_json, sha256_json, fact_key_for
from review_agent.repository import ConflictError, utc_now
from .contracts import proposal_digest, versions
from .delta import apply_delta, catalog
from .evidence import load_sources
from .tools import OntologyTools, remap
from .validators import consensus


def current_release_id(root):
    path = Path(root) / "release_catalog.sqlite3"
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        row = connection.execute("SELECT release_id FROM ontology_release_head WHERE singleton=1").fetchone()
    return row[0] if row else None


class OntologyPublisher:
    def __init__(self, repository, release_root, config=None):
        self.repository, self.root, self.config = repository, Path(release_root), config or {}

    def reviewed(self, extension_id):
        task = self.repository.extension_status(extension_id)
        if task["status"] == "rejected" or task.get("review_invalidated_by_human"):
            raise ValueError("human rejected/requested revision; a fresh modeling review is required")
        if not task.get("latest_audit_id"): raise ValueError("ontology extension has no completed modeling review")
        audit = self.repository.ontology_audit(task.get("latest_audit_id") or "")
        result = audit["result"] or {}
        proposal = next((p for p in audit["proposals"] if p["round_id"] == result.get("selected_round_id")
                         and p["proposal_digest"] == result.get("selected_proposal_digest")), None)
        if not proposal or proposal_digest(proposal) != proposal["proposal_digest"]:
            raise ValueError("no exact intact reviewed ontology proposal")
        if proposal.get("versions") != versions(): raise ValueError("ontology prompt/contract changed; fresh independent review required")
        if (task["delta_revision"] != proposal["object_revision"] or task["base_release_id"] != proposal["base_release_id"]
                or task.get("delta", {}).get("delta_hash") != proposal["delta_hash"] or sha256_json(proposal["evidence"]) != proposal["evidence_hash"]):
            raise ConflictError("ontology base/diff/evidence changed after review")
        votes = [v for v in audit["votes"] if v["round_id"] == proposal["round_id"]]
        if consensus(votes, proposal)["result"] != "approve": raise ValueError("independent ontology approval quorum not satisfied")
        base = load_release(self.root / task["base_release_id"])
        ops = [{**o, "target": None if o["kind"] in {"add_class", "add_object_property", "add_data_property"} else o["target"]} for o in proposal["operations"]]
        rebuilt = apply_delta(base, ops, {e["evidence_id"] for e in proposal["evidence"]})
        if rebuilt["merged_schema"] != proposal["merged_schema"] or rebuilt["delta_hash"] != proposal["delta_hash"]:
            raise ValueError("ontology materialized diff mismatch")
        if rebuilt.get('owl_patch')!=proposal.get('source_patch'): raise ValueError('reviewed OWL source patch/compiler binding mismatch')
        return task, proposal, base

    def artifact(self, proposal, base):
        if "ontology.ttl" in base.manifest["artifacts"]:
            from .owl_delta import compile_owl_delta
            compiled=compile_owl_delta(base,proposal['operations'])
            if compiled['project_schema']!=proposal['merged_schema'] or compiled['patch']!=proposal.get('source_patch'):
                raise ValueError('OWL source patch differs from the exact reviewed materialization')
            patch=compiled['patch']
            mapping={**compiled['mapping_report'],'format':'turtle_extension','base_release_id':proposal['base_release_id'],
                     'delta_hash':proposal['delta_hash'],'owl_patch_hash':patch['patch_hash']}
            return OntologyArtifact(project_schema=compiled['project_schema'],application_profile=compiled['application_profile'],
                mapping_report=mapping,iri_id_map=compiled['iri_id_map'],source_ttl=compiled['source_ttl'],owl_patch=patch,
                method_manifest={'method':'ontology_extension_agent_owl_v1','base_release_id':proposal['base_release_id'],
                    'base_source_sha256':patch['base_source_sha256'],'delta_hash':proposal['delta_hash'],
                    'selected_proposal_digest':proposal['proposal_digest'],'source_format':'turtle','rdf_parse_version':'lexical_turtle_v1',
                    'source_sha256':patch['source_sha256'],'source_graph_hash':patch['graph_hash'],'owl_patch_hash':patch['patch_hash'],
                    'compiler_version':patch['compiler_version'],'compiler_hash':patch['compiler_hash']},
                validation_report={'domain_semantic_review':{'status':'independent_model_approval','audit_id':proposal['audit_id'],'proposal_digest':proposal['proposal_digest']},
                    'competency_checks':proposal['check_records']['competency'],'reasoner_checks':proposal['check_records'].get('reasoner',{'status':'not_run'}),
                    'owl_source_integrity':{'status':'pass','source_graph_hash':patch['graph_hash'],'owl_patch_hash':patch['patch_hash'],'owl_consistency_claimed':False}},
                requirements=json.loads((base.path/'requirements.json').read_text(encoding='utf-8')),
                competency_questions=json.loads((base.path/'competency_questions.json').read_text(encoding='utf-8')))
        schema, profile = proposal["merged_schema"], base.application_profile
        mapping = json.loads((base.path / "mapping_report.json").read_text(encoding="utf-8"))
        mapping.update(format="project_json_extension", base_release_id=proposal["base_release_id"], delta_hash=proposal["delta_hash"], errors=[])
        iri_map = json.loads((base.path / "iri_id_map.json").read_text(encoding="utf-8"))
        for identity in set(catalog(schema)) - set(catalog(base.ontology)): iri_map["urn:kg-extension:" + identity] = identity
        return OntologyArtifact(project_schema=schema, application_profile=profile, mapping_report=mapping, iri_id_map=iri_map,
            method_manifest={"method": "ontology_extension_agent_v1", "base_release_id": proposal["base_release_id"],
                "delta_hash": proposal["delta_hash"], "selected_proposal_digest": proposal["proposal_digest"],
                "source_sha256": hashlib.sha256(canonical_bytes(schema) + b"\n").hexdigest(), "source_format": "project_json"},
            validation_report={"domain_semantic_review": {"status": "independent_model_approval", "audit_id": proposal["audit_id"], "proposal_digest": proposal["proposal_digest"]},
                "competency_checks": proposal["check_records"]["competency"], "reasoner_checks": proposal["check_records"].get("reasoner", {"status": "not_run"})},
            source_json=schema, requirements=json.loads((base.path / "requirements.json").read_text(encoding="utf-8")),
            competency_questions=json.loads((base.path / "competency_questions.json").read_text(encoding="utf-8")))

    def preview(self, extension_id):
        task, proposal, base = self.reviewed(extension_id)
        artifact = self.artifact(proposal, base)
        return {"extension_id": extension_id, "expected_row_version": task["row_version"], "base_release_id": task["base_release_id"],
                "proposal_digest": proposal["proposal_digest"], "delta_hash": proposal["delta_hash"], "profile_hash": digest(artifact.application_profile),
                "mapping_report_hash": digest(artifact.mapping_report), "requires_difference_confirmation": bool(artifact.application_profile.get("requires_difference_confirmation")),
                "risk_category": proposal["risk_category"], "operations": proposal["operations"], "impact": proposal["check_records"]["impact"],
                "before": base.ontology, "after": proposal["merged_schema"],
                **({'source_format':'turtle','source_patch':proposal['source_patch']} if proposal.get('source_patch') else {})}

    def rebase(self, extension_id, expected_row_version, expected_base_release_id):
        task = self.repository.extension_status(extension_id)
        current = current_release_id(self.root)
        if current != expected_base_release_id or task["row_version"] != expected_row_version:
            raise ConflictError("release/task changed before explicit rebase")
        if task["status"] in {"published", "publishing"}: raise ConflictError("cannot rebase a published or in-flight publication")
        base = load_release(self.root / current)
        prior = {k: task[k] for k in ("base_release_id", "base_ontology_version", "base_profile_hash")}
        data = {k: v for k, v in task.items() if k not in {"subscriptions", "row_version", "delta", "latest_audit_id", "selected_proposal_digest"}}
        data.update(base_history=[*task.get("base_history", []), prior], base_release_id=current,
                    base_ontology_version=base.manifest["ontology_version"], base_profile_hash=base.manifest["profile_hash"],
                    delta_revision=task["delta_revision"] + 1, status="proposed", ontology_review_executed=False)
        with self.repository.transaction(immediate=True) as connection:
            changed = connection.execute("UPDATE ontology_extension_tasks SET base_release_id=?,base_ontology_version=?,base_profile_hash=?,task_json=?,status='proposed',row_version=row_version+1,gap_key=? WHERE extension_id=? AND row_version=?",
                (current, data["base_ontology_version"], data["base_profile_hash"], canonical_json(data), sha256_json(["rebased", extension_id, current]), extension_id, expected_row_version)).rowcount
            if changed != 1: raise ConflictError("extension changed during rebase")
            intent = connection.execute("SELECT payload_json FROM ontology_publish_outbox WHERE extension_id=?", (extension_id,)).fetchone()
            if intent:
                data.setdefault("previous_publication_intents", []).append(json.loads(intent[0]))
                connection.execute("UPDATE ontology_extension_tasks SET task_json=? WHERE extension_id=?", (canonical_json(data), extension_id))
                connection.execute("DELETE FROM ontology_publish_outbox WHERE extension_id=? AND status='needs_rebase'", (extension_id,))
        return self.repository.extension_status(extension_id)

    def publish(self, extension_id, request, *, automatic=False):
        task, proposal, base = self.reviewed(extension_id)
        if automatic and (self.config.get("mode") != "active" or not (self.config.get("ontology_extension") or {}).get("auto_publish_enabled")
                          or proposal["risk_category"] != "additive" or base.application_profile.get("auto_review_blocked")):
            raise ValueError("automatic ontology publication policy blocks this change")
        if not request.get("approved_by") or not str(request.get("reason") or "").strip(): raise ValueError("operator and modeling review reason required")
        if request.get("proposal_digest") != proposal["proposal_digest"] or request.get("expected_base_release_id") != proposal["base_release_id"]:
            raise ConflictError("publication request must bind exact base and reviewed proposal")
        if proposal["risk_category"] == "high_impact" and not request.get("confirm_high_impact"): raise ValueError("high-impact diff and affected candidates require operator confirmation")
        artifact = self.artifact(proposal, base)
        if automatic and artifact.application_profile.get('auto_review_blocked'): raise ValueError('compiled OWL application profile blocks automatic publication')
        approval = {"status": "approved", "approved_by": request["approved_by"], "reason": request["reason"],
                    "ontology_version": digest(artifact.project_schema), "ontology_audit_id": proposal["audit_id"],
                    "ontology_proposal_digest": proposal["proposal_digest"], "delta_hash": proposal["delta_hash"]}
        if artifact.application_profile.get("requires_difference_confirmation"):
            if not request.get("confirm_profile_differences"): raise ValueError("profile/mapping differences require explicit confirmation")
            approval.update(confirmed_profile_hash=digest(artifact.application_profile), confirmed_mapping_report_hash=digest(artifact.mapping_report))
        with self.repository.connect() as connection:
            existing = connection.execute("SELECT * FROM ontology_publish_outbox WHERE extension_id=?", (extension_id,)).fetchone()
        if existing:
            intent = json.loads(existing["payload_json"])
            if existing["proposal_digest"] != proposal["proposal_digest"]: raise ConflictError("publication intent belongs to a different proposal")
            if existing["status"] == "completed": return json.loads(existing["result_json"])
            if existing["status"] == "needs_rebase": raise ReleaseConflictError("extension must rebase and be reviewed again")
            approval = intent["approval"]
        else:
            if request.get("expected_row_version") != task["row_version"]: raise ConflictError("extension task row version changed")
            evidence, documents, candidates, _ = load_sources(self.repository, task)
            proxy = SimpleNamespace(base=base, repository=self.repository, candidates=candidates, documents=documents,
                state={"task": task, "delta": task["delta"], "evidence": proposal["evidence"], "checks": {}, "validation": None})
            tools = OntologyTools(proxy)
            for name, method in (("structure", tools.validate_schema_structure), ("impact", tools.analyze_schema_impact), ("competency", tools.run_competency_checks)):
                check = method()
                if check["status"] != "pass" or check["record_id"] != proposal["check_records"][name]["record_id"]:
                    raise ConflictError("publication checks or affected candidates changed; investigate and review again")
            intent = {"approval": approval, "expected_base_release_id": task["base_release_id"], "request_row_version": task["row_version"]}
            with self.repository.transaction(immediate=True) as connection:
                changed = connection.execute("UPDATE ontology_extension_tasks SET status='publishing',row_version=row_version+1 WHERE extension_id=? AND row_version=?", (extension_id, task["row_version"])).rowcount
                if changed != 1: raise ConflictError("extension changed before publication intent")
                connection.execute("INSERT INTO ontology_publish_outbox VALUES(?,?,'prepared',?,NULL)", (extension_id, proposal["proposal_digest"], canonical_json(intent)))
        try:
            release = ReleaseCatalog(self.root).publish(artifact, approval=approval, expected_base_release_id=intent["expected_base_release_id"])
        except ReleaseConflictError:
            with self.repository.transaction(immediate=True) as connection:
                connection.execute("UPDATE ontology_publish_outbox SET status='needs_rebase' WHERE extension_id=?", (extension_id,))
                connection.execute("UPDATE ontology_extension_tasks SET status='needs_rebase',row_version=row_version+1 WHERE extension_id=?", (extension_id,))
            raise
        return self._wake(extension_id, release, proposal)

    def _wake(self, extension_id, release, proposal):
        task = self.repository.extension_status(extension_id)
        rebound, skipped, jobs, affected_rebound = [], [], [], []
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            targets=[{**s,"origin":"subscription"} for s in task['subscriptions']]
            subscribed={s['candidate_id'] for s in task['subscriptions']}
            for affected in proposal['check_records']['impact']['affected_candidates']:
                if affected['candidate_id'] not in subscribed:
                    targets.append({**affected,'expected_row_version':affected['row_version'],'status':'active','origin':'historical_impact'})
            for subscription in targets:
                historical = subscription['origin']=='historical_impact'
                if subscription["status"] != "active":
                    skipped.append({"candidate_id": subscription["candidate_id"], "reason": "subscription_" + subscription["status"]})
                    continue
                candidate_id, revision = subscription["candidate_id"], subscription["revision"]
                row = connection.execute("SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
                if (not row or row["current_revision"] != revision or row["row_version"] != subscription["expected_row_version"]
                        or (not historical and (row['human_status']!='pending' or row['pending_extension_id']!=extension_id))
                        or (historical and (row['human_status']!=subscription['human_status'] or row['pending_extension_id']))):
                    skipped.append({"candidate_id": candidate_id, "reason": "candidate_changed"}); continue
                case_id = self.repository._ensure_case(connection, candidate_id)
                try: self.repository._charge_cases(connection, [case_id], {"rebinds": 1}, "ontology_rebind:" + extension_id + ":" + candidate_id, "ontology_extension", extension_id)
                except Exception as exc:
                    from review_agent.agent_state import AgentStopped
                    if not isinstance(exc, AgentStopped): raise
                    if historical:
                        connection.execute("UPDATE candidates SET workflow_state='reaudit_required',human_status='pending',latest_decision_id=NULL,row_version=row_version+1 WHERE candidate_id=?",(candidate_id,))
                        connection.execute('UPDATE decisions SET active=0 WHERE candidate_id=? AND active=1',(candidate_id,))
                        if self.repository.has_graph_history(connection,candidate_id): self.repository.mark_graph_dirty(connection,candidate_id,'schema_impact_budget_exhausted')
                    skipped.append({"candidate_id": candidate_id, "reason": "case_budget_exhausted",'old_admission_withdrawn':historical}); continue
                source = json.loads(connection.execute("SELECT candidate_json FROM candidate_revisions WHERE candidate_id=? AND revision=?", (candidate_id, revision)).fetchone()[0])
                if historical and sha256_json(source)!=subscription['candidate_content_hash']:
                    raise ConflictError('historical candidate content changed during ontology publication')
                updated = remap(source, release.ontology)
                updated.update(release.binding(), revision=revision + 1, supersedes_revision=revision, revision_reason="ontology_rebind",
                               ontology_path=str(release.snapshot_path), rebind_extension_id=extension_id)
                if proposal["risk_category"] == "high_impact": updated["fact_namespace"] = (source.get("fact_namespace") or source.get("domain", "default")) + ":" + release.manifest["ontology_version"]
                if updated.get("contract_version") == "extraction_candidate_v4":
                    from extract_triplets.candidate_contract import refresh_revision
                    refresh_revision(updated)
                    updated = remap(updated, release.ontology)
                    statuses = updated["triple"].get("qualifier_status", {})
                    complete = (updated["triple"].get("relation_id") and updated.get("evidence_items") and updated["triple"].get("qualifiers", {}).get("polarity") in {"positive", "negative"}
                                and len(statuses) == 6 and all(v != "unknown" for v in statuses.values()))
                    if complete: updated["fact_key_state"] = "canonical"
                    from review_agent.admission import canonicalize_revision
                    updated = canonicalize_revision(self.repository, updated)
                updated["fact_key"] = fact_key_for(updated)
                connection.execute("INSERT INTO candidate_revisions(candidate_id,revision,document_id,document_version,ontology_version,source_chunk_id,section_id,fact_key,candidate_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (candidate_id, revision + 1, updated["document_id"], updated["document_version"], updated["ontology_version"], updated.get("source_chunk_id"), updated.get("section_id"), updated["fact_key"], canonical_json(updated), now))
                connection.execute("UPDATE candidates SET current_revision=?,row_version=row_version+1,latest_decision_id=NULL,human_status='pending',workflow_state='reaudit_required',pending_extension_id=NULL,updated_at=? WHERE candidate_id=?", (revision + 1, now, candidate_id))
                connection.execute("UPDATE decisions SET active=0 WHERE candidate_id=? AND active=1", (candidate_id,))
                if self.repository.has_graph_history(connection, candidate_id):
                    self.repository.mark_graph_dirty(connection, candidate_id, "ontology_rebind_withdraws_old_contribution")
                if not historical:
                    connection.execute("UPDATE ontology_extension_subscriptions SET status='rebound' WHERE extension_id=? AND candidate_id=? AND revision=?", (extension_id, candidate_id, revision))
                job_id = "job_" + uuid4().hex
                payload = {"run_id": updated["run_id"], "candidate_ids": [candidate_id], "mode": self.config.get("mode", "shadow"), "force": False, "ontology_rebind": extension_id}
                connection.execute("INSERT INTO jobs(job_id,job_type,status,payload_json,progress_json,created_at,updated_at) VALUES(?,'audit','queued',?,'{}',?,?)", (job_id, canonical_json(payload), now, now))
                jobs.append(job_id); rebound.append({"candidate_id": candidate_id, "revision": revision + 1})
                if historical: affected_rebound.append({'candidate_id':candidate_id,'revision':revision+1,'previous_human_status':subscription['human_status']})
            data = {**task, "status": "published", "published_release_id": release.manifest["release_id"], "publication_proposal_digest": proposal["proposal_digest"]}
            for key in ("subscriptions", "row_version"): data.pop(key, None)
            connection.execute("UPDATE ontology_extension_tasks SET status='published',task_json=?,row_version=row_version+1 WHERE extension_id=?", (canonical_json(data), extension_id))
            result = {**release.binding(), "extension_id": extension_id, "rebound": rebound, "affected_rebound":affected_rebound, "skipped": skipped, "reaudit_jobs": jobs, "old_votes_reused": False}
            connection.execute("UPDATE ontology_publish_outbox SET status='completed',result_json=? WHERE extension_id=?", (canonical_json(result), extension_id))
        return result
