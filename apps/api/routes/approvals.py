from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sacm.core.auth_service import production_mode, require_authenticated_actor
from sacm.core.policy_service import PolicyService
from sacm.core.tenancy_service import AuthorizationError, Role, TenancyService
from sacm.infrastructure.db.models import Approval, Run
from sacm.infrastructure.db.session import get_db

router = APIRouter()


class ApprovalDecisionRequest(BaseModel):
    approve: bool
    reason: str = Field(min_length=1, max_length=4_000)


@router.get("")
def list_approvals(
    run_id: str | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[dict]:
    return [
        _serialize(approval)
        for approval in PolicyService(db).list_approvals(actor, run_id)
    ]


@router.post("/{approval_id}/decision")
def decide_approval(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        approval_record = db.get(Approval, approval_id)
        if not approval_record:
            raise HTTPException(status_code=404, detail="Approval not found.")
        approval = PolicyService(db).decide(
            approval_id, payload.approve, actor, payload.reason
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize(approval)


def _authorize_approval_run(
    db: Session, run_id: str, actor: str, minimum_role: Role
) -> None:
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.project_id is None:
        if production_mode():
            raise HTTPException(
                status_code=403,
                detail="Production approvals must belong to a project.",
            )
        return
    try:
        TenancyService(db).require_project_role(
            run.project_id, actor, minimum_role
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _serialize(approval: Approval) -> dict:
    return {
        "id": approval.id,
        "run_id": approval.run_id,
        "action": approval.action,
        "resource": approval.resource,
        "status": approval.status,
        "requested_at": approval.requested_at,
        "decided_at": approval.decided_at,
        "decided_by": approval.decided_by,
        "decision_reason": approval.decision_reason,
    }
