from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from apps.api.task_access import authorize_task
from sacm.core.auth_service import require_authenticated_actor
from sacm.core.workflow_progress_service import WorkflowProgressService
from sacm.infrastructure.db.session import get_db
from sacm.schemas.progress import WorkflowProgressV1

router = APIRouter()


@router.get("/{task_id}/progress", response_model=WorkflowProgressV1)
def get_task_progress(
    task_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> WorkflowProgressV1:
    authorize_task(db, task_id, actor)
    return WorkflowProgressService(db).get(task_id, limit=limit)
