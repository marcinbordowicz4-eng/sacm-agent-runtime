from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import ContextEvent, Task, TaskRunLease
from sacm.schemas.progress import WorkflowProgressEntryV1, WorkflowProgressV1

_STANDARD_FIELDS = {
    "schema_version",
    "task_id",
    "run_id",
    "phase",
    "status",
    "task_status",
    "agent",
    "step",
    "elapsed_ms",
}
_SAFE_DETAIL_FIELDS = {"error_type", "verification_complete"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorkflowProgressService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, task_id: str, *, limit: int = 20) -> WorkflowProgressV1:
        task = self.db.get(Task, task_id)
        if task is None:
            raise ValueError("Task not found.")
        bounded_limit = min(max(limit, 1), 100)
        events = (
            self.db.query(ContextEvent)
            .filter(
                ContextEvent.task_id == task_id,
                ContextEvent.event_type == "workflow_progress",
            )
            .order_by(ContextEvent.created_at.desc(), ContextEvent.id.desc())
            .limit(max(100, bounded_limit))
            .all()
        )
        now = _utcnow()
        lease_active = (
            self.db.query(TaskRunLease.task_id)
            .filter(
                TaskRunLease.task_id == task_id,
                TaskRunLease.expires_at > now,
            )
            .first()
            is not None
        )
        latest = events[0] if events else None
        payload = latest.payload if latest else {}
        state = self._state(task.status, payload.get("status"), lease_active)
        elapsed_ms = self._elapsed_ms(events, state, now)
        entries = [self._entry(event) for event in events[:bounded_limit]]
        return WorkflowProgressV1(
            task_id=task.id,
            task_status=task.status,
            state=state,
            lease_active=lease_active,
            phase=self._text(payload.get("phase")),
            agent=self._text(payload.get("agent")),
            step=self._integer(payload.get("step")),
            elapsed_ms=elapsed_ms,
            last_update=latest.created_at if latest else None,
            entries=entries,
        )

    @staticmethod
    def _state(
        task_status: str,
        progress_status: object,
        lease_active: bool,
    ) -> Literal["running", "stalled", "finished", "failed", "cancelled"]:
        normalized_task = task_status.lower()
        normalized_progress = str(progress_status or "").lower()
        if normalized_progress == "cancelled" or normalized_task == "cancelled":
            return "cancelled"
        if normalized_progress == "failed" or normalized_task in {
            "failed",
            "error",
            "blocked",
        }:
            return "failed"
        if normalized_progress == "finished" or normalized_task in {
            "done",
            "completed",
        }:
            return "finished"
        if lease_active:
            return "running"
        return "stalled"

    @staticmethod
    def _elapsed_ms(events: list[ContextEvent], state: str, now: datetime) -> int:
        if not events:
            return 0
        latest = events[0]
        latest_elapsed = (
            WorkflowProgressService._integer(latest.payload.get("elapsed_ms")) or 0
        )
        if state not in {"running", "stalled"}:
            return max(0, latest_elapsed)
        latest_run_id = latest.payload.get("run_id")
        for event in events:
            if (
                latest_run_id is not None
                and event.payload.get("run_id") != latest_run_id
            ):
                continue
            if event.payload.get("status") == "started":
                return max(0, int((now - event.created_at).total_seconds() * 1_000))
        return max(
            0,
            latest_elapsed + int((now - latest.created_at).total_seconds() * 1_000),
        )

    @staticmethod
    def _entry(event: ContextEvent) -> WorkflowProgressEntryV1:
        payload = event.payload
        return WorkflowProgressEntryV1(
            event_id=event.id,
            phase=WorkflowProgressService._text(payload.get("phase")) or "unknown",
            status=WorkflowProgressService._text(payload.get("status")) or "unknown",
            agent=WorkflowProgressService._text(payload.get("agent")),
            step=WorkflowProgressService._integer(payload.get("step")),
            elapsed_ms=WorkflowProgressService._integer(payload.get("elapsed_ms")) or 0,
            created_at=event.created_at,
            details={
                key: value
                for key, value in payload.items()
                if key not in _STANDARD_FIELDS and key in _SAFE_DETAIL_FIELDS
            },
        )

    @staticmethod
    def _text(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _integer(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None
