from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from sacm.core.agent_registry import AgentRegistry
from sacm.core.auth_service import require_authenticated_actor
from sacm.core.context_briefing_service import ContextBriefingService
from sacm.core.context_compiler import ContextCompiler
from sacm.core.event_service import EventService
from sacm.core.memory_service import MemoryService
from sacm.core.tenancy_service import ResourceAuthorizationService
from sacm.infrastructure.db.session import get_db
from sacm.schemas.context import (
    AgentContext,
    CompileContextRequest,
    IngestContextRequest,
)
from sacm.schemas.memory import MemoryChunkRead

router = APIRouter()


@router.post("/compile", response_model=AgentContext)
def compile_context(
    payload: CompileContextRequest,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> AgentContext:
    try:
        task = ResourceAuthorizationService(db).require_task(
            payload.task_id, actor, "tasks.read"
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    agent = AgentRegistry().get(payload.agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    history = EventService(db).get_recent_events(payload.task_id)
    memory = MemoryService(db).search(
        payload.task_id, task.description, actor_id=actor
    )
    compiler = ContextCompiler(token_budget=payload.token_budget)
    context_package = None
    briefing = None
    if task.target_repo_path:
        generated = ContextBriefingService(db).build(
            task, role=agent.contract_role
        )
        context_package = generated.package
        briefing = generated.metadata
    return compiler.compile(
        task,
        agent,
        history,
        memory,
        context_package=context_package,
        briefing=briefing,
    )


@router.post("/ingest", response_model=MemoryChunkRead)
def ingest_context(
    payload: IngestContextRequest,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> MemoryChunkRead:
    try:
        ResourceAuthorizationService(db).require_task(
            payload.task_id, actor, "tasks.write"
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    chunk = MemoryService(db).add(
        task_id=payload.task_id,
        content=payload.content,
        source_type=payload.source_type,
        importance=0.6,
        actor_id=actor,
    )
    return MemoryChunkRead.model_validate(chunk)
