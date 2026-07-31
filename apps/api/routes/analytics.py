from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from sacm.core.analytics_service import AnalyticsNotFoundError, AnalyticsService
from sacm.core.auth_service import production_mode, require_authenticated_actor
from sacm.core.tenancy_service import AuthorizationError, TenancyService
from sacm.infrastructure.db.models import Organization, Project, Run, Task
from sacm.infrastructure.db.session import get_db
from sacm.schemas.analytics import (
    AggregateOutcomeAnalyticsV1,
    RunOutcomeAnalyticsV1,
)

router = APIRouter()


def _authorize_project(
    db: Session, project_id: str, actor: str
) -> Project:
    try:
        return TenancyService(db).require_project_role(
            project_id, actor, "viewer"
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _authorize_run(db: Session, run_id: str, actor: str) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.project_id is None:
        if production_mode():
            raise HTTPException(
                status_code=403,
                detail="Legacy runs without tenancy are unavailable in production.",
            )
        return run
    _authorize_project(db, run.project_id, actor)
    return run


@router.get(
    "/runs/{run_id}/analytics",
    response_model=RunOutcomeAnalyticsV1,
)
def get_run_analytics(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> RunOutcomeAnalyticsV1:
    _authorize_run(db, run_id, actor)
    try:
        return AnalyticsService(db).recompute_run(run_id)
    except AnalyticsNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/analytics/tasks/{task_id}",
    response_model=AggregateOutcomeAnalyticsV1,
)
def get_task_analytics(
    task_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> AggregateOutcomeAnalyticsV1:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    runs = (
        db.query(Run)
        .filter(Run.task_id == task_id)
        .order_by(Run.created_at, Run.id)
        .all()
    )
    project_ids = sorted({run.project_id for run in runs if run.project_id})
    if production_mode() and not project_ids:
        raise HTTPException(
            status_code=403,
            detail="Legacy tasks without tenancy are unavailable in production.",
        )
    for project_id in project_ids:
        _authorize_project(db, project_id, actor)
    return AnalyticsService(db).aggregate(
        "task", task.id, runs, scope_name=task.title
    )


@router.get(
    "/analytics/projects/{project_id}",
    response_model=AggregateOutcomeAnalyticsV1,
)
def get_project_analytics(
    project_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> AggregateOutcomeAnalyticsV1:
    project = _authorize_project(db, project_id, actor)
    runs = (
        db.query(Run)
        .filter(Run.project_id == project.id)
        .order_by(Run.created_at, Run.id)
        .all()
    )
    return AnalyticsService(db).aggregate(
        "project", project.id, runs, scope_name=project.name
    )


@router.get(
    "/analytics/organizations/{organization_id}",
    response_model=AggregateOutcomeAnalyticsV1,
)
def get_organization_analytics(
    organization_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> AggregateOutcomeAnalyticsV1:
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    try:
        TenancyService(db).require_role(organization_id, actor, "viewer")
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    runs = (
        db.query(Run)
        .join(Project, Run.project_id == Project.id)
        .filter(Project.organization_id == organization_id)
        .order_by(Run.created_at, Run.id)
        .all()
    )
    return AnalyticsService(db).aggregate(
        "organization",
        organization.id,
        runs,
        scope_name=organization.name,
    )
