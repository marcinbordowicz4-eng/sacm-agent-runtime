from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from sacm.core.event_service import EventService
from sacm.core.memory_service import MemoryService
from sacm.core.orchestrator import Orchestrator
from sacm.core.task_service import TaskService
from sacm.infrastructure.db.models import Artifact
from sacm.infrastructure.db.session import get_db
from sacm.schemas.event import ContextEventRead
from sacm.schemas.memory import MemoryChunkRead
from sacm.schemas.task import TaskCreate, TaskRead

router = APIRouter()


@router.post("", response_model=TaskRead)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)) -> TaskRead:
    task = TaskService(db).create(payload)
    return TaskRead.model_validate(task)


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: str, db: Session = Depends(get_db)) -> TaskRead:
    task = TaskService(db).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskRead.model_validate(task)


@router.post("/{task_id}/run")
def run_task(task_id: str, db: Session = Depends(get_db)) -> dict:
    task = TaskService(db).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return Orchestrator(db).run_task(task_id)


@router.get("/{task_id}/events", response_model=list[ContextEventRead])
def list_events(task_id: str, db: Session = Depends(get_db)) -> list[ContextEventRead]:
    task = TaskService(db).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    events = EventService(db).get_recent_events(task_id, limit=100)
    return [ContextEventRead.model_validate(event) for event in events]


@router.get("/{task_id}/memory", response_model=list[MemoryChunkRead])
def list_memory(task_id: str, db: Session = Depends(get_db)) -> list[MemoryChunkRead]:
    task = TaskService(db).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    chunks = MemoryService(db).search(task_id, query="*", top_k=100)
    return [MemoryChunkRead.model_validate(chunk) for chunk in chunks]


@router.get("/{task_id}/artifacts")
def list_artifacts(task_id: str, db: Session = Depends(get_db)) -> list[dict]:
    task = TaskService(db).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    artifacts = db.query(Artifact).filter(Artifact.task_id == task_id).all()
    return [
        {
            "id": artifact.id,
            "artifact_type": artifact.artifact_type,
            "path": artifact.path,
            "content_hash": artifact.content_hash,
            "metadata": artifact.metadata_,
            "created_at": artifact.created_at,
        }
        for artifact in artifacts
    ]
