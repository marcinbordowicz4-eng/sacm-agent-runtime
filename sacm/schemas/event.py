from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ContextEventCreate(BaseModel):
    task_id: str
    agent_id: str | None = None
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ContextEventRead(BaseModel):
    id: str
    task_id: str
    agent_id: str | None
    event_type: str
    payload: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}
