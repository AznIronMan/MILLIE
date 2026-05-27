from __future__ import annotations

import json
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
BACKGROUND_JOBS_SETTING = "background_jobs.v1"
ACTIVE_JOB_STATUSES = {"queued", "running"}
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
VALID_JOB_STATUSES = ACTIVE_JOB_STATUSES | TERMINAL_JOB_STATUSES


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

    def to_record(self) -> dict[str, Any]:
        return self.to_api()

    @classmethod
    def from_record(cls, record: dict[str, Any], *, default_profile_id: str) -> "BackgroundJob":
        status = string_value(record.get("status")) or "queued"
        if status not in VALID_JOB_STATUSES:
            status = "queued"
        return cls(
            id=string_value(record.get("id")) or uuid4().hex[:12],
            task_type=string_value(record.get("task_type")) or "sync",
            connector=normalize_connector(string_value(record.get("connector")) or "imap"),
            source_id=string_value(record.get("source_id")) or string_value(record.get("sourceId")) or "",
            profile_id=string_value(record.get("profile_id")) or default_profile_id,
            status=status,
            queued_at=string_value(record.get("queued_at")) or utc_now(),
            started_at=string_value(record.get("started_at")),
            finished_at=string_value(record.get("finished_at")),
            sync_limit=positive_int_or_none(record.get("sync_limit")),
            folders=string_list(record.get("folders")),
            message=string_value(record.get("message")) or "",
            result=dict_or_none(record.get("result")),
            error=string_value(record.get("error")),
            failure=dict_or_none(record.get("failure")),
            cancel_requested=bool(record.get("cancel_requested")),
            events=event_list(record.get("events")),
        )


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
        self.loaded_profiles: set[str] = set()
        self.worker: threading.Thread | None = None
        with self.lock:
            self.load_jobs_locked()
            self.recover_interrupted_jobs_locked()
            self.start_worker_locked()

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
            self.persist_profile_jobs_locked(job.profile_id)
            self.start_worker_locked()
        return job

    def list_jobs(self, profile_id: str | None = None) -> list[dict[str, Any]]:
        target_profile_id = profile_id or self.profile_manager.active_profile_id
        with self.lock:
            return [
                self.jobs[job_id].to_api()
                for job_id in reversed(self.order)
                if self.jobs[job_id].profile_id == target_profile_id
            ]

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
                self.persist_profile_jobs_locked(job.profile_id)
            elif job.status == "running":
                job.message = "Cancel requested; current connector call will finish first"
                self.add_event(job, "cancel_requested")
                self.persist_profile_jobs_locked(job.profile_id)
            return job

    def on_profile_changed(self) -> None:
        with self.lock:
            if self.profile_manager.active_profile_id not in self.loaded_profiles:
                self.load_profile_jobs_locked(self.profile_manager.active_profile_id)
            self.start_worker_locked()

    def start_worker_locked(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.has_runnable_job_locked():
            return
        self.worker = threading.Thread(target=self.worker_loop, name="millie-background-sync", daemon=True)
        self.worker.start()

    def worker_loop(self) -> None:
        while True:
            with self.lock:
                job = self.next_runnable_job_locked()
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
                self.persist_profile_jobs_locked(job.profile_id)
                return
            if job.profile_id != self.profile_manager.active_profile_id:
                job.status = "queued"
                job.message = "Waiting for its profile to become active"
                self.add_event(job, "queued")
                self.persist_profile_jobs_locked(job.profile_id)
                return
            job.status = "running"
            job.started_at = utc_now()
            job.finished_at = None
            job.message = "Running"
            self.add_event(job, "running")
            self.persist_profile_jobs_locked(job.profile_id)
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
                self.persist_profile_jobs_locked(job.profile_id)
            return
        with self.lock:
            job.status = "completed"
            job.finished_at = utc_now()
            job.result = result
            job.message = "Completed"
            self.add_event(job, "completed")
            self.persist_profile_jobs_locked(job.profile_id)

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

    def next_runnable_job_locked(self) -> BackgroundJob | None:
        active_profile_id = self.profile_manager.active_profile_id
        return next(
            (
                self.jobs[job_id]
                for job_id in self.order
                if self.jobs[job_id].status == "queued" and self.jobs[job_id].profile_id == active_profile_id
            ),
            None,
        )

    def has_runnable_job_locked(self) -> bool:
        return self.next_runnable_job_locked() is not None

    def load_jobs_locked(self) -> None:
        self.jobs.clear()
        self.order.clear()
        self.loaded_profiles.clear()
        for profile_id in self.profile_manager.profiles:
            self.load_profile_jobs_locked(profile_id)
        self.order.sort(key=lambda job_id: self.jobs[job_id].queued_at)

    def load_profile_jobs_locked(self, profile_id: str) -> None:
        self.loaded_profiles.add(profile_id)
        raw = self.profile_manager.get_profile_setting(BACKGROUND_JOBS_SETTING, profile_id)
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        records = payload.get("jobs") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            return
        existing_ids = {job_id for job_id in self.order if self.jobs[job_id].profile_id == profile_id}
        for job_id in existing_ids:
            self.jobs.pop(job_id, None)
            self.order.remove(job_id)
        for item in records:
            if not isinstance(item, dict):
                continue
            try:
                job = BackgroundJob.from_record(item, default_profile_id=profile_id)
            except ValueError:
                continue
            if not job.source_id:
                continue
            self.jobs[job.id] = job
            self.order.append(job.id)

    def recover_interrupted_jobs_locked(self) -> None:
        changed_profiles: set[str] = set()
        for job_id in list(self.order):
            job = self.jobs[job_id]
            if job.status != "running":
                continue
            if job.cancel_requested:
                job.status = "cancelled"
                job.finished_at = utc_now()
                job.message = "Cancelled after server restart"
                self.add_event(job, "cancelled")
            else:
                job.status = "queued"
                job.started_at = None
                job.finished_at = None
                job.message = "Re-queued after server restart"
                self.add_event(job, "requeued")
            changed_profiles.add(job.profile_id)
        for profile_id in changed_profiles:
            self.persist_profile_jobs_locked(profile_id)

    def persist_profile_jobs_locked(self, profile_id: str) -> None:
        records = [
            self.jobs[job_id].to_record()
            for job_id in self.order
            if self.jobs[job_id].profile_id == profile_id
        ]
        self.profile_manager.set_profile_setting(
            BACKGROUND_JOBS_SETTING,
            json.dumps({"jobs": records}, indent=2, sort_keys=True),
            profile_id,
        )


def normalize_connector(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"imap", "imap4"}:
        return "imap"
    if normalized in {"pop", "pop3"}:
        return "pop3"
    if normalized in {"graph", "microsoft-graph", "exchange"}:
        return "graph"
    raise ValueError(f"Unsupported sync connector: {value}")


def string_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def positive_int_or_none(value: object) -> int | None:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def dict_or_none(value: object) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


def event_list(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    events: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        at = string_value(item.get("at"))
        status = string_value(item.get("status"))
        if not at or not status:
            continue
        events.append({"at": at, "status": status, "message": string_value(item.get("message")) or ""})
    return events
