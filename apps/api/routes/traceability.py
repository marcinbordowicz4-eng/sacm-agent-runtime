from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.api.task_access import authorize_task
from sacm.core.auth_service import require_authenticated_actor
from sacm.core.traceability_service import (
    TraceabilityError,
    TraceabilityNotFoundError,
    TraceabilityService,
)
from sacm.infrastructure.db.session import get_db
from sacm.schemas.traceability import (
    RequirementLinkCreateV1,
    RequirementLinkV1,
    RequirementV1,
    TraceabilityV1,
)

router = APIRouter()


@router.get(
    "/tasks/{task_id}/requirements",
    response_model=list[RequirementV1],
)
def list_requirements(
    task_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[RequirementV1]:
    authorize_task(db, task_id, actor)
    try:
        return TraceabilityService(db).refresh(task_id).requirements
    except TraceabilityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TraceabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/tasks/{task_id}/requirements/refresh",
    response_model=list[RequirementV1],
)
def refresh_requirements(
    task_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[RequirementV1]:
    authorize_task(db, task_id, actor, "developer")
    return list_requirements(task_id, actor, db)


@router.get(
    "/tasks/{task_id}/traceability",
    response_model=TraceabilityV1,
)
def get_traceability(
    task_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> TraceabilityV1:
    authorize_task(db, task_id, actor)
    try:
        return TraceabilityService(db).refresh(task_id)
    except TraceabilityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TraceabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/tasks/{task_id}/traceability/refresh",
    response_model=TraceabilityV1,
)
def refresh_traceability(
    task_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> TraceabilityV1:
    authorize_task(db, task_id, actor, "developer")
    return get_traceability(task_id, actor, db)


@router.post(
    "/tasks/{task_id}/traceability/links",
    response_model=RequirementLinkV1,
    status_code=201,
)
def submit_traceability_link(
    task_id: str,
    payload: RequirementLinkCreateV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> RequirementLinkV1:
    authorize_task(db, task_id, actor, "developer")
    try:
        return TraceabilityService(db).submit_link(
            task_id, payload, actor=actor
        )
    except TraceabilityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TraceabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
