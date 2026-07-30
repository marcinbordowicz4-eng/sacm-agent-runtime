from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sacm.core.auth_service import actor_from_request
from sacm.core.tenancy_service import AuthorizationError, TenancyService
from sacm.infrastructure.db.models import Membership, Organization, Project
from sacm.infrastructure.db.session import get_db

router = APIRouter()


class OrganizationCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{2,63}$")
    name: str = Field(min_length=1, max_length=255)


class ProjectCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{2,63}$")
    name: str = Field(min_length=1, max_length=255)
    repository_full_name: str | None = None
    repository_path: str | None = None


class MembershipUpsert(BaseModel):
    actor_id: str = Field(min_length=1, max_length=255)
    role: Literal["owner", "admin", "developer", "viewer"]


def _actor(actor_id: str | None, authorization: str | None) -> str:
    try:
        return actor_from_request(authorization, actor_id)
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("", status_code=201)
def create_organization(
    payload: OrganizationCreate,
    actor_id: str | None = Header(default=None, alias="X-SACM-Actor"),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    try:
        organization = TenancyService(db).create_organization(
            payload.slug, payload.name, _actor(actor_id, authorization)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _organization(organization)


@router.post("/{organization_id}/projects", status_code=201)
def create_project(
    organization_id: str,
    payload: ProjectCreate,
    actor_id: str | None = Header(default=None, alias="X-SACM-Actor"),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    try:
        project = TenancyService(db).create_project(
            organization_id,
            payload.slug,
            payload.name,
            _actor(actor_id, authorization),
            payload.repository_full_name,
            payload.repository_path,
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
    actor_id: str | None = Header(default=None, alias="X-SACM-Actor"),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    try:
        member = TenancyService(db).add_member(
            organization_id, _actor(actor_id, authorization), payload.actor_id, payload.role
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return _membership(member)


@router.get("/{organization_id}/projects")
def list_projects(
    organization_id: str,
    actor_id: str | None = Header(default=None, alias="X-SACM-Actor"),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> list[dict]:
    service = TenancyService(db)
    try:
        service.require_role(organization_id, _actor(actor_id, authorization), "viewer")
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return [
        _project(project)
        for project in db.query(Project)
        .filter(Project.organization_id == organization_id)
        .order_by(Project.created_at)
    ]


def _organization(organization: Organization) -> dict:
    return {"id": organization.id, "slug": organization.slug, "name": organization.name}


def _project(project: Project) -> dict:
    return {
        "id": project.id,
        "organization_id": project.organization_id,
        "slug": project.slug,
        "name": project.name,
        "repository_full_name": project.repository_full_name,
        "repository_path": project.repository_path,
    }


def _membership(membership: Membership) -> dict:
    return {
        "id": membership.id,
        "organization_id": membership.organization_id,
        "actor_id": membership.actor_id,
        "role": membership.role,
    }
