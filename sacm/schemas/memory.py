from datetime import datetime

from pydantic import BaseModel


class MemoryChunkCreate(BaseModel):
    task_id: str
    source_type: str
    source_id: str | None = None
    content: str
    importance: float = 0.5


class MemoryChunkRead(BaseModel):
    id: str
    task_id: str
    source_type: str
    content: str
    importance: float
    created_at: datetime

    model_config = {"from_attributes": True}


class MemorySearchRequest(BaseModel):
    task_id: str
    query: str
    top_k: int = 8


class MemoryAddRequest(BaseModel):
    task_id: str
    content: str
    source_type: str = "manual"
    importance: float = 0.5
