from datetime import datetime

from typing import Literal

from pydantic import BaseModel, Field

MemoryScope = Literal["task", "project", "repository", "organization"]


class MemoryChunkCreate(BaseModel):
    task_id: str
    source_type: str
    source_id: str | None = None
    content: str
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0.7, ge=0, le=1)
    scope: MemoryScope = "task"
    valid_until: datetime | None = None
    supersedes_id: str | None = None


class MemoryChunkRead(BaseModel):
    id: str
    task_id: str
    source_type: str
    source_id: str | None = None
    scope: MemoryScope
    scope_key: str
    content: str
    content_hash: str
    importance: float
    confidence: float
    valid_until: datetime | None
    supersedes_id: str | None
    superseded_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MemorySearchRequest(BaseModel):
    task_id: str
    query: str
    top_k: int = 8
    scopes: list[MemoryScope] | None = None


class MemoryAddRequest(BaseModel):
    task_id: str
    content: str
    source_type: str = "manual"
    source_id: str | None = None
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0.7, ge=0, le=1)
    scope: MemoryScope = "task"
    valid_until: datetime | None = None
    supersedes_id: str | None = None
