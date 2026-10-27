"""HTTP/CLI entry points and killable ontology workers using shared job leases."""
import multiprocessing
import time
from pathlib import Path
from uuid import uuid4
from review_agent.repository import ReviewRepository, ConflictError
from review_agent.model_settings import create_planner_client
from review_agent.contracts import sha256_json
from .publisher import OntologyPublisher, current_release_id


ROOT = Path(__file__).resolve().parents[1]


def effective_config(config):
    result = dict(config)
    options = dict(config.get("ontology_extension") or {})
    if options.get("strategy", "llm_react") != "llm_react": raise ValueError("ontology agent requires llm_react strategy")
    planner = {**(config.get("planner") or {}), **options.get("planner", {}), "enabled": True}
    result["planner"] = planner
    return result


def worker(sender, database, release_root, config, extension_id, audit_id):
    try:
        from review_agent.cli import _configured_validators
        from .runtime import OntologyReviewRuntime
        repository = ReviewRepository(database)
        runtime = OntologyReviewRuntime(repository, Path(release_root), _configured_validators(config),
                                        create_planner_client(effective_config(config)), config)
        result = runtime.run(extension_id, audit_id)
        if (result["recommendation"] == "approve" and config.get("mode") == "active"
                and (config.get("ontology_extension") or {}).get("auto_publish_enabled")):
            publisher = OntologyPublisher(repository, release_root, config)
            task = repository.extension_status(extension_id)
            if task.get("delta", {}).get("risk_category") == "additive":
                result["publication"] = publisher.publish(extension_id, {"expected_row_version": task["row_version"],
                    "proposal_digest": result["selected_proposal_digest"], "expected_base_release_id": task["base_release_id"],
                    "approved_by": "ontology_agent_policy", "reason": "Active low-risk ontology policy and fixed independent committee approved."}, automatic=True)
        sender.send({"result": result})
    except BaseException as exc:
        sender.send({"error": str(exc), "kind": type(exc).__name__})
    finally: sender.close()


class OntologyReviewService:
    def __init__(self, review_service):
        self.review = review_service
        self.repository, self.config = review_service.repository, review_service.config
        self.root = Path(self.config.get("ontology_release_root") or ROOT / "runs/ontology_releases")
        if not self.root.is_absolute(): self.root = ROOT / self.root
        self.publisher = OntologyPublisher(self.repository, self.root, self.config)

    def list(self, status=None):
        return {"items": self.repository.extensions(status), "current_release_id": current_release_id(self.root),
                "enabled": bool((self.config.get("ontology_extension") or {}).get("enabled")),
                "auto_publish_enabled": bool((self.config.get("ontology_extension") or {}).get("auto_publish_enabled"))}

    def detail(self, extension_id):
        task = self.repository.extension_status(extension_id)
        with self.repository.connect() as connection:
            ids = connection.execute("SELECT audit_id FROM ontology_audits WHERE extension_id=? ORDER BY rowid DESC LIMIT 20", (extension_id,)).fetchall()
        task["audits"] = [self.repository.ontology_audit(row[0]) for row in ids]
        try: task["publication_preview"] = self.publisher.preview(extension_id)
        except (ValueError, ConflictError): task["publication_preview"] = None
        return task

    def create_job(self, extension_id, request=None):
        from review_agent.service import ServiceUnavailableError
        from review_agent.cli import _configured_validators
        options = self.config.get("ontology_extension") or {}
        if not options.get("enabled"): raise ServiceUnavailableError("ontology extension agent is disabled")
        if not _configured_validators(self.config) or create_planner_client(effective_config(self.config)) is None:
            raise ServiceUnavailableError("ontology planner or independent reviewer services are not configured")
        task = self.repository.extension_status(extension_id)
        if task["status"] in {"publishing", "published", "needs_rebase"}: raise ConflictError("extension must finish publication or rebase before review")
        resume_id = None
        with self.repository.connect() as connection:
            row = connection.execute("SELECT audit_id,checkpoint_json FROM ontology_audits WHERE extension_id=? AND status IN ('running','interrupted','paused') ORDER BY rowid DESC LIMIT 1", (extension_id,)).fetchone()
        if row:
            import json
            saved = json.loads(row["checkpoint_json"] or "null")
            if saved and saved["state"]["task"]["row_version"] == task["row_version"]: resume_id = row["audit_id"]
        payload = {"extension_id": extension_id, "audit_id": (request or {}).get("audit_id") or resume_id or "ontology_audit_" + uuid4().hex}
        if self.review.job_runner: return self.review.job_runner.submit(payload, job_type="ontology_extension")
        return self.repository.create_job("ontology_extension", payload)

    def execute(self, payload, *, _worker_target=None):
        extension_id, audit_id = payload["extension_id"], payload["audit_id"]
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_worker_target or worker, args=(sender, str(self.repository.database_path.resolve()), str(self.root), self.config, extension_id, audit_id))
        process.start(); sender.close()
        deadline = time.monotonic() + float(((self.config.get("ontology_extension") or {}).get("budget") or {}).get("total_timeout_seconds", 360))
        last_heartbeat, message = 0., None
        try:
            while process.is_alive() or receiver.poll():
                job_id = payload.get("_job_id")
                cancelled = job_id and self.repository.get_job(job_id).get("cancel_requested")
                if cancelled or time.monotonic() >= deadline:
                    process.terminate(); process.join(timeout=3)
                    with self.repository.transaction(immediate=True) as connection:
                        connection.execute("UPDATE ontology_audits SET status='interrupted',lease_expires=0 WHERE audit_id=? AND status='running'", (audit_id,))
                    return {"audit_id": audit_id, "cancelled": bool(cancelled), "failed": not bool(cancelled), "stop_reason": "cancelled" if cancelled else "hard_worker_timeout"}
                if receiver.poll(.1):
                    try: message = receiver.recv()
                    except EOFError: break
                    break
                if job_id and time.monotonic() - last_heartbeat > 2:
                    self.repository.heartbeat_job(job_id, {"task_type": "ontology_extension", "extension_id": extension_id, "audit_id": audit_id})
                    last_heartbeat = time.monotonic()
            if message and "result" in message:
                result = message["result"]
                if self.review.job_runner:
                    for _ in (result.get("publication") or {}).get("reaudit_jobs", []):
                        self.review.job_runner.pool.submit(self.review.job_runner.run_available_once)
                return result
            return {"failed": True, "audit_id": audit_id, "error": (message or {}).get("error", "ontology worker exited without a result")}
        finally:
            if process.is_alive(): process.terminate()
            process.join(timeout=3); receiver.close()

    def publish(self, extension_id, request):
        result = self.publisher.publish(extension_id, {**request, "approved_by": self.config.get("local_operator", "local_operator")})
        if self.review.job_runner:
            for _ in result["reaudit_jobs"]: self.review.job_runner.pool.submit(self.review.job_runner.run_available_once)
        return result

    def decide(self, extension_id, request):
        if request.get("action") not in {"revise", "reject"}: raise ValueError("use exact reviewed publication action for approval")
        if not str(request.get("reason") or "").strip(): raise ValueError("human modeling reason required")
        task = self.repository.extension_status(extension_id)
        if task["status"] in {"publishing", "published"}: raise ConflictError("cannot alter an in-flight or published ontology task")
        event = {"action": request["action"], "reason": request["reason"], "operator": self.config.get("local_operator", "local_operator"), "delta_revision": task["delta_revision"]}
        return self.repository.extension_update(extension_id, request.get("expected_row_version"),
            changes={"human_events": [*task.get("human_events", []), event], "review_invalidated_by_human": True,
                     "human_revision_inputs": {"delta_hash": (task.get("delta") or {}).get("delta_hash"), "evidence_hash": sha256_json(task.get("review_evidence", []))}},
            status="rejected" if request["action"] == "reject" else "needs_human")
