"""Recoverable SQLite-backed audit jobs with bounded local workers."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from .repository import ReviewRepository


class JobRunner:
    def __init__(
        self, repository: ReviewRepository, handler: Callable[[Dict[str, Any]], Dict[str, Any]], *,
        max_workers: int = 2, lease_seconds: int = 210,
    ):
        self.repository = repository
        self.handler = handler
        self.owner = f"worker_{uuid4().hex[:12]}"
        self.lease_seconds = max(30, int(lease_seconds))
        self.pool = ThreadPoolExecutor(max_workers=max(1, int(max_workers)), thread_name_prefix="review-agent")
        self._lock = threading.Lock()

    def submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        job = self.repository.create_job("audit", payload)
        self.pool.submit(self.run_available_once)
        return job

    def run_available_once(self) -> Optional[Dict[str, Any]]:
        job = self.repository.claim_job(self.owner, lease_seconds=self.lease_seconds)
        if job is None:
            return None
        if job.get("cancel_requested"):
            self.repository.complete_job(job["job_id"], {"cancelled": True}, cancelled=True)
            return self.repository.get_job(job["job_id"])
        try:
            result = self.handler({**job["payload"], "_job_id": job["job_id"]})
        except Exception as exc:
            latest = self.repository.get_job(job["job_id"])
            self.repository.complete_job(
                job["job_id"], {"error": str(exc), "cancelled": bool(latest.get("cancel_requested"))},
                failed=not bool(latest.get("cancel_requested")),
                cancelled=bool(latest.get("cancel_requested")),
            )
        else:
            latest = self.repository.get_job(job["job_id"])
            if latest.get("cancel_requested"):
                result = {**result, "cancelled": True}
            self.repository.complete_job(
                job["job_id"], result, cancelled=bool(latest.get("cancel_requested")),
                paused=bool(result.get("paused")), failed=bool(result.get("failed")),
            )
        return self.repository.get_job(job["job_id"])

    def recover_expired(self) -> Optional[Dict[str, Any]]:
        return self.run_available_once()

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
