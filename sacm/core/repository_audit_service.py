import hashlib
from typing import Any

from sqlalchemy.orm import Session

from sacm.core.event_service import EventService
from sacm.core.memory_service import MemoryService
from sacm.core.task_service import TaskService


class TaskContextError(ValueError):
    pass


class RepositoryAuditService:
    """Persists repository implementation activity in the task context."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def record(
        self,
        task_id: str | None,
        operation: str,
        repository_path: str,
        details: dict[str, Any],
        *,
        memory_summary: str | None = None,
    ) -> None:
        if task_id is None:
            return
        if TaskService(self.db).get(task_id) is None:
            raise TaskContextError(f"Task {task_id} not found.")
        EventService(self.db).save(
            task_id=task_id,
            event_type=f"repository_{operation}",
            payload={
                "operation": operation,
                "repository_path": repository_path,
                **details,
            },
        )
        if memory_summary:
            MemoryService(self.db).add(
                task_id=task_id,
                content=memory_summary,
                source_type="repository_operation",
                importance=0.8,
            )

    @staticmethod
    def content_summary(content: str) -> dict[str, Any]:
        return {
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "size_bytes": len(content.encode()),
        }

    @staticmethod
    def changed_files(diff: str) -> list[str]:
        return sorted(
            {
                line.removeprefix("+++ b/")
                for line in diff.splitlines()
                if line.startswith("+++ b/") and line != "+++ /dev/null"
            }
        )
