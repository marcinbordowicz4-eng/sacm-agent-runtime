from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sacm.core.auth_service import production_mode, require_authenticated_actor
from sacm.core.tenancy_service import (
    AuthorizationError,
    ServiceCredentialService,
    TenancyService,
    TenantAuditService,
    TenantBackfillService,
)
from sacm.infrastructure.db.models import (
    Membership,
    Organization,
    Project,
    ServiceCredential,
    TenantAuditEvent,
)
from sacm.infrastructure.db.session import get_db

router = APIRouter()


class OrganizationCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{2,63}$")
    name: str = Field(min_length=1, max_length=255)
    data_region: str | None = Field(default=None, min_length=1, max_length=100)
    data_classification: Literal[
        "Public", "Internal", "Confidential", "Restricted"
    ] | None = None


class ProjectCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{2,63}$")
    name: str = Field(min_length=1, max_length=255)
    repository_full_name: str | None = None
    repository_path: str | None = None
    data_region: str | None = Field(default=None, min_length=1, max_length=100)
    data_classification: Literal[
        "Public", "Internal", "Confidential", "Restricted"
    ] | None = None


class MembershipUpsert(BaseModel):
    actor_id: str = Field(min_length=1, max_length=255)
    role: Literal["owner", "admin", "developer", "viewer"]
    permissions: list[str] | None = None


class ServiceCredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    project_id: str | None = None
    role: Literal["owner", "admin", "developer", "viewer"] = "viewer"
    permissions: list[str] = Field(default_factory=list)
    expires_in_seconds: int | None = Field(default=None, ge=60, le=31_536_000)


@router.get("")
def list_organizations(
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = db.query(Organization)
    if production_mode():
        if actor.startswith("service:"):
            credential_id = actor.removeprefix("service:")
            query = query.join(ServiceCredential).filter(
                ServiceCredential.id == credential_id,
                ServiceCredential.revoked_at.is_(None),
            )
        else:
            query = query.join(Membership).filter(Membership.actor_id == actor)
    return [_organization(organization) for organization in query.order_by(Organization.name)]


@router.post("", status_code=201)
def create_organization(
    payload: OrganizationCreate,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        organization = TenancyService(db).create_organization(
            payload.slug,
            payload.name,
            actor,
            payload.data_region,
            payload.data_classification,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _organization(organization)


@router.post("/{organization_id}/projects", status_code=201)
def create_project(
    organization_id: str,
    payload: ProjectCreate,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        project = TenancyService(db).create_project(
            organization_id,
            payload.slug,
            payload.name,
            actor,
            payload.repository_full_name,
            payload.repository_path,
            payload.data_region,
            payload.data_classification,
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _project(project)


@router.put("/{organization_id}/members")
def upsert_membership(
    organization_id: str,
    payload: MembershipUpsert,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        member = TenancyService(db).add_member(
            organization_id,
            actor,
            payload.actor_id,
            payload.role,
            payload.permissions,
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return _membership(member)


@router.get("/{organization_id}/projects")
def list_projects(
    organization_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[dict]:
    service = TenancyService(db)
    try:
        service.require_permission(organization_id, actor, "tasks.read")
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return [
        _project(project)
        for project in db.query(Project)
        .filter(Project.organization_id == organization_id)
        .order_by(Project.created_at)
    ]


@router.post("/{organization_id}/service-credentials", status_code=201)
def create_service_credential(
    organization_id: str,
    payload: ServiceCredentialCreate,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        issued = ServiceCredentialService(db).create(
            organization_id=organization_id,
            actor_id=actor,
            name=payload.name,
            project_id=payload.project_id,
            role=payload.role,
            permissions=payload.permissions,
            expires_in_seconds=payload.expires_in_seconds,
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        status_code = 409 if "already exists" in str(exc) else 404
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {**_service_credential(issued.record), "token": issued.token}


@router.get("/{organization_id}/service-credentials")
def list_service_credentials(
    organization_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[dict]:
    try:
        records = ServiceCredentialService(db).list(organization_id, actor)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return [_service_credential(record) for record in records]


@router.post("/{organization_id}/service-credentials/{credential_id}/revoke")
def revoke_service_credential(
    organization_id: str,
    credential_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        record = ServiceCredentialService(db).revoke(
            organization_id, credential_id, actor
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _service_credential(record)


@router.get("/{organization_id}/audit-events")
def query_audit_events(
    organization_id: str,
    project_id: str | None = None,
    action: str | None = None,
    decision: Literal["ALLOW", "DENY"] | None = None,
    limit: int = 200,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[dict]:
    try:
        events = TenantAuditService(db).query(
            organization_id,
            actor,
            project_id=project_id,
            action=action,
            decision=decision,
            limit=limit,
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return [_audit_event(event) for event in events]


@router.post("/{organization_id}/tenant-backfill")
def backfill_tenant_context(
    organization_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        TenancyService(db).require_permission(
            organization_id,
            actor,
            "data.manage",
            resource_type="tenant_backfill",
            resource_id=organization_id,
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    report = TenantBackfillService(db).run(organization_id)
    TenancyService(db).audit_sensitive(
        organization_id,
        None,
        actor,
        "tenant.backfill",
        "organization",
        organization_id,
        "Tenant attribution backfill executed.",
        {"updated": report["updated"], "unresolved_count": len(report["unresolved"])},
    )
    return report


def _organization(organization: Organization) -> dict:
    return {
        "id": organization.id,
        "slug": organization.slug,
        "name": organization.name,
        "data_region": organization.data_region,
        "data_classification": organization.data_classification,
    }


def _project(project: Project) -> dict:
    return {
        "id": project.id,
        "organization_id": project.organization_id,
        "slug": project.slug,
        "name": project.name,
        "repository_full_name": project.repository_full_name,
        "repository_path": project.repository_path,
        "data_region": project.data_region,
        "data_classification": project.data_classification,
    }


def _membership(membership: Membership) -> dict:
    return {
        "id": membership.id,
        "organization_id": membership.organization_id,
        "actor_id": membership.actor_id,
        "role": membership.role,
        "permissions": membership.permissions,
    }


def _service_credential(credential: ServiceCredential) -> dict:
    return {
        "id": credential.id,
        "organization_id": credential.organization_id,
        "project_id": credential.project_id,
        "name": credential.name,
        "token_prefix": credential.token_prefix,
        "role": credential.role,
        "permissions": credential.permissions,
        "expires_at": credential.expires_at,
        "revoked_at": credential.revoked_at,
        "last_used_at": credential.last_used_at,
        "created_by": credential.created_by,
        "created_at": credential.created_at,
    }


def _audit_event(event: TenantAuditEvent) -> dict:
    return {
        "id": event.id,
        "organization_id": event.organization_id,
        "project_id": event.project_id,
        "sequence": event.sequence,
        "actor_id": event.actor_id,
        "actor_type": event.actor_type,
        "service_credential_id": event.service_credential_id,
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "decision": event.decision,
        "reason": event.reason,
        "correlation_id": event.correlation_id,
        "request_metadata": event.request_metadata,
        "previous_event_hash": event.previous_event_hash,
        "event_hash": event.event_hash,
        "created_at": event.created_at,
    }
