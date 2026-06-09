from datetime import datetime

from pydantic import BaseModel


class TaskStatus:
    PENDING = "pending"
    PLANNING = "planning"
    CODING = "coding"
    TESTING = "testing"
    DEBUGGING = "debugging"
    REVIEWING = "reviewing"
    BLOCKED = "blocked"
    DONE = "done"


class TaskCreate(BaseModel):
    title: str
    description: str
    target_repo_path: str | None = None


class TaskRead(BaseModel):
    id: str
    title: str
    description: str
    status: str
    target_repo_path: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
