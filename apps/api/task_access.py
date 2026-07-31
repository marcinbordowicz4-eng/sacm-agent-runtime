from fastapi import HTTPException
from sqlalchemy.orm import Session

from sacm.core.auth_service import production_mode
from sacm.core.tenancy_service import (
    AuthorizationError,
    ResourceAuthorizationService,
    Role,
    TenancyService,
)
from sacm.infrastructure.db.models import Project, Run, Task
from sacm.schemas.task import TaskContractV1


def authorize_task(
    db: Session,
    task_id: str,
    actor: str,
    minimum_role: Role = "viewer",
) -> Task:
    permission = "tasks.read" if minimum_role == "viewer" else "tasks.write"
    try:
        return ResourceAuthorizationService(db).require_task(
            task_id, actor, permission
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def authorize_contract(
    db: Session,
    contract: TaskContractV1,
    actor: str,
) -> None:
    projects = _contract_projects(db, contract)
    if not projects:
        if production_mode():
            raise HTTPException(
                status_code=422,
                detail="Production task contracts must map to a SACM project.",
            )
        return
    tenancy = TenancyService(db)
    try:
        for project in projects:
            tenancy.require_project_permission(
                project.id,
                actor,
                "tasks.write",
                resource_type="task_contract",
                resource_id=contract.external_id,
            )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _task_projects(db: Session, task: Task) -> list[Project]:
    project_ids = {
        project_id
        for (project_id,) in db.query(Run.project_id)
        .filter(Run.task_id == task.id, Run.project_id.is_not(None))
        .all()
        if project_id
    }
    contract_data = task.task_contract
    if isinstance(contract_data, dict):
        try:
            contract = TaskContractV1.model_validate(contract_data)
        except ValueError:
            contract = None
        if contract is not None:
            project_ids.update(project.id for project in _contract_projects(db, contract))
    if not project_ids:
        return []
    return (
        db.query(Project)
        .filter(Project.id.in_(sorted(project_ids)))
        .order_by(Project.id)
        .all()
    )


def _contract_projects(db: Session, contract: TaskContractV1) -> list[Project]:
    projects: dict[str, Project] = {}
    if contract.project_id:
        project = db.get(Project, contract.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        projects[project.id] = project
    for reference in contract.repositories:
        query = db.query(Project)
        if reference.full_name:
            project = query.filter(
                Project.repository_full_name == reference.full_name
            ).first()
        elif reference.path:
            project = query.filter(Project.repository_path == reference.path).first()
        else:
            project = None
        if project is not None:
            projects[project.id] = project
    return [projects[key] for key in sorted(projects)]


def _authorize_projects(
    db: Session,
    projects: list[Project],
    actor: str,
    minimum_role: Role,
) -> None:
    tenancy = TenancyService(db)
    try:
        for project in projects:
            tenancy.require_project_role(project.id, actor, minimum_role)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
