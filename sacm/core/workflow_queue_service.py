import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from types import TracebackType
from typing import Any, Callable, Literal

from sqlalchemy.orm import Session, sessionmaker

from sacm.core.lifecycle_metric_service import LifecycleMetricService
from sacm.core.local_workflow import LocalWorkflow
from sacm.core.run_service import RunService
from sacm.infrastructure.db.models import WorkflowJob


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorkflowLeaseHeartbeatError(RuntimeError):
    """Raised when a worker can no longer renew its claimed workflow job."""


class WorkflowLeaseHeartbeat:
    def __init__(
        self,
        job_id: str,
        token: str,
        *,
        lease_seconds: int,
        interval_seconds: float,
        session_factory: Callable[[], Session],
    ) -> None:
        if interval_seconds <= 0 or interval_seconds >= lease_seconds:
            raise ValueError(
                "Workflow lease heartbeat interval must be positive and shorter "
                "than the lease duration."
            )
        self.job_id = job_id
        self.token = token
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self.session_factory = session_factory
        self._stop = threading.Event()
        self._failure: BaseException | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"sacm-workflow-heartbeat-{job_id[:12]}",
            daemon=False,
        )
        self._started = False

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def __enter__(self) -> "WorkflowLeaseHeartbeat":
        self._thread.start()
        self._started = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        self.stop()
        if exc is not None:
            if self._failure is not None:
                exc.add_note(f"Workflow lease heartbeat also failed: {self._failure}")
            return False
        self.check()
        return False

    def stop(self) -> None:
        self._stop.set()
        if not self._started:
            return
        self._thread.join(timeout=max(1.0, self.interval_seconds * 2))
        if self._thread.is_alive():
            raise WorkflowLeaseHeartbeatError(
                f"Workflow job {self.job_id} heartbeat thread did not stop cleanly."
            )

    def check(self) -> None:
        if self._failure is not None:
            raise WorkflowLeaseHeartbeatError(
                f"Workflow job {self.job_id} lease heartbeat failed; "
                "the blocking result was not applied."
            ) from self._failure

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            db: Session | None = None
            try:
                db = self.session_factory()
                WorkflowQueueService(db, lease_seconds=self.lease_seconds).renew(
                    self.job_id, self.token
                )
            except BaseException as exc:
                self._failure = exc
                self._stop.set()
            finally:
                if db is not None:
                    try:
                        db.close()
                    except BaseException as exc:
                        if self._failure is None:
                            self._failure = exc
                        self._stop.set()


class WorkflowQueueService:
    def __init__(self, db: Session, lease_seconds: int | None = None) -> None:
        self.db = db
        self.lease_seconds = (
            lease_seconds
            if lease_seconds is not None
            else int(os.getenv("SACM_WORKFLOW_LEASE_SECONDS", "1800"))
        )
        if self.lease_seconds <= 0:
            raise ValueError("Workflow lease duration must be positive.")

    def submit(self, run_id: str) -> WorkflowJob:
        run = RunService(self.db).get(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found")
        if run.status in {"COMPLETED", "CANCELLED"}:
            raise ValueError(f"Run {run_id} cannot be queued from {run.status}")
        job = (
            self.db.query(WorkflowJob).filter(WorkflowJob.run_id == run_id).first()
        )
        if job is None:
            job = WorkflowJob(
                run_id=run_id,
                max_attempts=int(os.getenv("SACM_WORKFLOW_MAX_ATTEMPTS", "3")),
            )
            self.db.add(job)
        elif job.state in {"QUEUED", "RUNNING"}:
            return job
        else:
            job.state = "QUEUED"
            job.available_at = _utcnow()
            job.lease_token = None
            job.lease_expires_at = None
            job.last_error = None
            job.completed_at = None
        self.db.commit()
        self.db.refresh(job)
        LifecycleMetricService(self.db).record(
            "workflow.queued",
            run_id=run_id,
            details={"job_id": job.id, "attempt": job.attempt},
        )
        return job

    def get_for_run(self, run_id: str) -> WorkflowJob | None:
        return (
            self.db.query(WorkflowJob).filter(WorkflowJob.run_id == run_id).first()
        )

    def cancel(self, run_id: str) -> WorkflowJob | None:
        job = self.get_for_run(run_id)
        if job is None or job.state in {"COMPLETED", "FAILED", "CANCELLED"}:
            return job
        job.state = "CANCELLED"
        job.lease_token = None
        job.lease_expires_at = None
        job.completed_at = _utcnow()
        self.db.commit()
        self.db.refresh(job)
        LifecycleMetricService(self.db).record(
            "workflow.queue_cancelled",
            run_id=run_id,
            details={"job_id": job.id},
        )
        return job

    def claim(self) -> tuple[WorkflowJob, str] | None:
        now = _utcnow()
        self._recover_expired(now)
        candidate = (
            self.db.query(WorkflowJob)
            .filter(
                WorkflowJob.state == "QUEUED",
                WorkflowJob.available_at <= now,
            )
            .order_by(WorkflowJob.available_at, WorkflowJob.created_at)
            .first()
        )
        if candidate is None:
            return None
        token = str(uuid.uuid4())
        updated = (
            self.db.query(WorkflowJob)
            .filter(
                WorkflowJob.id == candidate.id,
                WorkflowJob.state == "QUEUED",
            )
            .update(
                {
                    WorkflowJob.state: "RUNNING",
                    WorkflowJob.attempt: WorkflowJob.attempt + 1,
                    WorkflowJob.lease_token: token,
                    WorkflowJob.lease_expires_at: now
                    + timedelta(seconds=self.lease_seconds),
                    WorkflowJob.started_at: now,
                    WorkflowJob.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        self.db.commit()
        if not updated:
            return None
        job = self.db.get(WorkflowJob, candidate.id)
        if job is not None:
            queue_latency_ms = max(
                0.0, (now - job.available_at).total_seconds() * 1_000
            )
            LifecycleMetricService(self.db).record(
                "workflow.queue_latency_ms",
                run_id=job.run_id,
                value=queue_latency_ms,
                details={"job_id": job.id, "attempt": job.attempt},
            )
        return (job, token) if job is not None else None

    def process_one(self) -> dict[str, Any] | None:
        claimed = self.claim()
        if claimed is None:
            return None
        job, token = claimed
        try:
            with self.lease_guard(job.id, token):
                result = LocalWorkflow(self.db).execute(job.run_id)
        except Exception as exc:
            try:
                self.fail(job.id, token, str(exc))
            except RuntimeError:
                return {
                    "job_id": job.id,
                    "run_id": job.run_id,
                    "status": "LEASE_LOST",
                    "error": str(exc),
                }
            return {
                "job_id": job.id,
                "run_id": job.run_id,
                "status": "FAILED",
                "error": str(exc),
            }
        self.complete(job.id, token)
        return {
            "job_id": job.id,
            "run_id": job.run_id,
            "status": "COMPLETED",
            "result": result,
        }

    def renew(self, job_id: str, token: str) -> None:
        now = _utcnow()
        updated = (
            self.db.query(WorkflowJob)
            .filter(
                WorkflowJob.id == job_id,
                WorkflowJob.state == "RUNNING",
                WorkflowJob.lease_token == token,
                WorkflowJob.lease_expires_at > now,
            )
            .update(
                {
                    WorkflowJob.lease_expires_at: now
                    + timedelta(seconds=self.lease_seconds),
                    WorkflowJob.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if not updated:
            self.db.rollback()
            raise RuntimeError("Workflow job lease was lost.")
        self.db.commit()

    def lease_guard(
        self,
        job_id: str,
        token: str,
        *,
        interval_seconds: float | None = None,
        session_factory: Callable[[], Session] | None = None,
    ) -> WorkflowLeaseHeartbeat:
        configured_interval = interval_seconds
        if configured_interval is None:
            raw_interval = os.getenv("SACM_WORKFLOW_LEASE_HEARTBEAT_SECONDS")
            configured_interval = (
                float(raw_interval)
                if raw_interval is not None
                else min(30.0, self.lease_seconds / 3)
            )
        factory = session_factory or sessionmaker(
            bind=self.db.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        )
        return WorkflowLeaseHeartbeat(
            job_id,
            token,
            lease_seconds=self.lease_seconds,
            interval_seconds=configured_interval,
            session_factory=factory,
        )

    def complete(self, job_id: str, token: str) -> None:
        self._finish(job_id, token, state="COMPLETED")

    def fail(self, job_id: str, token: str, error: str) -> None:
        job = self._require_owned(job_id, token)
        now = _utcnow()
        job.last_error = error[:4_000]
        job.lease_token = None
        job.lease_expires_at = None
        if job.attempt < job.max_attempts:
            job.state = "QUEUED"
            delay = min(300, 2 ** max(0, job.attempt - 1))
            job.available_at = now + timedelta(seconds=delay)
            metric = "workflow.retry_scheduled"
        else:
            job.state = "FAILED"
            job.completed_at = now
            run = RunService(self.db).get(job.run_id)
            if run and run.status not in {"FAILED", "COMPLETED", "CANCELLED"}:
                RunService(self.db).transition(
                    run.id,
                    "FAILED",
                    "WorkflowJobFailed",
                    payload={"job_id": job.id, "error": job.last_error},
                )
            metric = "workflow.failed"
        self.db.commit()
        LifecycleMetricService(self.db).record(
            metric,
            run_id=job.run_id,
            details={"job_id": job.id, "attempt": job.attempt},
        )

    def _finish(self, job_id: str, token: str, *, state: str) -> None:
        job = self._require_owned(job_id, token)
        job.state = state
        job.lease_token = None
        job.lease_expires_at = None
        job.completed_at = _utcnow()
        self.db.commit()
        LifecycleMetricService(self.db).record(
            "workflow.completed",
            run_id=job.run_id,
            details={"job_id": job.id, "attempt": job.attempt},
        )

    def _require_owned(self, job_id: str, token: str) -> WorkflowJob:
        job = self.db.get(WorkflowJob, job_id)
        if job is None or job.state != "RUNNING" or job.lease_token != token:
            raise RuntimeError("Workflow job lease was lost.")
        return job

    def _recover_expired(self, now: datetime) -> None:
        expired = (
            self.db.query(WorkflowJob)
            .filter(
                WorkflowJob.state == "RUNNING",
                WorkflowJob.lease_expires_at <= now,
            )
            .all()
        )
        for job in expired:
            job.lease_token = None
            job.lease_expires_at = None
            job.last_error = "Workflow worker lease expired."
            if job.attempt < job.max_attempts:
                job.state = "QUEUED"
                job.available_at = now
            else:
                job.state = "FAILED"
                job.completed_at = now
        if expired:
            self.db.commit()
            metrics = LifecycleMetricService(self.db)
            for job in expired:
                metrics.record(
                    "workflow.lease_expired",
                    run_id=job.run_id,
                    details={
                        "job_id": job.id,
                        "attempt": job.attempt,
                        "requeued": job.state == "QUEUED",
                    },
                )
