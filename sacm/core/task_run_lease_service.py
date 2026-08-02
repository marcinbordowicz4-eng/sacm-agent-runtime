import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from types import TracebackType
from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from sacm.infrastructure.db.models import TaskRunLease


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TaskLeaseHeartbeatError(RuntimeError):
    """Raised when a background lease heartbeat can no longer prove ownership."""


class TaskLeaseHeartbeat:
    def __init__(
        self,
        task_id: str,
        owner_token: str,
        *,
        lease_seconds: int,
        interval_seconds: float,
        session_factory: Callable[[], Session],
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Task run heartbeat interval must be positive.")
        if interval_seconds >= lease_seconds:
            raise ValueError(
                "Task run heartbeat interval must be shorter than the lease duration."
            )
        self.task_id = task_id
        self.owner_token = owner_token
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self.session_factory = session_factory
        self._stop = threading.Event()
        self._failure: BaseException | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"sacm-task-heartbeat-{task_id[:12]}",
            daemon=False,
        )
        self._started = False

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def __enter__(self) -> "TaskLeaseHeartbeat":
        self._thread.start()
        self._started = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            self.stop()
        except BaseException as stop_error:
            if exc is not None:
                exc.add_note(f"Task lease heartbeat shutdown failed: {stop_error}")
                return False
            raise
        if exc is not None:
            if self._failure is not None:
                exc.add_note(f"Task lease heartbeat also failed: {self._failure}")
            return False
        self.check()
        return False

    def check(self) -> None:
        if self._failure is not None:
            raise TaskLeaseHeartbeatError(
                f"Task {self.task_id} lease heartbeat failed; "
                "the blocking result is stale and was not applied."
            ) from self._failure

    def stop(self) -> None:
        self._stop.set()
        if not self._started:
            return
        self._thread.join(timeout=max(1.0, self.interval_seconds * 2))
        if self._thread.is_alive():
            raise TaskLeaseHeartbeatError(
                f"Task {self.task_id} lease heartbeat thread did not stop cleanly."
            )

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            db: Session | None = None
            try:
                db = self.session_factory()
                TaskRunLeaseService(db, lease_seconds=self.lease_seconds).heartbeat(
                    self.task_id, self.owner_token
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

    def guard(
        self,
        task_id: str,
        owner_token: str,
        *,
        interval_seconds: float | None = None,
        session_factory: Callable[[], Session] | None = None,
    ) -> TaskLeaseHeartbeat:
        configured_interval = interval_seconds
        if configured_interval is None:
            raw_interval = os.getenv("SACM_TASK_RUN_HEARTBEAT_SECONDS")
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
        return TaskLeaseHeartbeat(
            task_id,
            owner_token,
            lease_seconds=self.lease_seconds,
            interval_seconds=configured_interval,
            session_factory=factory,
        )

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
