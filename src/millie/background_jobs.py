from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from .connector_reliability import classify_connector_exception
from .database import utc_now
from .graph_connector import sync_graph_source
from .imap_connector import get_imap_source, sync_imap_source
from .pop_connector import get_pop_source, sync_pop_source
from .profiles import ProfileManager
from .secrets import SecretManager


SyncRunner = Callable[["BackgroundJob"], dict[str, Any]]


@dataclass(slots=True)
class BackgroundJob:
    id: str
    task_type: str
    connector: str
    source_id: str
    profile_id: str
    status: str
    queued_at: str
    started_at: str | None = None
    finished_at: str | None = None
    sync_limit: int | None = None
    folders: list[str] | None = None
    message: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    failure: dict[str, Any] | None = None
    cancel_requested: bool = False
    events: list[dict[str, str]] = field(default_factory=list)

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_type": self.task_type,
            "connector": self.connector,
            "source_id": self.source_id,
            "profile_id": self.profile_id,
            "status": self.status,
            "queued_at": self.queued_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "sync_limit": self.sync_limit,
            "folders": self.folders or [],
            "message": self.message,
            "result": self.result or {},
            "error": self.error,
            "failure": self.failure,
            "cancel_requested": self.cancel_requested,
            "events": list(self.events),
        }


class BackgroundJobManager:
    def __init__(
        self,
        profile_manager: ProfileManager,
        secret_manager: SecretManager,
        sync_runner: SyncRunner | None = None,
    ):
        self.profile_manager = profile_manager
        self.secret_manager = secret_manager
        self.sync_runner = sync_runner or self.run_sync_job
        self.lock = threading.RLock()
        self.jobs: dict[str, BackgroundJob] = {}
        self.order: list[str] = []
        self.worker: threading.Thread | None = None

    def enqueue_sync(
        self,
        connector: str,
        source_id: str,
        *,
        sync_limit: int | None = None,
        folders: list[str] | None = None,
    ) -> BackgroundJob:
        normalized = normalize_connector(connector)
        job = BackgroundJob(
            id=uuid4().hex[:12],
            task_type="sync",
            connector=normalized,
            source_id=source_id,
            profile_id=self.profile_manager.active_profile_id,
            status="queued",
            queued_at=utc_now(),
            sync_limit=max(1, int(sync_limit)) if sync_limit else None,
            folders=[folder for folder in (folders or []) if folder],
            message="Queued",
        )
        self.add_event(job, "queued")
        with self.lock:
            self.jobs[job.id] = job
            self.order.append(job.id)
            self.start_worker_locked()
        return job

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.lock:
            return [self.jobs[job_id].to_api() for job_id in reversed(self.order)]

    def get_job(self, job_id: str) -> BackgroundJob | None:
        with self.lock:
            return self.jobs.get(job_id)

    def cancel_job(self, job_id: str) -> BackgroundJob | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            job.cancel_requested = True
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = utc_now()
                job.message = "Cancelled before start"
                self.add_event(job, "cancelled")
            elif job.status == "running":
                job.message = "Cancel requested; current connector call will finish first"
                self.add_event(job, "cancel_requested")
            return job

    def start_worker_locked(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.worker = threading.Thread(target=self.worker_loop, name="millie-background-sync", daemon=True)
        self.worker.start()

    def worker_loop(self) -> None:
        while True:
            with self.lock:
                job = next((self.jobs[job_id] for job_id in self.order if self.jobs[job_id].status == "queued"), None)
                if job is None:
                    self.worker = None
                    return
            self.execute(job)

    def execute(self, job: BackgroundJob) -> None:
        with self.lock:
            if job.cancel_requested:
                job.status = "cancelled"
                job.finished_at = utc_now()
                job.message = "Cancelled before start"
                self.add_event(job, "cancelled")
                return
            if job.profile_id != self.profile_manager.active_profile_id:
                job.status = "failed"
                job.finished_at = utc_now()
                job.error = "Active profile changed before the job started"
                job.message = job.error
                self.add_event(job, "failed")
                return
            job.status = "running"
            job.started_at = utc_now()
            job.message = "Running"
            self.add_event(job, "running")
        try:
            result = self.sync_runner(job)
        except Exception as exc:  # noqa: BLE001
            failure = classify_connector_exception(job.connector, exc)
            with self.lock:
                job.status = "failed"
                job.finished_at = utc_now()
                job.error = str(exc)
                job.failure = failure.to_api()
                job.message = failure.user_action
                self.add_event(job, "failed")
            return
        with self.lock:
            job.status = "completed"
            job.finished_at = utc_now()
            job.result = result
            job.message = "Completed"
            self.add_event(job, "completed")

    def run_sync_job(self, job: BackgroundJob) -> dict[str, Any]:
        db = self.profile_manager.active_database()
        if job.connector == "imap":
            source = get_imap_source(self.profile_manager, job.source_id, self.secret_manager)
            return sync_imap_source(db, source, folders=job.folders, sync_limit=job.sync_limit).to_api()
        if job.connector == "pop3":
            source = get_pop_source(self.profile_manager, job.source_id, self.secret_manager)
            return sync_pop_source(db, source, sync_limit=job.sync_limit).to_api()
        if job.connector == "graph":
            return sync_graph_source(
                db,
                self.profile_manager,
                job.source_id,
                self.secret_manager,
                sync_limit=job.sync_limit,
            ).to_api()
        raise ValueError(f"Unsupported sync connector: {job.connector}")

    def add_event(self, job: BackgroundJob, status: str) -> None:
        job.events.append({"at": utc_now(), "status": status, "message": job.message})


def normalize_connector(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"imap", "imap4"}:
        return "imap"
    if normalized in {"pop", "pop3"}:
        return "pop3"
    if normalized in {"graph", "microsoft-graph", "exchange"}:
        return "graph"
    raise ValueError(f"Unsupported sync connector: {value}")
