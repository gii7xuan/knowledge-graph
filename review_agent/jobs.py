"""Recoverable SQLite-backed audit jobs with bounded local workers."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from .repository import ReviewRepository
from .repository import ConflictError
import time


class JobRunner:
    def __init__(
        self, repository: ReviewRepository, handler: Callable[[Dict[str, Any]], Dict[str, Any]], *,
        max_workers: int = 2, lease_seconds: int = 210, job_types=None,
    ):
        self.repository = repository
        self.handler = handler
        self.owner = f"worker_{uuid4().hex[:12]}"
        self.lease_seconds = max(30, int(lease_seconds))
        self.pool = ThreadPoolExecutor(max_workers=max(1, int(max_workers)), thread_name_prefix="review-agent")
        self._lock = threading.Lock()
        self.job_types=tuple(job_types or ('audit','ontology_extension'))
        self.max_workers=max(1,int(max_workers))
        self._stop=threading.Event()
        self._dispatcher=None
        self._serving=False

    def start(self):
        self.repository.repair_extension_jobs()
        if self._dispatcher and self._dispatcher.is_alive(): return
        self._stop.clear()
        def consume():
            pending=set()
            while not self._stop.is_set():
                pending={f for f in pending if not f.done()}
                for _ in range(self.max_workers-len(pending)):
                    pending.add(self.pool.submit(self.run_available_once))
                self._stop.wait(.5)
        self._dispatcher=threading.Thread(target=consume,daemon=True,name='durable-review-consumer')
        self._dispatcher.start()

    def serve(self, stop_event=None, poll_seconds=.5):
        self.repository.repair_extension_jobs()
        self._serving=True
        try:
            while not self._stop.is_set() and not (stop_event and stop_event.is_set()):
                self.run_available_once()
                self._stop.wait(poll_seconds)
        finally:
            self._serving=False

    def submit(self, payload: Dict[str, Any], *, job_type="audit") -> Dict[str, Any]:
        job = self.repository.create_job(job_type, payload)
        self.pool.submit(self.run_available_once,chain=True)
        return job

    def run_available_once(self, *, chain=False) -> Optional[Dict[str, Any]]:
        job = self.repository.claim_job(self.owner, lease_seconds=self.lease_seconds,job_types=self.job_types)
        if job is None:
            return None
        fence={'owner':self.owner,'lease_token':job['lease_token']}
        stopped=threading.Event()
        def heartbeat():
            while not stopped.wait(min(10,self.lease_seconds/3)):
                try: self.repository.heartbeat_job(job['job_id'],self.repository.get_job(job['job_id'])['progress'],self.lease_seconds,**fence)
                except ConflictError: return
        pulse=threading.Thread(target=heartbeat,daemon=True,name='review-job-heartbeat')
        pulse.start()
        try:
            result = self.handler({**job["payload"], "_job_id": job["job_id"], "_job_type": job["job_type"],
                                   '_job_owner':self.owner,'_job_lease_token':job['lease_token']})
        except Exception as exc:
            latest = self.repository.get_job(job["job_id"])
            try: self.repository.complete_job(
                job["job_id"], {"error": str(exc), "cancelled": bool(latest.get("cancel_requested"))},
                failed=not bool(latest.get("cancel_requested")),
                cancelled=bool(latest.get("cancel_requested")),**fence,
            )
            except ConflictError: pass
        else:
            latest = self.repository.get_job(job["job_id"])
            if latest.get("cancel_requested"):
                result = {**result, "cancelled": True}
            try: self.repository.complete_job(
                job["job_id"], result, cancelled=bool(latest.get("cancel_requested")),
                paused=bool(result.get("paused")), failed=bool(result.get("failed")),**fence,
            )
            except ConflictError: pass
        finally:
            stopped.set()
        with self.repository.connect() as connection:
            queued = connection.execute("SELECT 1 FROM jobs WHERE status='queued' AND cancel_requested=0 AND job_type='ontology_extension' LIMIT 1").fetchone()
        if queued and chain and not self._stop.is_set() and not self._serving and not self._dispatcher:
            self.pool.submit(self.run_available_once,chain=True)
        return self.repository.get_job(job["job_id"])

    def recover_expired(self) -> Optional[Dict[str, Any]]:
        return self.run_available_once()

    def shutdown(self) -> None:
        self._stop.set()
        if self._dispatcher: self._dispatcher.join(timeout=2)
        self.pool.shutdown(wait=False, cancel_futures=True)
