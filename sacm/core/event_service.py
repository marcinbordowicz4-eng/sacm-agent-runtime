import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from sacm.core.tenancy_service import ResourceAuthorizationService
from sacm.infrastructure.db.models import ContextEvent, Task


class EventService:
    def __init__(self, db: Session):
        self.db = db

    def save(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
        agent_id: Optional[str] = None,
    ) -> ContextEvent:
        task = self.db.get(Task, task_id)
        context = (
            ResourceAuthorizationService(self.db).task_context(task) if task else None
        )
        event = ContextEvent(
            id=str(uuid.uuid4()),
            task_id=task_id,
            organization_id=context.organization_id if context else None,
            project_id=context.project_id if context else None,
            tenant_attribution=(
                {"schema_version": "tenant-attribution/v1", "source": context.source}
                if context
                else None
            ),
            data_region=task.data_region if task else None,
            data_classification=task.data_classification if task else None,
            agent_id=agent_id,
            event_type=event_type,
            payload=payload,
            created_at=datetime.utcnow(),
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def get_recent_events(self, task_id: str, limit: int = 20) -> list[ContextEvent]:
        return (
            self.db.query(ContextEvent)
            .filter(ContextEvent.task_id == task_id)
            .order_by(ContextEvent.created_at.desc())
            .limit(limit)
            .all()
        )

    def save_agent_result(
        self,
        task_id: str,
        agent_name: str,
        result: Any,
        *,
        task_contract: Any | None = None,
        result_contract: Any | None = None,
    ) -> None:
        self.save(
            task_id=task_id,
            event_type="agent_result",
            payload={
                "agent_name": agent_name,
                "summary": result.summary,
                "confidence": result.confidence,
                "next_state_hint": result.next_state_hint,
                "actions": result.actions,
                "usage": [
                    artifact
                    for artifact in result.artifacts
                    if artifact.get("type") == "usage"
                ],
                "tool_execution": [
                    artifact
                    for artifact in result.artifacts
                    if artifact.get("type") == "tool_execution"
                ],
                **(
                    {
                        "agent_task_contract": task_contract.model_dump(
                            mode="json"
                        ),
                        "agent_result_contract": result_contract.model_dump(
                            mode="json"
                        ),
                    }
                    if task_contract is not None and result_contract is not None
                    else {}
                ),
            },
        )
