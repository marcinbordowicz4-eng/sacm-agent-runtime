from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from sacm.core.auth_service import require_authenticated_actor
from sacm.core.memory_service import MemoryService
from sacm.infrastructure.db.session import get_db
from sacm.schemas.memory import MemoryAddRequest, MemoryChunkRead, MemorySearchRequest

router = APIRouter()


@router.post("/search", response_model=list[MemoryChunkRead])
def search_memory(
    payload: MemorySearchRequest,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[MemoryChunkRead]:
    chunks = MemoryService(db).search(
        payload.task_id,
        payload.query,
        payload.top_k,
        actor,
        scopes=payload.scopes,
    )
    return [MemoryChunkRead.model_validate(chunk) for chunk in chunks]


@router.post("/add", response_model=MemoryChunkRead)
def add_memory(
    payload: MemoryAddRequest,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> MemoryChunkRead:
    chunk = MemoryService(db).add(
        task_id=payload.task_id,
        content=payload.content,
        source_type=payload.source_type,
        importance=payload.importance,
        actor_id=actor,
        source_id=payload.source_id,
        confidence=payload.confidence,
        scope=payload.scope,
        valid_until=payload.valid_until,
        supersedes_id=payload.supersedes_id,
    )
    return MemoryChunkRead.model_validate(chunk)
