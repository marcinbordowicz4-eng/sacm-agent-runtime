import uuid
from typing import Literal

from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import Membership, Organization, Project

Role = Literal["owner", "admin", "developer", "viewer"]
_ROLE_RANK: dict[str, int] = {"viewer": 0, "developer": 1, "admin": 2, "owner": 3}


class AuthorizationError(PermissionError):
    pass


class TenancyService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_organization(self, slug: str, name: str, owner_id: str) -> Organization:
        if self.db.query(Organization).filter(Organization.slug == slug).first():
            raise ValueError(f"Organization {slug} already exists.")
        organization = Organization(id=str(uuid.uuid4()), slug=slug, name=name)
        membership = Membership(
            id=str(uuid.uuid4()),
            organization=organization,
            actor_id=owner_id,
            role="owner",
        )
        self.db.add_all([organization, membership])
        self.db.commit()
        self.db.refresh(organization)
        return organization

    def create_project(
        self,
        organization_id: str,
        slug: str,
        name: str,
        actor_id: str,
        repository_full_name: str | None = None,
        repository_path: str | None = None,
    ) -> Project:
        self.require_role(organization_id, actor_id, "admin")
        project = Project(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            slug=slug,
            name=name,
            repository_full_name=repository_full_name,
            repository_path=repository_path,
        )
        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        return project

    def add_member(
        self, organization_id: str, actor_id: str, member_id: str, role: Role
    ) -> Membership:
        self.require_role(organization_id, actor_id, "admin")
        member = (
            self.db.query(Membership)
            .filter(
                Membership.organization_id == organization_id,
                Membership.actor_id == member_id,
            )
            .first()
        )
        if member:
            member.role = role
        else:
            member = Membership(
                id=str(uuid.uuid4()),
                organization_id=organization_id,
                actor_id=member_id,
                role=role,
            )
            self.db.add(member)
        self.db.commit()
        self.db.refresh(member)
        return member

    def require_project_role(
        self, project_id: str, actor_id: str, minimum_role: Role
    ) -> Project:
        project = self.db.get(Project, project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found.")
        self.require_role(project.organization_id, actor_id, minimum_role)
        return project

    def require_role(
        self, organization_id: str, actor_id: str, minimum_role: Role
    ) -> Membership:
        membership = (
            self.db.query(Membership)
            .filter(
                Membership.organization_id == organization_id,
                Membership.actor_id == actor_id,
            )
            .first()
        )
        if not membership or _ROLE_RANK.get(membership.role, -1) < _ROLE_RANK[minimum_role]:
            raise AuthorizationError("Insufficient organization role.")
        return membership
