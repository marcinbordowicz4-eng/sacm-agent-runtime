from datetime import datetime

from pydantic import BaseModel


class AgentCreate(BaseModel):
    name: str
    role: str
    provider: str
    model_name: str
    cost_weight: float = 1.0
    quality_score: float = 0.5
    latency_score: float = 0.5


class AgentRead(BaseModel):
    id: str
    name: str
    role: str
    provider: str
    model_name: str
    is_active: bool
    cost_weight: float
    quality_score: float
    latency_score: float
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentUpdate(BaseModel):
    is_active: bool | None = None
    cost_weight: float | None = None
    quality_score: float | None = None
    latency_score: float | None = None
