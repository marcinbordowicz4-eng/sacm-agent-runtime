from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from sacm.core.agent_registry import AgentRegistry
from sacm.core.context_compiler import ContextCompiler
from sacm.core.event_service import EventService
from sacm.core.memory_service import MemoryService
from sacm.core.task_service import TaskService
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
    payload: CompileContextRequest, db: Session = Depends(get_db)
) -> AgentContext:
    task = TaskService(db).get(payload.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    agent = AgentRegistry().get(payload.agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    history = EventService(db).get_recent_events(payload.task_id)
    memory = MemoryService(db).search(payload.task_id, task.description)
    compiler = ContextCompiler(token_budget=payload.token_budget)
    return compiler.compile(task, agent, history, memory)


@router.post("/ingest", response_model=MemoryChunkRead)
def ingest_context(
    payload: IngestContextRequest, db: Session = Depends(get_db)
) -> MemoryChunkRead:
    task = TaskService(db).get(payload.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    chunk = MemoryService(db).add(
        task_id=payload.task_id,
        content=payload.content,
        source_type=payload.source_type,
        importance=0.6,
    )
    return MemoryChunkRead.model_validate(chunk)
