from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.api.task_access import authorize_task
from sacm.core.auth_service import require_authenticated_actor
from sacm.core.execution_planning_service import (
    ApplicationContextRequiredError,
    DefinitionOfReadyError,
    ExecutionPlanningError,
    ExecutionPlanningNotFoundError,
    ExecutionPlanningService,
)
from sacm.infrastructure.db.session import get_db
from sacm.schemas.execution_plan import (
    ExecutionPlanBuildRequest,
    ExecutionPlanPolicyRead,
    ExecutionPlanSecretsRead,
    ExecutionPlanV1,
    SecurityReviewV1,
)

router = APIRouter()


@router.post(
    "/tasks/{task_id}/execution-plan",
    response_model=ExecutionPlanV1,
    status_code=201,
)
def build_execution_plan(
    task_id: str,
    payload: ExecutionPlanBuildRequest | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> ExecutionPlanV1:
    authorize_task(db, task_id, actor, "developer")
    try:
        return ExecutionPlanningService(db).build(
            task_id,
            policy_pack=(payload or ExecutionPlanBuildRequest()).policy_pack,
        )
    except ExecutionPlanningNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (DefinitionOfReadyError, ApplicationContextRequiredError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExecutionPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/tasks/{task_id}/execution-plan",
    response_model=ExecutionPlanV1,
)
def get_execution_plan(
    task_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> ExecutionPlanV1:
    authorize_task(db, task_id, actor)
    plan = ExecutionPlanningService(db).get(task_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Execution plan not found")
    return plan


@router.get(
    "/tasks/{task_id}/execution-plan/policy",
    response_model=ExecutionPlanPolicyRead,
)
def get_execution_plan_policy(
    task_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> ExecutionPlanPolicyRead:
    authorize_task(db, task_id, actor)
    policy = ExecutionPlanningService(db).get_policy(task_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Execution plan not found")
    return policy


@router.get(
    "/tasks/{task_id}/execution-plan/security-review",
    response_model=SecurityReviewV1,
)
def get_execution_plan_security_review(
    task_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> SecurityReviewV1:
    authorize_task(db, task_id, actor)
    review = ExecutionPlanningService(db).get_security_review(task_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Execution plan not found")
    return review


@router.get(
    "/tasks/{task_id}/execution-plan/secret-requirements",
    response_model=ExecutionPlanSecretsRead,
)
def get_execution_plan_secret_requirements(
    task_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> ExecutionPlanSecretsRead:
    authorize_task(db, task_id, actor)
    requirements = ExecutionPlanningService(db).get_secret_requirements(task_id)
    if requirements is None:
        raise HTTPException(status_code=404, detail="Execution plan not found")
    return requirements
