from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from sacm.core.auth_service import require_authenticated_actor
from sacm.core.governance_service import (
    AuditExportService,
    GovernancePolicyService,
    GovernanceRequestService,
    ResidencyService,
    SIEMService,
    governance_health,
)
from sacm.core.tenancy_service import AuthorizationError
from sacm.infrastructure.db.models import (
    AuditExportBatch,
    DataGovernancePolicy,
    GovernanceRequest,
    GovernanceRequestItem,
    SIEMDelivery,
    SIEMSink,
)
from sacm.infrastructure.db.session import get_db
from sacm.schemas.governance import (
    AuditExportCreate,
    GovernanceApproval,
    GovernanceLegalHoldCreate,
    GovernancePolicyCreate,
    GovernanceProcessRequest,
    GovernanceRequestCreate,
    SIEMDrainRequest,
    SIEMSinkCreate,
    SIEMSinkUpdate,
)

router = APIRouter(dependencies=[Depends(require_authenticated_actor)])


def _error(exc: Exception) -> NoReturn:
    if isinstance(exc, (AuthorizationError, PermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if "not found" in str(exc).lower():
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{organization_id}/governance/policies", status_code=201)
def create_policy(
    organization_id: str,
    payload: GovernancePolicyCreate,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        policy = GovernancePolicyService(db).create(organization_id, payload, actor)
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return _policy(db, policy)


@router.get("/{organization_id}/governance/policies")
def list_policies(
    organization_id: str,
    project_id: str | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    try:
        service = GovernancePolicyService(db)
        return [
            _policy(db, policy)
            for policy in service.list_policies(organization_id, actor, project_id)
        ]
    except AuthorizationError as exc:
        _error(exc)


@router.post("/{organization_id}/governance/policies/{policy_id}/activate")
def activate_policy(
    organization_id: str,
    policy_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        policy = GovernancePolicyService(db).activate(
            organization_id, policy_id, actor
        )
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return _policy(db, policy)


@router.get("/{organization_id}/governance/policies/{policy_id}")
def get_policy(
    organization_id: str,
    policy_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        policy = GovernancePolicyService(db).get(
            organization_id, policy_id, actor
        )
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return _policy(db, policy)


@router.post("/{organization_id}/governance/policies/{policy_id}/retire")
def retire_policy(
    organization_id: str,
    policy_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        policy = GovernancePolicyService(db).retire(
            organization_id, policy_id, actor
        )
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return _policy(db, policy)


@router.post("/{organization_id}/governance/metadata/backfill")
def backfill_metadata(
    organization_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        return ResidencyService(db).backfill(organization_id, actor)
    except (AuthorizationError, ValueError) as exc:
        _error(exc)


@router.post("/{organization_id}/governance/requests", status_code=201)
def create_request(
    organization_id: str,
    payload: GovernanceRequestCreate,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        request = GovernanceRequestService(db).create(
            organization_id, payload, actor
        )
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return _request(db, request)


@router.get("/{organization_id}/governance/requests")
def list_requests(
    organization_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    try:
        return [
            _request(db, request)
            for request in GovernanceRequestService(db).list_requests(
                organization_id, actor
            )
        ]
    except AuthorizationError as exc:
        _error(exc)


@router.get("/{organization_id}/governance/requests/{request_id}")
def get_request(
    organization_id: str,
    request_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        request = GovernanceRequestService(db).get(
            organization_id, request_id, actor
        )
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return _request(db, request)


@router.post("/{organization_id}/governance/requests/{request_id}/approval")
def approve_request(
    organization_id: str,
    request_id: str,
    payload: GovernanceApproval,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        request = GovernanceRequestService(db).approve(
            organization_id,
            request_id,
            actor,
            approved=payload.approved,
            reason=payload.reason,
        )
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return _request(db, request)


@router.post("/{organization_id}/governance/requests/{request_id}/inventory")
def inventory_request(
    organization_id: str,
    request_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        request = GovernanceRequestService(db).inventory(
            organization_id, request_id, actor
        )
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return _request(db, request)


@router.post("/{organization_id}/governance/requests/{request_id}/process")
def process_request(
    organization_id: str,
    request_id: str,
    payload: GovernanceProcessRequest,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        request = GovernanceRequestService(db).process(
            organization_id,
            request_id,
            actor,
            batch_size=payload.batch_size,
        )
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return _request(db, request)


@router.post("/{organization_id}/governance/legal-holds", status_code=201)
def create_legal_hold(
    organization_id: str,
    payload: GovernanceLegalHoldCreate,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        hold = GovernanceRequestService(db).create_hold(
            organization_id, payload, actor
        )
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return _columns(hold)


@router.post("/{organization_id}/governance/legal-holds/{hold_id}/release")
def release_legal_hold(
    organization_id: str,
    hold_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        hold = GovernanceRequestService(db).release_hold(
            organization_id, hold_id, actor
        )
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return _columns(hold)


@router.post("/{organization_id}/audit-exports", status_code=201)
def create_audit_export(
    organization_id: str,
    payload: AuditExportCreate,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        batch = AuditExportService(db).create(organization_id, payload, actor)
    except (AuthorizationError, ValueError, RuntimeError) as exc:
        _error(exc)
    return _audit_export(batch, include_manifest=False)


@router.get("/{organization_id}/audit-exports")
def list_audit_exports(
    organization_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    try:
        return [
            _audit_export(batch, include_manifest=False)
            for batch in AuditExportService(db).list(organization_id, actor)
        ]
    except AuthorizationError as exc:
        _error(exc)


@router.get("/{organization_id}/audit-exports/{batch_id}/manifest")
def audit_export_manifest(
    organization_id: str,
    batch_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        batch = AuditExportService(db).get(organization_id, batch_id, actor)
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return _audit_export(batch, include_manifest=True)


@router.get("/{organization_id}/audit-exports/{batch_id}/download")
def download_audit_export(
    organization_id: str,
    batch_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> JSONResponse:
    try:
        batch = AuditExportService(db).get(organization_id, batch_id, actor)
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return JSONResponse(
        content=jsonable_encoder(_audit_export(batch, include_manifest=True)),
        headers={
            "Content-Disposition": f'attachment; filename="audit-export-{batch.id}.json"'
        },
    )


@router.post("/{organization_id}/audit-exports/{batch_id}/verify")
def verify_audit_export(
    organization_id: str,
    batch_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        batch = AuditExportService(db).get(organization_id, batch_id, actor)
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return AuditExportService.verify(batch)


@router.post("/{organization_id}/siem/sinks", status_code=201)
def create_siem_sink(
    organization_id: str,
    payload: SIEMSinkCreate,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        sink = SIEMService(db).create(organization_id, payload, actor)
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return _sink(sink)


@router.get("/{organization_id}/siem/sinks")
def list_siem_sinks(
    organization_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    try:
        return [
            _sink(sink)
            for sink in SIEMService(db).list_sinks(organization_id, actor)
        ]
    except AuthorizationError as exc:
        _error(exc)


@router.patch("/{organization_id}/siem/sinks/{sink_id}")
def update_siem_sink(
    organization_id: str,
    sink_id: str,
    payload: SIEMSinkUpdate,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        sink = SIEMService(db).update(organization_id, sink_id, payload, actor)
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return _sink(sink)


@router.post("/{organization_id}/siem/sinks/{sink_id}/drain")
def drain_siem_sink(
    organization_id: str,
    sink_id: str,
    payload: SIEMDrainRequest,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        return SIEMService(db).drain(
            organization_id, sink_id, actor, limit=payload.limit
        )
    except (AuthorizationError, ValueError) as exc:
        _error(exc)


@router.get("/{organization_id}/siem/sinks/{sink_id}/deliveries")
def list_siem_deliveries(
    organization_id: str,
    sink_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    try:
        return [
            _delivery(delivery)
            for delivery in SIEMService(db).deliveries(
                organization_id, sink_id, actor
            )
        ]
    except (AuthorizationError, ValueError) as exc:
        _error(exc)


@router.post("/{organization_id}/siem/deliveries/{delivery_id}/retry")
def retry_siem_delivery(
    organization_id: str,
    delivery_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        delivery = SIEMService(db).retry_dead_letter(
            organization_id, delivery_id, actor
        )
    except (AuthorizationError, ValueError) as exc:
        _error(exc)
    return _delivery(delivery)


@router.get("/{organization_id}/governance/health")
def get_governance_health(
    organization_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        GovernanceRequestService(db).list_requests(organization_id, actor)
    except AuthorizationError as exc:
        _error(exc)
    return governance_health(db, organization_id)


def _columns(record: Any) -> dict[str, Any]:
    return {
        column.name: getattr(record, column.name)
        for column in record.__table__.columns
    }


def _policy(db: Session, policy: DataGovernancePolicy) -> dict[str, Any]:
    service = GovernancePolicyService(db)
    return {
        **_columns(policy),
        "rules": [
            {
                **_columns(rule),
                "metadata": rule.metadata_,
            }
            for rule in service.rules(policy.id)
        ],
    }


def _request(db: Session, request: GovernanceRequest) -> dict[str, Any]:
    items = (
        db.query(GovernanceRequestItem)
        .filter(GovernanceRequestItem.request_id == request.id)
        .order_by(GovernanceRequestItem.position)
        .all()
    )
    return {**_columns(request), "items": [_columns(item) for item in items]}


def _audit_export(
    batch: AuditExportBatch, *, include_manifest: bool
) -> dict[str, Any]:
    result = _columns(batch)
    if not include_manifest:
        result.pop("canonical_manifest", None)
    return result


def _sink(sink: SIEMSink) -> dict[str, Any]:
    return {
        "id": sink.id,
        "organization_id": sink.organization_id,
        "project_id": sink.project_id,
        "name": sink.name,
        "sink_type": sink.sink_type,
        "status": sink.status,
        "endpoint": sink.endpoint,
        "allowed_hosts": sink.allowed_hosts,
        "storage_metadata": sink.storage_metadata,
        "credential_reference_configured": bool(sink.credential_reference_hash),
        "signing_reference_configured": bool(sink.signing_reference_hash),
        "cursor_sequence": sink.cursor_sequence,
        "batch_size": sink.batch_size,
        "max_attempts": sink.max_attempts,
        "backoff_seconds": sink.backoff_seconds,
        "last_success_at": sink.last_success_at,
        "last_failure_at": sink.last_failure_at,
        "last_error_code": sink.last_error_code,
        "created_at": sink.created_at,
        "updated_at": sink.updated_at,
    }


def _delivery(delivery: SIEMDelivery) -> dict[str, Any]:
    return {
        "id": delivery.id,
        "sink_id": delivery.sink_id,
        "organization_id": delivery.organization_id,
        "first_sequence": delivery.first_sequence,
        "last_sequence": delivery.last_sequence,
        "event_count": delivery.event_count,
        "idempotency_key": delivery.idempotency_key,
        "payload_checksum": delivery.payload_checksum,
        "status": delivery.status,
        "attempts": delivery.attempts,
        "next_attempt_at": delivery.next_attempt_at,
        "response_metadata": delivery.response_metadata,
        "error_code": delivery.error_code,
        "created_at": delivery.created_at,
        "delivered_at": delivery.delivered_at,
    }
