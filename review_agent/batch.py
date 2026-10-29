"""Bounded, killable candidate processes shared by CLI and HTTP review jobs."""

from __future__ import annotations

import json
import logging
import multiprocessing
import time
from multiprocessing.connection import wait
from pathlib import Path
from uuid import uuid4

from .contracts import EVIDENCE_BUNDLE_VERSION, sha256_json
from .execution import AuditPaused
from .repository import ReviewRepository
from .model_settings import validator_settings, configured_planner, create_planner_client
from .response_contract import PROMPT_VERSION, CONTRACT_VERSION, PROMPT_HASH, RESPONSE_SCHEMA
from .progress import write_status_snapshot


def configured_members(config):
    members = []
    for slot in config.get("validators") or []:
        values = validator_settings(slot)
        if all(values[key] for key in ("model", "family", "api_key", "base_url")):
            members.append({"id": slot["id"], "model_id": values["model"], "model_family": values["family"], "model_identity": values["model_identity"]})
    return members


def versions_for(config):
    from .memory_scope import resolve_scope,scope_hash
    versions = {"prompt_version": PROMPT_VERSION, "response_contract_version": CONTRACT_VERSION,
            "prompt_hash": PROMPT_HASH,
            "response_contract_hash": sha256_json(RESPONSE_SCHEMA),
            "committee_policy_hash": sha256_json({key: (config.get("consensus") or {}).get(key, default)
                for key, default in (("committee_size", 4), ("required_votes", 3), ("minimum_distinct_families", 3))}),
            "evidence_bundle_version": EVIDENCE_BUNDLE_VERSION,
            "committee_version": (config.get("consensus") or {}).get("version", "committee_v1")}
    versions['memory_policy_hash']=sha256_json({'scope_hash':scope_hash(resolve_scope(config)),
        'max_positive_cases':(config.get('memory') or {}).get('max_positive_cases',3),
        'max_negative_cases':(config.get('memory') or {}).get('max_negative_cases',3)})
    planner = config.get("planner") or {}
    if planner.get("enabled") and planner.get("strategy") == "llm_react":
        from .planner_contracts import PLANNER_CONTRACT_VERSION, PLANNER_PROMPT_VERSION, AGENT_STRATEGY_VERSION, PLANNER_PROMPT_HASH, argument_schemas
        settings = configured_planner(config)
        versions.update(planner_contract_version=PLANNER_CONTRACT_VERSION, planner_prompt_version=PLANNER_PROMPT_VERSION,
            planner_contract_hash=sha256_json(argument_schemas()),
            planner_prompt_hash=PLANNER_PROMPT_HASH,
            planner_strategy_version=AGENT_STRATEGY_VERSION, planner_model_id=settings["model"],
            planner_model_family=settings["family"], configured_strategy="llm_react")
    return versions


def _candidate_worker(sender, payload):
    """Everything able to block is owned by this disposable process."""
    repository = ReviewRepository(payload["database"])
    try:
        from confidence.calibrator import TripleCalibrator, load_calibrator
        from confidence.risk_policy import RiskPolicy, load_policy
        from document.hierarchy_index import load_document_index
        from .cli import _configured_validators
        from .ontology_guard import OntologyGuard, load_snapshot
        from .orchestrator import ReviewOrchestrator

        config = payload["config"]
        manifest = repository.get_run(payload["run_id"])
        document = load_document_index(manifest["document_path"], manifest.get("source_index_hash"),
                                       require_verified=manifest.get("candidate_contract_version") == "extraction_candidate_v4")
        candidate = repository.get_revision(payload["candidate_id"], payload["revision"])
        guard = OntologyGuard(load_snapshot(candidate.get("ontology_path") or manifest["ontology_path"],
                                           candidate.get("ontology_version") or manifest.get("ontology_hash")))
        calibration = config.get("calibration") or {}
        root = Path(__file__).resolve().parents[1]

        def resolved(value):
            if not value:
                return None
            path = Path(value)
            return str(path if path.is_absolute() else root / path)

        model, policy = resolved(calibration.get("model_path")), resolved(calibration.get("policy_path"))
        def should_stop():
            job_id = payload.get("job_id")
            return bool(job_id and repository.get_job(job_id).get("cancel_requested"))

        orchestrator = ReviewOrchestrator(repository, guard, _configured_validators(config),
            calibrator=load_calibrator(model) if model else TripleCalibrator(),
            risk_policy=load_policy(policy) if policy else RiskPolicy(), config=config,
            planner_client=create_planner_client(config), should_cancel=should_stop)
        result = orchestrator.audit_candidate(payload["candidate_id"], payload["revision"], document,
            context={"audit_id": payload["audit_id"], "memory_snapshot_id": payload["memory_snapshot_id"],
                     "execution_id": payload["execution_id"], "execution_version": "bounded_batch_v1",
                     "request_cache_namespace": payload.get("request_cache_namespace", "default"),
                     "job_id": payload.get("job_id"),
                     "execution_deadline_epoch": payload["deadline_epoch"]})
        status = repository.get_audit(payload["candidate_id"], payload["audit_id"])["status"]
        sender.send({"status": "completed" if status == "completed" else status, "result": result})
    except AuditPaused as exc:
        sender.send({"status": "paused", "reason_code": "INSUFFICIENT_BALANCE", "vote": exc.vote})
    except Exception as exc:
        repository.finish_incomplete_audit(payload["audit_id"], "failed", {"error": str(exc)})
        sender.send({"status": "failed", "error": str(exc)})
    finally:
        sender.close()


def _stop_process(process):
    # A successful result may still own blocked request threads. Reap them too.
    process.join(timeout=.05)
    if process.is_alive():
        process.terminate()
        process.join(timeout=1)
    if process.is_alive():
        process.kill()
        process.join(timeout=1)
    if process.is_alive():
        raise RuntimeError("candidate process could not be terminated")
    process.close()


def run_review_batch(repository, run_id, config, *, candidate_ids=None, force=False,
                     limit=None, status_file=None, on_progress=None, should_cancel=None,
                     _worker_target=None, job_id=None):
    """Resume healthy completed revisions, bound concurrency, pause globally on 402."""
    repository.get_run(run_id)
    members = configured_members(config)
    if _worker_target is None and not members:
        raise ValueError("no configured review models")
    candidates = ([repository.get_candidate(cid) for cid in dict.fromkeys(candidate_ids)] if candidate_ids
                  else repository.list_candidates(run_id, human_status="pending", limit=1_000_000))
    if any(c["run_id"] != run_id for c in candidates):
        raise ValueError("candidate does not belong to the audit run")
    versions = versions_for(config)
    pending, skipped = [], 0
    for candidate in candidates:
        if candidate.get("workflow_state") in {"awaiting_ontology_review", "awaiting_ontology_release"}:
            skipped += 1
        elif not force and repository.has_healthy_audit(candidate["candidate_id"], candidate["revision"], versions, members):
            skipped += 1
        else:
            pending.append(candidate)
    if limit is not None:
        if int(limit) < 1:
            raise ValueError("limit must be positive")
        pending = pending[:int(limit)]
    budget = config.get("budget") or {}
    timeout = float(budget.get("total_timeout_seconds", 180))
    parallel = int(budget.get("max_parallel_candidates", 2))
    if timeout <= 0 or parallel < 1:
        raise ValueError("candidate deadline and parallelism must be positive")
    from .memory import ReviewMemory
    memory = ReviewMemory(repository).current_version()
    execution_id = "batch_" + uuid4().hex
    state = {"execution_id": execution_id, "run_id": run_id, "status": "running",
             "started_at_epoch": time.time(), "total": len(candidates), "selected": len(pending),
             "skipped": skipped, "completed": 0, "failed": 0, "interrupted": 0,
             "independent_review_completed":0,"review_incomplete":0,
             "paused": False, "cancelled": False, "results": [], "review_versions": versions,
             "progress_write_failures": 0, "progress_file_stale": False}
    ctx = multiprocessing.get_context("spawn")
    active, index = {}, 0
    last_progress, last_published = None, 0.0
    last_write_warning = None

    def publish():
        nonlocal last_progress, last_published, last_write_warning
        progress = {k: v for k, v in state.items() if k != "results"}
        progress.update(active=[{"pid": w["process"].pid,
            "audit_id": w["payload"]["audit_id"], "candidate_id": w["payload"]["candidate_id"]} for w in active.values()],
            remaining=len(pending) - index + len(active))
        if progress == last_progress and time.monotonic() - last_published < 5:
            return
        progress["updated_at_epoch"] = time.time()
        if status_file:
            progress["progress_file_stale"] = False
            try:
                write_status_snapshot(status_file, progress)
                state["progress_file_stale"] = False
            except OSError as exc:
                state["progress_write_failures"] += 1
                state["progress_file_stale"] = True
                state["last_progress_write_error"] = str(exc)
                progress.update({k: state[k] for k in (
                    "progress_write_failures", "progress_file_stale", "last_progress_write_error")})
                if last_write_warning is None or time.monotonic() - last_write_warning >= 60:
                    logging.getLogger(__name__).warning(
                        "Progress file unavailable; review continues and results remain in SQLite: %s", exc)
                    last_write_warning = time.monotonic()
        last_progress = {k: v for k, v in progress.items() if k != "updated_at_epoch"}
        last_published = time.monotonic()
        if on_progress:
            on_progress(progress)

    def reap(connection, outcome):
        worker = active.pop(connection)
        payload = worker["payload"]
        _stop_process(worker["process"])
        connection.close()
        status = outcome.get("status", "failed")
        if status != "completed":
            repository.finish_incomplete_audit(payload["audit_id"],
                status if status in {"timeout", "paused", "interrupted"} else "failed", outcome)
        state["results"].append({"audit_id": payload["audit_id"], "candidate_id": payload["candidate_id"],
                                 "revision": payload["revision"], **outcome})
        if status == "completed":
            state["completed"] += 1
            if (outcome.get('result') or {}).get('independent_review_completed'):
                state['independent_review_completed']+=1
            else:
                state['review_incomplete']+=1
        elif status == "interrupted":
            state["interrupted"] += 1
        elif status != "paused":
            state["failed"] += 1

    publish()
    try:
        while index < len(pending) or active:
            if should_cancel and should_cancel():
                state.update(status="cancelled", cancelled=True)
                break
            # Do not pre-submit the whole book: at most parallel candidates own sockets.
            while index < len(pending) and len(active) < parallel:
                candidate = pending[index]
                index += 1
                if repository.get_candidate(candidate["candidate_id"]).get("workflow_state") in {"awaiting_ontology_review", "awaiting_ontology_release"}:
                    state["skipped"] += 1
                    state["selected"] -= 1
                    continue
                # Another job may have finished this candidate since queue selection.
                if not force and repository.has_healthy_audit(candidate["candidate_id"], candidate["revision"], versions, members):
                    state["skipped"] += 1
                    state["selected"] -= 1
                    continue
                payload = {"database": str(repository.database_path.resolve()), "run_id": run_id,
                    "candidate_id": candidate["candidate_id"], "revision": candidate["revision"],
                    "audit_id": "audit_" + uuid4().hex, "config": dict(config),
                    "execution_id": execution_id, "memory_snapshot_id": memory,
                    "deadline_epoch": time.time() + timeout}
                payload["job_id"] = job_id
                payload["request_cache_namespace"] = execution_id if force else "default"
                if not force and (config.get("planner") or {}).get("strategy") == "llm_react":
                    resume = repository.resumable_agent_audit(candidate["candidate_id"], candidate["revision"], versions, members)
                    if resume:
                        payload["audit_id"] = resume["audit_id"]
                        payload["deadline_epoch"] = min(payload["deadline_epoch"],
                            resume["context"].get("execution_deadline_epoch", payload["deadline_epoch"]))
                receiver, sender = ctx.Pipe(duplex=False)
                process = ctx.Process(target=_worker_target or _candidate_worker, args=(sender, payload),
                                      name="review-candidate")
                began = time.monotonic()
                try:
                    process.start()
                except BaseException:
                    receiver.close()
                    sender.close()
                    raise
                sender.close()
                active[receiver] = {"process": process, "payload": payload, "started": began}
            ready = wait(list(active), timeout=.1) if active else []
            outcomes = []
            for connection in ready:
                try:
                    outcome = connection.recv()
                except (EOFError, OSError):
                    outcome = {"status": "failed", "error": "candidate process exited without a result"}
                outcomes.append((connection, outcome))
            # Observe all available results, especially billing errors, before dispatch.
            for connection, outcome in outcomes:
                if outcome.get("status") == "paused":
                    state.update(status="paused", paused=True, pause_reason=outcome)
                reap(connection, outcome)
            if state["paused"]:
                break
            for connection, worker in list(active.items()):
                if time.monotonic() - worker["started"] >= timeout or time.time() >= worker["payload"]["deadline_epoch"]:
                    reap(connection, {"status": "timeout", "reason_code": "HARD_CANDIDATE_TIMEOUT",
                                      "error": "candidate exceeded its total hard deadline"})
                elif not worker["process"].is_alive():
                    # On Windows the process handle can become signalled a few milliseconds
                    # before the pipe's feeder thread makes the final result observable.  Give
                    # that already-finished worker one bounded chance to flush its result rather
                    # than turning a successful candidate into a spurious failure.
                    if connection.poll(.1):
                        try:
                            outcome = connection.recv()
                        except (EOFError, OSError):
                            outcome = {"status": "failed", "error": "candidate process exited without a result"}
                    else:
                        outcome = {"status": "failed", "error": "candidate process exited unexpectedly"}
                    reap(connection, outcome)
            publish()
        if state["status"] == "running":
            state["status"] = "failed" if state["failed"] else "completed"
    except BaseException:
        state["status"] = "interrupted"
        raise
    finally:
        for connection in list(active):
            reap(connection, {"status": "interrupted", "reason_code": "BATCH_" + state["status"].upper()})
        state["finished_at_epoch"] = time.time()
        publish()
    return state
