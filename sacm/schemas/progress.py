from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class WorkflowProgressEntryV1(BaseModel):
    event_id: str
    phase: str
    status: str
    agent: str | None = None
    step: int | None = None
    elapsed_ms: int
    created_at: datetime
    details: dict[str, Any]


class WorkflowProgressV1(BaseModel):
    schema_version: Literal["workflow-progress-status/v1"] = (
        "workflow-progress-status/v1"
    )
    task_id: str
    task_status: str
    state: Literal["running", "stalled", "finished", "failed"]
    lease_active: bool
    phase: str | None = None
    agent: str | None = None
    step: int | None = None
    elapsed_ms: int
    last_update: datetime | None = None
    entries: list[WorkflowProgressEntryV1]
