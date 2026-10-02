"""Asynchronous, idempotent processing for Git-to-cognitive-state ingestion."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from sacm.core.cognitive_state_service import CognitiveStateService
from sacm.infrastructure.db.models import CognitiveCommitJob
from sacm.schemas.cognitive_state import (
    CognitiveEventCreateV1,
    GitCommitAnalyzeV1,
    GitCommitJobV1,
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class CognitiveCommitQueueService:
    def __init__(self, db: Session, *, lease_seconds: int | None = None) -> None:
        self.db = db
        self.lease_seconds = lease_seconds or int(
            os.getenv("SACM_COGNITIVE_COMMIT_LEASE_SECONDS", "300")
        )
        if self.lease_seconds <= 0:
            raise ValueError("Cognitive commit lease duration must be positive.")

    def submit(self, project_id: str, payload: GitCommitAnalyzeV1) -> CognitiveCommitJob:
        existing = (
            self.db.query(CognitiveCommitJob)
            .filter(
                CognitiveCommitJob.project_id == project_id,
                CognitiveCommitJob.commit_hash == payload.commit_hash,
            )
            .first()
        )
        if existing is not None:
            return existing
        event = CognitiveStateService(self.db).record_event(
            project_id,
            CognitiveEventCreateV1(
                event_type="COMMIT_ANALYSIS_QUEUED",
                agent_id=payload.agent_id,
                commit_hash=payload.commit_hash,
                trace_id=payload.trace_id,
                payload={"semantic_summary_provided": payload.semantic_summary is not None},
            ),
            commit=False,
        )
        job = CognitiveCommitJob(
            id=str(uuid.uuid4()),
            project_id=project_id,
            commit_hash=payload.commit_hash,
            agent_id=payload.agent_id,
            trace_id=payload.trace_id,
            semantic_summary=payload.semantic_summary,
            state="QUEUED",
            max_attempts=int(os.getenv("SACM_COGNITIVE_COMMIT_MAX_ATTEMPTS", "3")),
            available_at=event.created_at,
            created_at=event.created_at,
            updated_at=event.created_at,
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def process_one(self) -> dict[str, Any] | None:
        claimed = self._claim()
        if claimed is None:
            return None
        job, token = claimed
        try:
            result = CognitiveStateService(self.db).analyze_and_ingest_git_commit(
                job.project_id,
                GitCommitAnalyzeV1(
                    commit_hash=job.commit_hash,
                    agent_id=job.agent_id,
                    trace_id=job.trace_id,
                    semantic_summary=job.semantic_summary,
                ),
            )
        except Exception as exc:
            self._fail(job.id, token, str(exc))
            return {"job_id": job.id, "status": "FAILED", "error": str(exc)}
        self._complete(job.id, token)
        CognitiveStateService(self.db).record_event(
            job.project_id,
            CognitiveEventCreateV1(
                event_type="COMMIT_ANALYZED",
                agent_id=job.agent_id,
                commit_hash=job.commit_hash,
                trace_id=job.trace_id,
                payload={"job_id": job.id, "snapshot_id": result["snapshot"].id},
            ),
        )
        return {"job_id": job.id, "status": "COMPLETED", "result": result}

    def _claim(self) -> tuple[CognitiveCommitJob, str] | None:
        now = _utcnow()
        self._recover_expired(now)
        query = self.db.query(CognitiveCommitJob).filter(
            CognitiveCommitJob.state == "QUEUED",
            CognitiveCommitJob.available_at <= now,
        )
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        job = query.order_by(CognitiveCommitJob.available_at, CognitiveCommitJob.created_at).first()
        if job is None:
            return None
        token = str(uuid.uuid4())
        job.state = "RUNNING"
        job.attempt += 1
        job.lease_token = token
        job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        job.updated_at = now
        self.db.commit()
        self.db.refresh(job)
        return job, token

    def _recover_expired(self, now: datetime) -> None:
        expired = (
            self.db.query(CognitiveCommitJob)
            .filter(
                CognitiveCommitJob.state == "RUNNING",
                CognitiveCommitJob.lease_expires_at.is_not(None),
                CognitiveCommitJob.lease_expires_at <= now,
            )
            .all()
        )
        for job in expired:
            job.state = "QUEUED" if job.attempt < job.max_attempts else "FAILED"
            job.lease_token = None
            job.lease_expires_at = None
            job.available_at = now
            job.updated_at = now
            if job.state == "FAILED":
                job.completed_at = now
                job.last_error = "Cognitive commit worker lease expired."
        if expired:
            self.db.commit()

    def _complete(self, job_id: str, token: str) -> None:
        job = self._owned(job_id, token)
        job.state = "COMPLETED"
        job.lease_token = None
        job.lease_expires_at = None
        job.completed_at = _utcnow()
        job.updated_at = job.completed_at
        self.db.commit()

    def _fail(self, job_id: str, token: str, error: str) -> None:
        job = self._owned(job_id, token)
        now = _utcnow()
        job.last_error = error[:4_000]
        job.lease_token = None
        job.lease_expires_at = None
        job.updated_at = now
        if job.attempt >= job.max_attempts:
            job.state = "FAILED"
            job.completed_at = now
        else:
            job.state = "QUEUED"
            job.available_at = now + timedelta(seconds=min(300, 2 ** (job.attempt - 1)))
        self.db.commit()

    def _owned(self, job_id: str, token: str) -> CognitiveCommitJob:
        job = self.db.get(CognitiveCommitJob, job_id)
        if job is None or job.state != "RUNNING" or job.lease_token != token:
            raise RuntimeError("Cognitive commit job lease was lost.")
        return job

    @staticmethod
    def read(job: CognitiveCommitJob) -> GitCommitJobV1:
        return GitCommitJobV1(
            id=job.id,
            project_id=job.project_id,
            commit_hash=job.commit_hash,
            agent_id=job.agent_id,
            trace_id=job.trace_id,
            semantic_summary=job.semantic_summary,
            state=job.state,
            attempt=job.attempt,
            max_attempts=job.max_attempts,
            last_error=job.last_error,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )
