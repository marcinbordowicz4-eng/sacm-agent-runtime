from typing import Any

from sqlalchemy.orm import Session

from sacm.core.cost_service import CostService
from sacm.infrastructure.db.models import ContextEvent, Run


class RunContextService:
    """Builds the operational context displayed for a durable run."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def build(self, run: Run) -> dict[str, Any]:
        task = run.task
        project = run.project
        organization = project.organization if project else None
        agent_events = (
            self.db.query(ContextEvent)
            .filter(
                ContextEvent.task_id == task.id,
                ContextEvent.event_type == "agent_result",
            )
            .order_by(ContextEvent.created_at)
            .all()
        )
        return {
            "run": {
                "id": run.id,
                "workflow_version": run.workflow_version,
                "source_revision": run.source_revision,
                "target_repo_path": run.target_repo_path,
            },
            "task": {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "status": task.status,
                "target_repo_path": task.target_repo_path,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
            },
            "organization": (
                {
                    "id": organization.id,
                    "slug": organization.slug,
                    "name": organization.name,
                }
                if organization
                else None
            ),
            "project": (
                {
                    "id": project.id,
                    "slug": project.slug,
                    "name": project.name,
                    "repository_full_name": project.repository_full_name,
                    "repository_path": project.repository_path,
                }
                if project
                else None
            ),
            "agents": [self._agent_invocation(event) for event in agent_events],
            "costs": CostService(self.db).summarize_task(task.id),
        }

    @staticmethod
    def _agent_invocation(event: ContextEvent) -> dict[str, Any]:
        payload = event.payload
        task_contract = payload.get("agent_task_contract") or {}
        result_contract = payload.get("agent_result_contract") or {}
        return {
            "event_id": event.id,
            "name": payload.get("agent_name") or "unknown-agent",
            "role": task_contract.get("role"),
            "status": result_contract.get("status"),
            "summary": payload.get("summary"),
            "confidence": payload.get("confidence"),
            "next_state_hint": payload.get("next_state_hint"),
            "usage": payload.get("usage", []),
            "tool_execution": payload.get("tool_execution", []),
            "created_at": event.created_at,
        }
