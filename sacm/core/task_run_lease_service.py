import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import TaskRunLease


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TaskRunLeaseService:
    def __init__(self, db: Session, lease_seconds: int | None = None) -> None:
        self.db = db
        self.lease_seconds = (
            lease_seconds
            if lease_seconds is not None
            else int(os.getenv("SACM_TASK_RUN_LEASE_SECONDS", "3600"))
        )
        if self.lease_seconds <= 0:
            raise ValueError("Task run lease duration must be positive.")

    def acquire(self, task_id: str, *, now: datetime | None = None) -> str:
        current = now or _utcnow()
        owner_token = str(uuid.uuid4())
        expires_at = current + timedelta(seconds=self.lease_seconds)
        updated = (
            self.db.query(TaskRunLease)
            .filter(
                TaskRunLease.task_id == task_id,
                TaskRunLease.expires_at <= current,
            )
            .update(
                {
                    TaskRunLease.owner_token: owner_token,
                    TaskRunLease.acquired_at: current,
                    TaskRunLease.heartbeat_at: current,
                    TaskRunLease.expires_at: expires_at,
                },
                synchronize_session=False,
            )
        )
        if updated:
            self.db.commit()
            return owner_token

        lease = TaskRunLease(
            task_id=task_id,
            owner_token=owner_token,
            acquired_at=current,
            heartbeat_at=current,
            expires_at=expires_at,
        )
        self.db.add(lease)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise RuntimeError(
                f"Task {task_id} already has an active orchestrator run."
            ) from exc
        return owner_token

    def heartbeat(
        self,
        task_id: str,
        owner_token: str,
        *,
        now: datetime | None = None,
    ) -> None:
        current = now or _utcnow()
        updated = (
            self.db.query(TaskRunLease)
            .filter(
                TaskRunLease.task_id == task_id,
                TaskRunLease.owner_token == owner_token,
                TaskRunLease.expires_at > current,
            )
            .update(
                {
                    TaskRunLease.heartbeat_at: current,
                    TaskRunLease.expires_at: current
                    + timedelta(seconds=self.lease_seconds),
                },
                synchronize_session=False,
            )
        )
        if not updated:
            self.db.rollback()
            raise RuntimeError(f"Task {task_id} orchestrator lease was lost.")
        self.db.commit()

    def release(self, task_id: str, owner_token: str) -> bool:
        if not self.db.is_active:
            self.db.rollback()
        deleted = (
            self.db.query(TaskRunLease)
            .filter(
                TaskRunLease.task_id == task_id,
                TaskRunLease.owner_token == owner_token,
            )
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return bool(deleted)
