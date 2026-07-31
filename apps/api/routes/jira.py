import hashlib
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from apps.api.task_access import authorize_task
from sacm.core.auth_service import require_authenticated_actor
from sacm.core.jira_orchestration_service import JiraOrchestrationService
from sacm.core.jira_service import JiraService
from sacm.core.tenancy_service import AuthorizationError, TenancyService
from sacm.infrastructure.db.models import JiraConnector, JiraDeliveryState
from sacm.infrastructure.db.session import get_db
from sacm.schemas.jira import (
    JiraConnectorCreate,
    JiraConnectorRead,
    JiraDeliveryRead,
    JiraOrchestrationRead,
    JiraOrchestrationRequest,
    JiraWebhookResult,
)

router = APIRouter()


@router.post(
    "/connectors",
    response_model=JiraConnectorRead,
    status_code=201,
)
def configure_connector(
    payload: JiraConnectorCreate,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> JiraConnectorRead:
    try:
        connector = JiraService(db).create_connector(payload, actor=actor)
    except (AuthorizationError, ValueError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return JiraConnectorRead.model_validate(connector)


@router.post(
    "/webhooks/{connector_id}",
    response_model=JiraWebhookResult,
)
async def jira_webhook(
    connector_id: str,
    request: Request,
    x_atlassian_webhook_identifier: str | None = Header(default=None),
    x_sacm_jira_signature: str | None = Header(default=None),
    x_hub_signature: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> JiraWebhookResult:
    connector = db.get(JiraConnector, connector_id)
    if connector is None or not connector.enabled:
        raise HTTPException(status_code=404, detail="Jira connector not found")
    body = await request.body()
    service = JiraService(db)
    signature = x_hub_signature_256 or x_hub_signature or x_sacm_jira_signature
    if not service.verify_signature(connector, body, signature):
        raise HTTPException(status_code=401, detail="Invalid Jira webhook signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid Jira webhook JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid Jira webhook payload")
    delivery_id = x_atlassian_webhook_identifier or hashlib.sha256(body).hexdigest()
    try:
        return service.process_webhook(
            connector, payload, delivery_id=delivery_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/connectors/{connector_id}/tasks/{task_id}/orchestrate",
    response_model=JiraOrchestrationRead,
)
def orchestrate_jira_task(
    connector_id: str,
    task_id: str,
    payload: JiraOrchestrationRequest | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> JiraOrchestrationRead:
    connector = _connector(db, connector_id, actor)
    authorize_task(db, task_id, actor, "developer")
    request = payload or JiraOrchestrationRequest()
    try:
        return JiraOrchestrationService(db).orchestrate(
            connector,
            task_id,
            actor=actor,
            policy_pack=request.policy_pack,
            create_pull_request=request.create_pull_request,
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/connectors/{connector_id}/tasks/{task_id}/finalize",
    response_model=JiraOrchestrationRead,
)
def finalize_jira_task(
    connector_id: str,
    task_id: str,
    payload: JiraOrchestrationRequest | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> JiraOrchestrationRead:
    connector = _connector(db, connector_id, actor)
    authorize_task(db, task_id, actor, "developer")
    try:
        return JiraOrchestrationService(db).finalize(
            connector,
            task_id,
            actor=actor,
            create_pull_request=(payload or JiraOrchestrationRequest()).create_pull_request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/connectors/{connector_id}/tasks/{task_id}",
    response_model=JiraDeliveryRead,
)
def jira_delivery(
    connector_id: str,
    task_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> JiraDeliveryRead:
    _connector(db, connector_id, actor)
    authorize_task(db, task_id, actor)
    state = (
        db.query(JiraDeliveryState)
        .filter(
            JiraDeliveryState.connector_id == connector_id,
            JiraDeliveryState.task_id == task_id,
        )
        .first()
    )
    if state is None:
        raise HTTPException(status_code=404, detail="Jira delivery state not found")
    return JiraDeliveryRead.model_validate(state)


def _connector(db: Session, connector_id: str, actor: str) -> JiraConnector:
    connector = db.get(JiraConnector, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Jira connector not found")
    try:
        TenancyService(db).require_project_permission(
            connector.project_id,
            actor,
            "tasks.read",
            resource_type="jira_connector",
            resource_id=connector.id,
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return connector
