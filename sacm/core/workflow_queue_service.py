import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from sacm.core.local_workflow import LocalWorkflow
from sacm.core.lifecycle_metric_service import LifecycleMetricService
from sacm.core.run_service import RunService
from sacm.infrastructure.db.models import WorkflowJob


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorkflowQueueService:
    def __init__(self, db: Session) -> None:
        self.db = db

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
        lease_seconds = int(os.getenv("SACM_WORKFLOW_LEASE_SECONDS", "1800"))
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
                    + timedelta(seconds=lease_seconds),
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
            result = LocalWorkflow(self.db).execute(job.run_id)
        except Exception as exc:
            self.fail(job.id, token, str(exc))
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
