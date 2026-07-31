from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.api.task_access import authorize_contract, authorize_task
from sacm.core.auth_service import require_authenticated_actor
from sacm.core.task_intake_service import TaskIntakeService
from sacm.infrastructure.db.models import TaskClarification
from sacm.infrastructure.db.session import get_db
from sacm.schemas.task import (
    ClarificationAnswer,
    JiraWebhook,
    TaskClarificationRead,
    TaskContractV1,
    TaskIntakeRead,
    TaskRead,
)

router = APIRouter()


def _response(task, readiness, clarifications) -> TaskIntakeRead:
    return TaskIntakeRead(
        task=TaskRead.model_validate(task),
        readiness=readiness,
        clarifications=[
            TaskClarificationRead.model_validate(item) for item in clarifications
        ],
    )


@router.post("/tasks", response_model=TaskIntakeRead)
def ingest_task(
    payload: TaskContractV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> TaskIntakeRead:
    authorize_contract(db, payload, actor)
    result = TaskIntakeService(db).ingest(payload)
    authorize_task(db, result[0].id, actor)
    return _response(*result)


@router.post("/jira/webhooks", response_model=TaskIntakeRead)
def ingest_jira_webhook(
    payload: JiraWebhook,
    project_id: str | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> TaskIntakeRead:
    service = TaskIntakeService(db)
    contract = service.from_jira(payload, project_id)
    authorize_contract(db, contract, actor)
    result = service.ingest(contract)
    authorize_task(db, result[0].id, actor)
    return _response(*result)


@router.get(
    "/tasks/{task_id}/clarifications",
    response_model=list[TaskClarificationRead],
)
def list_clarifications(
    task_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[TaskClarificationRead]:
    authorize_task(db, task_id, actor)
    items = (
        db.query(TaskClarification)
        .filter(TaskClarification.task_id == task_id)
        .order_by(TaskClarification.created_at)
        .all()
    )
    return [TaskClarificationRead.model_validate(item) for item in items]


@router.post(
    "/tasks/{task_id}/clarifications/{clarification_id}/answer",
    response_model=TaskIntakeRead,
)
def answer_clarification(
    task_id: str,
    clarification_id: str,
    payload: ClarificationAnswer,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> TaskIntakeRead:
    authorize_task(db, task_id, actor, "developer")
    try:
        result = TaskIntakeService(db).answer(
            task_id, clarification_id, payload.answer
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Clarification not found")
    return _response(*result)
