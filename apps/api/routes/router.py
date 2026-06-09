from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sacm.core.embedding_service import EmbeddingService
from sacm.core.event_service import EventService
from sacm.core.memory_service import MemoryService
from sacm.core.router import RouterService
from sacm.core.state_service import StateService
from sacm.core.task_service import TaskService
from sacm.infrastructure.db.session import get_db

router = APIRouter()


class RouteRequest(BaseModel):
    task_id: str
    context_text: Optional[str] = None


@router.post("/route")
def route_task(payload: RouteRequest, db: Session = Depends(get_db)) -> dict:
    task = TaskService(db).get(payload.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    history = EventService(db).get_recent_events(payload.task_id)
    memory = MemoryService(db).search(payload.task_id, payload.context_text or task.description)
    vector = EmbeddingService().embed_task_context(task, history, memory)
    belief = StateService(db).get_belief_state(payload.task_id)
    return RouterService().route(vector, belief)
