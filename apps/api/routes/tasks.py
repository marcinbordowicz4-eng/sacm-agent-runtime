from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.task_access import authorize_task
from sacm.core.auth_service import require_authenticated_actor
from sacm.core.bdd_traceability import BddTraceabilityService
from sacm.core.cost_service import CostService
from sacm.core.event_service import EventService
from sacm.core.memory_service import MemoryService
from sacm.core.run_service import RunService
from sacm.core.task_service import TaskService
from sacm.core.workflow_backend import workflow_backend
from sacm.infrastructure.db.models import Artifact
from sacm.infrastructure.db.session import get_db
from sacm.schemas.event import ContextEventRead
from sacm.schemas.memory import MemoryChunkRead
from sacm.schemas.task import TaskCreate, TaskRead

router = APIRouter()

class BddTaskCreate(TaskCreate):
    jira_key: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9]+-\d+$")


class BusinessImpactRequest(BaseModel):
    base_revision: str = Field(min_length=1)
    target_revision: str = Field(min_length=1)


@router.post("", response_model=TaskRead)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)) -> TaskRead:
    task = TaskService(db).create(payload)
    return TaskRead.model_validate(task)


@router.post("/bdd")
def create_bdd_task(payload: BddTaskCreate, db: Session = Depends(get_db)) -> dict:
    task = TaskService(db).create(TaskCreate(**payload.model_dump(exclude={"jira_key"})))
    requirement = BddTraceabilityService(db).register(task, payload.jira_key)
    return {"task": TaskRead.model_validate(task), "requirement": requirement}


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
    try:
        run = RunService(db).create_for_task(task)
        return workflow_backend(db).execute(run.id)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=409, detail=f"Task execution failed: {exc}"
        ) from exc


@router.get("/{task_id}/events", response_model=list[ContextEventRead])
def list_events(
    task_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[ContextEventRead]:
    authorize_task(db, task_id, actor)
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


@router.get("/{task_id}/costs")
def task_costs(task_id: str, db: Session = Depends(get_db)) -> dict:
    task = TaskService(db).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return CostService(db).summarize_task(task_id)


@router.post("/{task_id}/business-impact")
def business_impact(
    task_id: str, payload: BusinessImpactRequest, db: Session = Depends(get_db)
) -> dict:
    task = TaskService(db).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        return BddTraceabilityService(db).analyze_git_impact(
            task, payload.base_revision, payload.target_revision
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
