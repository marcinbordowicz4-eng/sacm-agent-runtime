import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import ContextEvent


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
        event = ContextEvent(
            id=str(uuid.uuid4()),
            task_id=task_id,
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
