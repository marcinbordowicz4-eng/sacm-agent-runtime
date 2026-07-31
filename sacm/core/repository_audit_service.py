import hashlib
from typing import Any

from sqlalchemy.orm import Session

from sacm.core.event_service import EventService
from sacm.core.memory_service import MemoryService
from sacm.core.task_service import TaskService
from sacm.core.tenancy_service import ResourceAuthorizationService, TenancyService


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
        actor_id: str | None = None,
        permission: str = "tasks.write",
    ) -> None:
        if task_id is None:
            return
        if TaskService(self.db).get(task_id) is None:
            raise TaskContextError(f"Task {task_id} not found.")
        task = self.authorize(task_id, repository_path, actor_id, permission)
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
                actor_id=actor_id,
            )
        from sacm.core.traceability_service import TraceabilityService

        TraceabilityService(self.db).refresh(task_id)
        context = ResourceAuthorizationService(self.db).task_context(task)
        if context and actor_id:
            TenancyService(self.db).audit_sensitive(
                context.organization_id,
                context.project_id,
                actor_id,
                f"repository.{operation}",
                "task",
                task_id,
                "Repository operation recorded.",
                {"repository_path": repository_path},
            )

    def authorize(
        self,
        task_id: str | None,
        repository_path: str,
        actor_id: str | None,
        permission: str,
    ):
        resources = ResourceAuthorizationService(self.db)
        if actor_id is None:
            if resources._production():
                raise PermissionError("Authenticated tenant context is required.")
            return TaskService(self.db).get(task_id) if task_id else None
        return resources.require_repository(
            task_id, repository_path, actor_id, permission
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
