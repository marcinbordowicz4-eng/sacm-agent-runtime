from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sacm.core.auth_service import require_authenticated_actor
from sacm.core.embedding_service import EmbeddingService
from sacm.core.event_service import EventService
from sacm.core.memory_service import MemoryService
from sacm.core.outcome_router_service import OutcomeRouterService
from sacm.core.router import RouterService
from sacm.core.state_service import StateService
from sacm.core.tenancy_service import ResourceAuthorizationService
from sacm.infrastructure.db.session import get_db
from sacm.schemas.router import RouterDecisionV1, RouterRankRequestV1

router = APIRouter()


class RouteRequest(BaseModel):
    task_id: str
    context_text: Optional[str] = None


@router.post("/route")
def route_task(
    payload: RouteRequest,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        task = ResourceAuthorizationService(db).require_task(
            payload.task_id, actor, "tasks.read"
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Task not found") from None

    history = EventService(db).get_recent_events(payload.task_id)
    memory = MemoryService(db).search(
        payload.task_id,
        payload.context_text or task.description,
        actor_id=actor,
    )
    vector = EmbeddingService().embed_task_context(task, history, memory)
    belief = StateService(db).get_belief_state(payload.task_id)
    return RouterService().route(vector, belief)


@router.post("/rank", response_model=RouterDecisionV1)
def rank_task(
    payload: RouterRankRequestV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> RouterDecisionV1:
    try:
        task = ResourceAuthorizationService(db).require_task(
            payload.task_id, actor, "tasks.read"
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    history = EventService(db).get_recent_events(payload.task_id)
    memory = MemoryService(db).search(
        payload.task_id, task.description, actor_id=actor
    )
    vector = EmbeddingService().embed_task_context(task, history, memory)
    belief = StateService(db).get_belief_state(payload.task_id)
    return OutcomeRouterService(db).rank(
        task,
        vector,
        belief,
        role=payload.role,
        risk_level=payload.risk_level,
        cost_budget_usd=payload.cost_budget_usd,
        latency_budget_ms=payload.latency_budget_ms,
        previous_failure_classification=payload.previous_failure_classification,
    )
