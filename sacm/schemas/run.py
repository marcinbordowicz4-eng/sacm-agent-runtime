from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

RunStatus = Literal[
    "CREATED",
    "PLANNING",
    "AWAITING_APPROVAL",
    "IMPLEMENTING",
    "REVIEWING",
    "FIXING",
    "TESTING",
    "DELIVERING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
]


class RunCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1)
    target_repo_path: str | None = None
    source_revision: str | None = None
    project_id: str | None = None


class RunRead(BaseModel):
    id: str
    task_id: str
    project_id: str | None
    status: RunStatus
    workflow_version: str
    source_revision: str | None
    target_repo_path: str | None
    cancellation_requested: bool
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class RunStepRead(BaseModel):
    id: str
    sequence: int
    name: str
    status: str
    idempotency_key: str
    retry_count: int
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}
