from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.api.task_access import authorize_task
from sacm.core.application_context_service import (
    ApplicationContextError,
    ApplicationContextNotFoundError,
    ApplicationContextService,
)
from sacm.core.auth_service import require_authenticated_actor
from sacm.infrastructure.db.session import get_db
from sacm.schemas.application_context import ApplicationContextRead, ImpactRiskRead

router = APIRouter()


@router.post(
    "/tasks/{task_id}/application-context",
    response_model=ApplicationContextRead,
    status_code=201,
)
def build_application_context(
    task_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> ApplicationContextRead:
    authorize_task(db, task_id, actor, "developer")
    try:
        return ApplicationContextService(db).build(task_id)
    except ApplicationContextNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApplicationContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/tasks/{task_id}/application-context",
    response_model=ApplicationContextRead,
)
def get_application_context(
    task_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> ApplicationContextRead:
    authorize_task(db, task_id, actor)
    context = ApplicationContextService(db).get(task_id)
    if context is None:
        raise HTTPException(status_code=404, detail="Application context not found")
    return context


@router.get(
    "/tasks/{task_id}/application-context/impact-risk",
    response_model=ImpactRiskRead,
)
def get_impact_risk(
    task_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> ImpactRiskRead:
    authorize_task(db, task_id, actor)
    analysis = ApplicationContextService(db).get_impact_risk(task_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Application context not found")
    return analysis
