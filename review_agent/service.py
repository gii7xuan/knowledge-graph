"""Business service shared by HTTP, the standalone page, and CLI."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .feedback import FeedbackService
from .jobs import JobRunner
from .llm_clients import OpenAICompatibleClient
from .memory import ReviewMemory
from .ontology_guard import OntologyGuard, load_snapshot
from .orchestrator import ReviewOrchestrator
from .repository import NotFoundError, ReviewRepository, ValidationError
from .model_settings import validator_settings, configured_planner, create_planner_client


ROOT = Path(__file__).resolve().parents[1]


class ServiceUnavailableError(RuntimeError):
    pass


def load_config(path: Optional[Any] = None) -> Dict[str, Any]:
    config_path = Path(path or os.environ.get("KG_REVIEW_CONFIG", ROOT / "config" / "review_agent.json"))
    if not config_path.exists():
        example = ROOT / "config" / "review_agent.example.json"
        config_path = example if example.exists() else config_path
    if not config_path.exists():
        return {"mode": "shadow", "database_path": "runs/v3/review.sqlite3", "validators": [],
                "routing": {"auto_accept_enabled": False}}
    return json.loads(config_path.read_text(encoding="utf-8"))


class ReviewService:
    def __init__(
        self, repository: ReviewRepository, *, config: Optional[Mapping[str, Any]] = None,
        orchestrator: Any = None, job_runner: Any = None, ontology_guard: Any = None,
    ):
        self.repository = repository
        self.config = dict(config or {})
        self.orchestrator = orchestrator
        self.job_runner = job_runner
        self.feedback = FeedbackService(repository, ontology_guard=ontology_guard)

    def config_status(self) -> Dict[str, Any]:
        from confidence.calibrator import load_calibrator
        from confidence.risk_policy import load_policy

        def resolved_path(value: Any) -> Optional[Path]:
            if not value:
                return None
            path = Path(value)
            return path if path.is_absolute() else ROOT / path

        slots = []
        for item in self.config.get("validators") or []:
            settings = validator_settings(item)
            model, family = settings["model"], settings["family"]
            has_api_key = bool(settings["api_key"])
            has_base_url = bool(settings["base_url"])
            slots.append({"id": item.get("id"),
                          "configured": bool(model and family and has_api_key and has_base_url),
                          "model": model, "family": family, "has_api_key": has_api_key,
                          "has_base_url": has_base_url})
        calibration = self.config.get("calibration") or {}
        model_path = resolved_path(calibration.get("model_path"))
        policy_path = resolved_path(calibration.get("policy_path"))
        calibrator_ready = False
        policy_ready = False
        if model_path and model_path.is_file():
            try:
                calibrator = load_calibrator(str(model_path))
                calibrator_ready = bool(
                    calibrator.ready and calibrator.feature_schema_version == calibration.get(
                        "feature_schema_version", "review_features_v3"
                    )
                )
                from .batch import versions_for
                versions = versions_for(self.config)
                versions["executed_strategy"] = (self.config.get("planner") or {}).get("strategy", "deterministic")
                calibrator_ready = calibrator_ready and all(calibrator.metadata.get(k) == v for k, v in versions.items())
            except Exception:
                calibrator_ready = False
        if policy_path and policy_path.is_file():
            try:
                policy_ready = bool(load_policy(str(policy_path)).ready)
            except Exception:
                policy_ready = False
        consensus = self.config.get("consensus") or {}
        planner = self.config.get("planner") or {}
        planner_connection = configured_planner(self.config)
        configured_strategy = planner.get("strategy", "deterministic")
        planner_ready = bool(planner.get("enabled") and all(planner_connection.get(k)
            for k in ("model", "family", "api_key", "base_url")))
        latest_strategy = None
        try:
            with self.repository.connect() as connection:
                row = connection.execute("SELECT context_json FROM audit_runs ORDER BY created_at DESC,rowid DESC LIMIT 1").fetchone()
                if row:
                    latest_strategy = json.loads(row["context_json"]).get("executed_strategy")
        except Exception:
            pass
        configured_slots = [item for item in slots if item["configured"]]
        distinct_families = len({item["family"] for item in configured_slots})
        distinct_slot_ids = len({item["id"] for item in configured_slots if item.get("id")})
        distinct_models = len({str(item["model"]).strip().casefold() for item in configured_slots})
        committee_size = int(consensus.get("committee_size", 4))
        required_votes = int(consensus.get("required_votes", 3))
        minimum_families = int(consensus.get("minimum_distinct_families", 3))
        committee_ready = (
            committee_size >= 1
            and 1 <= required_votes <= committee_size
            and 1 <= minimum_families <= committee_size
            and len(configured_slots) == committee_size
            and distinct_slot_ids == len(configured_slots)
            and distinct_families >= minimum_families
            and distinct_models == committee_size
        )
        return {
            "mode": self.config.get("mode", "shadow"),
            "planner_strategy": (self.config.get("planner") or {}).get("strategy", "deterministic"),
            "configured_strategy": configured_strategy, "last_executed_strategy": latest_strategy,
            "planner_ready": planner_ready, "planner_model": planner_connection["model"],
            "planner_family": planner_connection["family"], "planner_readiness_scope": "configuration_only_not_live_health",
            "validators": slots,
            "committee_ready": committee_ready, "distinct_model_families": distinct_families,
            "calibrator_ready": calibrator_ready,
            "policy_ready": policy_ready,
            "auto_accept_enabled": bool((self.config.get("routing") or {}).get("auto_accept_enabled", False)),
        }

    def runs(self, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        page, page_size = max(1, int(page)), min(100, max(1, int(page_size)))
        items = self.repository.list_runs(limit=page_size, offset=(page - 1) * page_size)
        return {"items": items, "page": page, "page_size": page_size}

    def items(
        self, run_id: str, *, status: Optional[str] = None, relation_id: Optional[str] = None,
        page: int = 1, page_size: int = 20,
    ) -> Dict[str, Any]:
        page, page_size = max(1, int(page)), min(100, max(1, int(page_size)))
        self.repository.get_run(run_id)
        total = self.repository.count_candidates(
            run_id, human_status=status, relation_id=relation_id,
        )
        items = self.repository.list_candidates(
            run_id, human_status=status, relation_id=relation_id,
            limit=page_size, offset=(page - 1) * page_size,
        )
        summaries = []
        for item in items:
            decision = self.repository.latest_decision(item["candidate_id"])
            try:
                audit = self.repository.get_audit(item["candidate_id"], (decision or {}).get("audit_id"))
                if int(audit["revision"]) != int(item["revision"]):
                    audit = None
            except NotFoundError:
                audit = None
            summaries.append({**item, "latest_decision": decision,
                              "audit_summary": None if audit is None else {
                                  "audit_id": audit["audit_id"], "status": audit["status"],
                                  "calibrated_probability": audit["calibrated_probability"],
                                  "calibration_status": audit["calibration_status"],
                                  "rounds": len(audit["rounds"]), "votes": audit["votes"],
                                  "revision": audit["revision"], "versions": audit["context"],
                                  "final_round_id": (audit["rounds"][-1]["round_id"] if audit["rounds"] else None),
                                  "evidence_bundle": (audit["rounds"][-1].get("evidence_bundle") if audit["rounds"] else {}),
                                  "gaps": (audit["rounds"][-1].get("gaps") if audit["rounds"] else {}),
                                  "ontology_check": (audit["rounds"][-1].get("ontology_check") if audit["rounds"] else {}),
                                  "planner_steps": [{k: v for k, v in step.items() if k != "attempts"}
                                                    for step in audit["planner_steps"]],
                                  "tool_calls": [{k: v for k, v in call.items() if k in {
                                      "tool_call_id", "step_no", "tool", "status", "error", "new_evidence_count",
                                      "scope", "arguments", "validation"}} for call in audit["tool_calls"]],
                                  "configured_strategy": audit["context"].get("configured_strategy", "deterministic"),
                                  "executed_strategy": audit["context"].get("executed_strategy", "historical_unrecorded"),
                                  "stop_reason": audit["context"].get("agent_stop_reason"),
                                  "unresolved_issues": audit["context"].get("unresolved_issues", []),
                                  "agent_budget": audit["context"].get("agent_budget"),
                                  "revision_drafts": audit["context"].get("revision_drafts", []),
                              }})
        return {"items": summaries, "total": total, "page": page, "page_size": page_size}

    def summary(self, run_id: str) -> Dict[str, Any]:
        self.repository.get_run(run_id)
        with self.repository.connect() as connection:
            rows = connection.execute(
                "SELECT human_status, import_status, COUNT(*) AS count FROM candidates WHERE run_id=? GROUP BY human_status, import_status",
                (run_id,),
            ).fetchall()
            routes = connection.execute(
                """SELECT d.final_decision, COUNT(*) AS count FROM decisions d JOIN candidates c
                   ON c.latest_decision_id=d.decision_id WHERE c.run_id=? GROUP BY d.final_decision""",
                (run_id,),
            ).fetchall()
            relations = connection.execute(
                """SELECT COALESCE(json_extract(r.candidate_json, '$.triple.relation_id'),
                          json_extract(r.candidate_json, '$.relation_id'), '(unmapped)') AS relation,
                          COUNT(*) AS count
                   FROM candidates c JOIN candidate_revisions r
                   ON r.candidate_id=c.candidate_id AND r.revision=c.current_revision
                   WHERE c.run_id=? GROUP BY relation ORDER BY count DESC, relation""",
                (run_id,),
            ).fetchall()
        human = Counter()
        imports = Counter()
        for row in rows:
            human[row["human_status"]] += row["count"]
            imports[row["import_status"]] += row["count"]
        return {
            "run_id": run_id,
            "human_status": human,
            "import_status": imports,
            "routes": {row["final_decision"]: row["count"] for row in routes},
            "total": sum(row["count"] for row in rows),
            "pending": human.get("pending", 0), "accepted": human.get("accepted", 0),
            "rejected": human.get("rejected", 0), "deferred": human.get("deferred", 0),
            "filtered": sum(row["count"] for row in rows),
            "groups": [{"relation": row["relation"], "count": row["count"]} for row in relations],
        }

    def evidence_context(self, evidence_id: str, *, before: int = 1, after: int = 1,
                         audit_id: Optional[str] = None, round_id: Optional[int] = None) -> Dict[str, Any]:
        from evidence.verification import verify_evidence_bundle, make_evidence_bundle
        before, after = min(5, max(0, int(before))), min(5, max(0, int(after)))
        with self.repository.connect() as connection:
            rows = connection.execute(
                """SELECT p.proposal_json, a.candidate_id, a.revision FROM proposals p
                   JOIN audit_runs a ON a.audit_id=p.audit_id
                   WHERE (? IS NULL OR a.audit_id=?) AND (? IS NULL OR p.round_id=?)
                   ORDER BY p.created_at DESC, p.round_id DESC""",
                (audit_id, audit_id, round_id, round_id),
            ).fetchall()
        selected = None
        candidate_id = None
        for row in rows:
            proposal = json.loads(row["proposal_json"])
            selected = next((item for item in (proposal.get("evidence_bundle") or {}).get("items", [])
                             if item.get("evidence_id") == evidence_id), None)
            if selected:
                candidate_id = row["candidate_id"]
                revision = row["revision"]
                break
        if selected is None:
            raise NotFoundError(f"evidence not found: {evidence_id}")
        candidate = self.repository.get_revision(candidate_id, revision)
        manifest = self.repository.get_run(candidate["run_id"])
        context = []
        document_path = manifest.get("document_path")
        status = "source_missing"
        if document_path and Path(document_path).exists():
            document = json.loads(Path(document_path).read_text(encoding="utf-8"))
            verified = verify_evidence_bundle(make_evidence_bundle([selected], []), document, candidate)
            selected = (verified["items"] or verified["rejected_items"])[0]
            status = "exact" if selected["anchor_valid"] else selected["anchor_status"]
            chunk = next((item for item in document.get("chunks", [])
                          if item.get("chunk_id") == selected.get("chunk_id")), None)
            if chunk and selected["anchor_valid"]:
                sentences = chunk.get("sentences") or []
                start = selected.get("chunk_start") if selected.get("offset_basis") == "canonical_document" else selected["start"]
                end = selected.get("chunk_end") if selected.get("offset_basis") == "canonical_document" else selected["end"]
                covered = [i for i, s in enumerate(sentences) if s["start"] < end and s["end"] > start]
                context = sentences[max(0, covered[0] - before):covered[-1] + after + 1] if covered else [
                    {"text": selected["quote"], "start": start, "end": end}]
        else:
            selected = {**selected, "anchor_valid": False, "anchor_status": "source_missing"}
        return {"evidence": selected, "context": context, "before": before, "after": after,
                "status": status, "audit_id": proposal["audit_id"], "round_id": proposal["round_id"]}

    def candidate(self, candidate_id: str, revision: Optional[int] = None) -> Dict[str, Any]:
        candidate = self.repository.get_candidate(candidate_id) if revision is None else self.repository.get_revision(candidate_id, revision)
        return {"candidate": candidate, "latest_decision": self.repository.latest_decision(candidate_id)}

    def audit(self, candidate_id: str, audit_id: Optional[str] = None) -> Dict[str, Any]:
        return self.repository.get_audit(candidate_id, audit_id)

    def decide(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        request = {**dict(payload), "reviewer": self.config.get("local_operator", "local_operator")}
        if "labels" not in request and isinstance(request.get("annotation"), dict):
            request["labels"] = request["annotation"]
        candidate = self.repository.get_candidate(str(request.get("candidate_id") or ""))
        if request.get("run_id") and str(request["run_id"]) != candidate["run_id"]:
            raise ValidationError("candidate does not belong to the requested run")
        manifest = self.repository.get_run(candidate["run_id"])
        ontology_path = manifest.get("ontology_path") or self.config.get("ontology_path")
        guard = None
        if ontology_path:
            path = Path(ontology_path)
            if not path.is_absolute():
                path = ROOT / path
            guard = OntologyGuard(load_snapshot(path, candidate.get("ontology_version")))
        return FeedbackService(self.repository, ontology_guard=guard).submit(request)

    def create_audit_job(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        run_id = str(payload.get("run_id") or "")
        if not run_id:
            raise ValidationError("run_id is required")
        self.repository.get_run(run_id)
        for candidate_id in payload.get("candidate_ids") or []:
            if self.repository.get_candidate(str(candidate_id))["run_id"] != run_id:
                raise ValidationError("candidate does not belong to the requested run")
        requested_mode = payload.get("mode") or self.config.get("mode", "shadow")
        payload = {**dict(payload), "mode": requested_mode, "force": bool(payload.get("force", False))}
        if requested_mode == "active":
            status = self.config_status()
            missing = [
                key for key in ("committee_ready", "calibrator_ready", "policy_ready")
                if not status[key]
            ]
            if missing:
                raise ServiceUnavailableError("active review is unavailable: " + ", ".join(missing))
        if self.job_runner is None:
            return self.repository.create_job("audit", payload)
        return self.job_runner.submit(dict(payload))

    def create_reaudit_job(
        self, candidate_id: str, payload: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        candidate = self.repository.get_candidate(candidate_id)
        request = {**dict(payload or {}), "run_id": candidate["run_id"],
                   "candidate_ids": [candidate_id], "force": True}
        return self.create_audit_job(request)

    def execute_audit_job(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        from .batch import run_review_batch

        job_id = payload.get("_job_id")
        effective_config = {**self.config, "mode": payload.get("mode") or self.config.get("mode", "shadow")}
        return run_review_batch(self.repository, str(payload.get("run_id") or ""), effective_config,
            job_id=job_id,
            candidate_ids=payload.get("candidate_ids"), force=bool(payload.get("force", False)),
            limit=payload.get("limit"), status_file=payload.get("status_file"),
            on_progress=(lambda progress: self.repository.heartbeat_job(str(job_id), progress)) if job_id else None,
            should_cancel=(lambda: bool(self.repository.get_job(str(job_id)).get("cancel_requested"))) if job_id else None)

    def create_planner_client(self):
        return create_planner_client(self.config)

    def job(self, job_id: str) -> Dict[str, Any]:
        return self.repository.get_job(job_id)

    def cancel_job(self, job_id: str) -> Dict[str, Any]:
        return self.repository.request_cancel(job_id)

    def export_run(self, run_id: str, output_dir: Any) -> Dict[str, Any]:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        all_items = self.repository.list_candidates(run_id, limit=1_000_000)
        groups = {"candidates": [], "accepted": [], "review_queue": [], "rejected": []}
        for item in all_items:
            decision = self.repository.latest_decision(item["candidate_id"])
            record = {**item, "latest_decision": decision}
            groups["candidates"].append(record)
            final = (decision or {}).get("decision")
            if final in {"auto_accept", "human_accept"}:
                groups["accepted"].append(record)
            elif final in {"auto_reject", "human_reject"}:
                groups["rejected"].append(record)
            else:
                groups["review_queue"].append(record)
        paths = {}
        for name, values in groups.items():
            path = output / f"{name}.json"
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
            paths[name] = str(path)
        return {"run_id": run_id, "paths": paths, "counts": {key: len(value) for key, value in groups.items()}}


_default_service: Optional[ReviewService] = None


def get_default_service() -> ReviewService:
    global _default_service
    if _default_service is None:
        config = load_config()
        database_path = Path(config.get("database_path", "runs/v3/review.sqlite3"))
        if not database_path.is_absolute():
            database_path = ROOT / database_path
        repository = ReviewRepository(database_path)
        repository.initialize()
        service = ReviewService(repository, config=config)
        service.job_runner = JobRunner(
            repository, service.execute_audit_job,
            max_workers=int((config.get("budget") or {}).get("max_parallel_candidates", 2)),
            lease_seconds=int((config.get("budget") or {}).get("total_timeout_seconds", 180)) + 30,
        )
        _default_service = service
    return _default_service
