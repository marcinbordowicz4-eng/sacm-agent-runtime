import hashlib
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from sacm.core.auth_service import current_auth_context, hash_service_token
from sacm.infrastructure.db.models import (
    Approval,
    Artifact,
    ContextEvent,
    EvidencePack,
    ExecutionJob,
    ExecutionPlan,
    ExecutorEnrollmentToken,
    ExecutorRegistration,
    Membership,
    MemoryChunk,
    Organization,
    Project,
    Run,
    RunReplay,
    RunSnapshot,
    ServiceCredential,
    Task,
    TenantAuditEvent,
)

Role = Literal["owner", "admin", "developer", "viewer"]
Permission = Literal[
    "runs.read",
    "runs.write",
    "runs.execute",
    "tasks.read",
    "tasks.write",
    "evidence.read",
    "evidence.build",
    "approvals.read",
    "approvals.decide",
    "executors.manage",
    "executors.use",
    "secrets.read",
    "secrets.manage",
    "audit.export",
    "data.manage",
    "resilience.read",
    "resilience.manage",
    "operations.manage",
]

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        "runs.read",
        "runs.write",
        "runs.execute",
        "tasks.read",
        "tasks.write",
        "evidence.read",
        "evidence.build",
        "approvals.read",
        "approvals.decide",
        "executors.manage",
        "executors.use",
        "secrets.read",
        "secrets.manage",
        "audit.export",
        "data.manage",
        "resilience.read",
        "resilience.manage",
        "operations.manage",
    }
)
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset(
        {
            "runs.read",
            "tasks.read",
            "evidence.read",
            "approvals.read",
            "resilience.read",
        }
    ),
    "developer": frozenset(
        {
            "runs.read",
            "runs.write",
            "runs.execute",
            "tasks.read",
            "tasks.write",
            "evidence.read",
            "evidence.build",
            "approvals.read",
            "executors.use",
            "resilience.read",
        }
    ),
    "admin": ALL_PERMISSIONS,
    "owner": ALL_PERMISSIONS,
}
_ROLE_RANK: dict[str, int] = {"viewer": 0, "developer": 1, "admin": 2, "owner": 3}
_SENSITIVE_KEYS = {
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuthorizationError(PermissionError):
    pass


@dataclass(frozen=True)
class TenantContext:
    organization_id: str
    project_id: str | None
    source: str


@dataclass(frozen=True)
class IssuedServiceCredential:
    record: ServiceCredential
    token: str


class TenantAuditService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record(
        self,
        *,
        organization_id: str,
        project_id: str | None,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str | None,
        decision: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> TenantAuditEvent:
        organization_query = self.db.query(Organization).filter(
            Organization.id == organization_id
        )
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            organization_query = organization_query.with_for_update()
        organization = organization_query.first()
        if organization is None:
            raise ValueError("Organization not found.")
        project = self.db.get(Project, project_id) if project_id else None
        previous = (
            self.db.query(TenantAuditEvent)
            .filter(TenantAuditEvent.organization_id == organization_id)
            .order_by(TenantAuditEvent.sequence.desc())
            .first()
        )
        sequence = previous.sequence + 1 if previous else 1
        created_at = _utcnow()
        auth_context = current_auth_context()
        actor_type = (
            auth_context.actor_type
            if auth_context and auth_context.actor_id == actor_id
            else ("service" if actor_id.startswith("service:") else "user")
        )
        credential_id = (
            auth_context.service_credential_id
            if auth_context and auth_context.actor_id == actor_id
            else (
                actor_id.removeprefix("service:")
                if actor_id.startswith("service:")
                else None
            )
        )
        correlation_id = auth_context.correlation_id if auth_context else None
        safe_metadata = self._sanitize(metadata or {})
        canonical = {
            "organization_id": organization_id,
            "project_id": project_id,
            "sequence": sequence,
            "actor_id": actor_id,
            "actor_type": actor_type,
            "service_credential_id": credential_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "decision": decision,
            "reason": reason,
            "correlation_id": correlation_id,
            "request_metadata": safe_metadata,
            "previous_event_hash": previous.event_hash if previous else None,
            "created_at": created_at.isoformat(timespec="microseconds"),
        }
        event_hash = hashlib.sha256(
            json.dumps(
                canonical, sort_keys=True, separators=(",", ":"), default=str
            ).encode()
        ).hexdigest()
        canonical["created_at"] = created_at
        event = TenantAuditEvent(
            id=str(uuid.uuid4()),
            **canonical,
            event_hash=event_hash,
            data_region=(
                project.data_region
                if project and project.data_region
                else organization.data_region
            ),
            data_classification=(
                project.data_classification
                if project and project.data_classification
                else organization.data_classification or "Confidential"
            ),
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        from sacm.core.governance_service import SIEMService

        SIEMService.mark_pending(self.db, event)
        return event

    def query(
        self,
        organization_id: str,
        actor_id: str,
        *,
        project_id: str | None = None,
        action: str | None = None,
        decision: str | None = None,
        limit: int = 200,
    ) -> list[TenantAuditEvent]:
        TenancyService(self.db).require_permission(
            organization_id,
            actor_id,
            "audit.export",
            project_id=project_id,
            resource_type="tenant_audit",
            resource_id=organization_id,
        )
        query = self.db.query(TenantAuditEvent).filter(
            TenantAuditEvent.organization_id == organization_id
        )
        if project_id:
            query = query.filter(TenantAuditEvent.project_id == project_id)
        if action:
            query = query.filter(TenantAuditEvent.action == action)
        if decision:
            query = query.filter(TenantAuditEvent.decision == decision)
        return query.order_by(TenantAuditEvent.sequence.desc()).limit(min(limit, 1000)).all()

    @classmethod
    def _sanitize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): (
                    "[REDACTED]"
                    if any(part in str(key).lower() for part in _SENSITIVE_KEYS)
                    else cls._sanitize(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._sanitize(item) for item in value]
        if isinstance(value, str) and value.startswith("sacm_service_"):
            return "[REDACTED]"
        return value


class TenancyService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_organization(
        self,
        slug: str,
        name: str,
        owner_id: str,
        data_region: str | None = None,
        data_classification: str | None = None,
    ) -> Organization:
        if self.db.query(Organization).filter(Organization.slug == slug).first():
            raise ValueError(f"Organization {slug} already exists.")
        organization = Organization(
            id=str(uuid.uuid4()),
            slug=slug,
            name=name,
            data_region=data_region,
            data_classification=data_classification,
        )
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
        data_region: str | None = None,
        data_classification: str | None = None,
    ) -> Project:
        self.require_permission(
            organization_id,
            actor_id,
            "data.manage",
            resource_type="project",
            resource_id=slug,
        )
        organization = self.db.get(Organization, organization_id)
        if organization is None:
            raise ValueError("Organization not found.")
        project = Project(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            slug=slug,
            name=name,
            repository_full_name=repository_full_name,
            repository_path=repository_path,
            data_region=data_region or organization.data_region,
            data_classification=(
                data_classification or organization.data_classification
            ),
        )
        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        self.audit_sensitive(
            organization_id,
            project.id,
            actor_id,
            "project.create",
            "project",
            project.id,
            "Project created.",
        )
        return project

    def add_member(
        self,
        organization_id: str,
        actor_id: str,
        member_id: str,
        role: Role,
        permissions: list[str] | None = None,
    ) -> Membership:
        self.require_permission(
            organization_id,
            actor_id,
            "data.manage",
            resource_type="membership",
            resource_id=member_id,
        )
        self._validate_permissions(permissions or [])
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
            member.permissions = sorted(set(permissions)) if permissions else None
        else:
            member = Membership(
                id=str(uuid.uuid4()),
                organization_id=organization_id,
                actor_id=member_id,
                role=role,
                permissions=sorted(set(permissions)) if permissions else None,
            )
            self.db.add(member)
        self.db.commit()
        self.db.refresh(member)
        self.audit_sensitive(
            organization_id,
            None,
            actor_id,
            "membership.upsert",
            "membership",
            member.id,
            "Membership role or permissions changed.",
        )
        return member

    def require_project_permission(
        self,
        project_id: str,
        actor_id: str,
        permission: Permission | str,
        *,
        resource_type: str = "project",
        resource_id: str | None = None,
    ) -> Project:
        project = self.db.get(Project, project_id)
        if not project:
            raise ValueError("Project not found.")
        self.require_permission(
            project.organization_id,
            actor_id,
            permission,
            project_id=project.id,
            resource_type=resource_type,
            resource_id=resource_id or project.id,
        )
        return project

    def require_project_role(
        self, project_id: str, actor_id: str, minimum_role: Role
    ) -> Project:
        project = self.db.get(Project, project_id)
        if not project:
            raise ValueError("Project not found.")
        self.require_role(project.organization_id, actor_id, minimum_role)
        return project

    def require_permission(
        self,
        organization_id: str,
        actor_id: str,
        permission: Permission | str,
        *,
        project_id: str | None = None,
        resource_type: str = "organization",
        resource_id: str | None = None,
    ) -> Membership | ServiceCredential:
        principal = self._principal(organization_id, actor_id)
        allowed = False
        reason = "Actor is not a member of the organization."
        if isinstance(principal, ServiceCredential):
            scope_matches = principal.project_id is None or principal.project_id == project_id
            allowed = scope_matches and permission in self._effective_permissions(principal)
            reason = (
                "Allowed by scoped service credential."
                if allowed
                else "Service credential scope or permission does not allow this action."
            )
        elif principal is not None:
            allowed = permission in self._effective_permissions(principal)
            reason = (
                "Allowed by organization membership."
                if allowed
                else "Organization role does not grant the required permission."
            )
        TenantAuditService(self.db).record(
            organization_id=organization_id,
            project_id=project_id,
            actor_id=actor_id,
            action=f"authorization.{permission}",
            resource_type=resource_type,
            resource_id=resource_id,
            decision="ALLOW" if allowed else "DENY",
            reason=reason,
        )
        if not allowed or principal is None:
            raise AuthorizationError("Resource is not accessible.")
        return principal

    def require_role(
        self, organization_id: str, actor_id: str, minimum_role: Role
    ) -> Membership | ServiceCredential:
        principal = self._principal(organization_id, actor_id)
        allowed = (
            principal is not None
            and _ROLE_RANK.get(principal.role, -1) >= _ROLE_RANK[minimum_role]
        )
        TenantAuditService(self.db).record(
            organization_id=organization_id,
            project_id=(
                principal.project_id
                if isinstance(principal, ServiceCredential)
                else None
            ),
            actor_id=actor_id,
            action=f"authorization.role.{minimum_role}",
            resource_type="organization",
            resource_id=organization_id,
            decision="ALLOW" if allowed else "DENY",
            reason=(
                "Role requirement satisfied."
                if allowed
                else "Organization role requirement was not satisfied."
            ),
        )
        if not allowed or principal is None:
            raise AuthorizationError("Insufficient organization role.")
        return principal

    def audit_sensitive(
        self,
        organization_id: str,
        project_id: str | None,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str | None,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> TenantAuditEvent:
        return TenantAuditService(self.db).record(
            organization_id=organization_id,
            project_id=project_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            decision="ALLOW",
            reason=reason,
            metadata=metadata,
        )

    def _principal(
        self, organization_id: str, actor_id: str
    ) -> Membership | ServiceCredential | None:
        if actor_id.startswith("service:"):
            credential = self.db.get(
                ServiceCredential, actor_id.removeprefix("service:")
            )
            if (
                credential is None
                or credential.organization_id != organization_id
                or credential.revoked_at is not None
                or (
                    credential.expires_at is not None
                    and credential.expires_at <= _utcnow()
                )
            ):
                return None
            return credential
        return (
            self.db.query(Membership)
            .filter(
                Membership.organization_id == organization_id,
                Membership.actor_id == actor_id,
            )
            .first()
        )

    @staticmethod
    def _effective_permissions(
        principal: Membership | ServiceCredential,
    ) -> frozenset[str]:
        return ROLE_PERMISSIONS.get(principal.role, frozenset()).union(
            principal.permissions or []
        )

    @staticmethod
    def _validate_permissions(permissions: list[str]) -> None:
        invalid = set(permissions).difference(ALL_PERMISSIONS)
        if invalid:
            raise ValueError(f"Unknown permissions: {', '.join(sorted(invalid))}")


class ResourceAuthorizationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tenancy = TenancyService(db)

    def task_context(self, task: Task) -> TenantContext | None:
        if task.organization_id:
            return TenantContext(
                task.organization_id, task.project_id, "task.tenant_context"
            )
        project_ids = {
            project_id
            for (project_id,) in self.db.query(Run.project_id)
            .filter(Run.task_id == task.id, Run.project_id.is_not(None))
            .all()
            if project_id
        }
        contract = task.task_contract if isinstance(task.task_contract, dict) else {}
        contract_project = contract.get("project_id")
        if isinstance(contract_project, str):
            project_ids.add(contract_project)
        if len(project_ids) != 1:
            return None
        project = self.db.get(Project, next(iter(project_ids)))
        if project is None:
            return None
        return TenantContext(project.organization_id, project.id, "task/run bridge")

    def run_context(self, run: Run) -> TenantContext | None:
        if run.organization_id:
            return TenantContext(
                run.organization_id, run.project_id, "run.tenant_context"
            )
        if run.project_id:
            project = self.db.get(Project, run.project_id)
            if project:
                return TenantContext(project.organization_id, project.id, "run/project bridge")
        return self.task_context(run.task)

    def require_task(
        self, task_id: str, actor_id: str, permission: Permission | str
    ) -> Task:
        task = self.db.get(Task, task_id)
        if task is None:
            raise ValueError("Task not found.")
        context = self.task_context(task)
        if context is None:
            if self._production():
                raise AuthorizationError("Resource is not accessible.")
            return task
        self.tenancy.require_permission(
            context.organization_id,
            actor_id,
            permission,
            project_id=context.project_id,
            resource_type="task",
            resource_id=task.id,
        )
        return task

    def require_run(
        self, run_id: str, actor_id: str, permission: Permission | str
    ) -> Run:
        run = self.db.get(Run, run_id)
        if run is None:
            raise ValueError("Run not found.")
        context = self.run_context(run)
        if context is None:
            if self._production():
                raise AuthorizationError("Resource is not accessible.")
            return run
        self.tenancy.require_permission(
            context.organization_id,
            actor_id,
            permission,
            project_id=context.project_id,
            resource_type="run",
            resource_id=run.id,
        )
        return run

    def require_evidence(
        self,
        run_id: str,
        actor_id: str,
        permission: Permission | str,
        evidence_id: str | None = None,
    ) -> Run:
        run = self.require_run(run_id, actor_id, permission)
        if evidence_id:
            exists = (
                self.db.query(EvidencePack.id)
                .filter(
                    EvidencePack.id == evidence_id,
                    EvidencePack.run_id == run_id,
                )
                .first()
            )
            if not exists:
                raise ValueError("Evidence pack not found.")
        return run

    def require_approval(
        self, approval_id: str, actor_id: str, permission: Permission | str
    ) -> Approval:
        approval = self.db.get(Approval, approval_id)
        if approval is None:
            raise ValueError("Approval not found.")
        self.require_run(approval.run_id, actor_id, permission)
        return approval

    def require_repository(
        self,
        task_id: str | None,
        repository_path: str,
        actor_id: str,
        permission: Permission | str,
    ) -> Task | None:
        if task_id is None:
            if self._production():
                raise AuthorizationError("Resource is not accessible.")
            return None
        task = self.require_task(task_id, actor_id, permission)
        context = self.task_context(task)
        if context and context.project_id:
            project = self.db.get(Project, context.project_id)
            if (
                project
                and project.repository_path
                and os.path.realpath(project.repository_path)
                != os.path.realpath(repository_path)
            ):
                raise AuthorizationError("Resource is not accessible.")
        return task

    def accessible_runs(
        self, actor_id: str, permission: Permission | str = "runs.read"
    ) -> list[Run]:
        query = self.db.query(Run)
        if not self._production():
            return query.order_by(Run.created_at.desc()).all()
        if actor_id.startswith("service:"):
            credential = self.db.get(
                ServiceCredential, actor_id.removeprefix("service:")
            )
            if credential is None or permission not in TenancyService._effective_permissions(
                credential
            ):
                return []
            query = query.filter(Run.organization_id == credential.organization_id)
            if credential.project_id:
                query = query.filter(Run.project_id == credential.project_id)
        else:
            memberships = (
                self.db.query(Membership)
                .filter(Membership.actor_id == actor_id)
                .all()
            )
            organization_ids = [
                membership.organization_id
                for membership in memberships
                if permission in TenancyService._effective_permissions(membership)
            ]
            query = query.filter(Run.organization_id.in_(organization_ids))
        return query.order_by(Run.created_at.desc()).all()

    @staticmethod
    def _production() -> bool:
        return os.getenv("SACM_ENVIRONMENT", "development").lower() == "production"


class ServiceCredentialService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tenancy = TenancyService(db)

    def create(
        self,
        *,
        organization_id: str,
        actor_id: str,
        name: str,
        role: Role,
        permissions: list[str],
        project_id: str | None = None,
        expires_in_seconds: int | None = None,
    ) -> IssuedServiceCredential:
        self.tenancy.require_permission(
            organization_id,
            actor_id,
            "data.manage",
            project_id=project_id,
            resource_type="service_credential",
            resource_id=name,
        )
        self.tenancy._validate_permissions(permissions)
        if (
            self.db.query(ServiceCredential)
            .filter(
                ServiceCredential.organization_id == organization_id,
                ServiceCredential.name == name,
            )
            .first()
        ):
            raise ValueError("Service credential name already exists.")
        if project_id:
            project = self.db.get(Project, project_id)
            if project is None or project.organization_id != organization_id:
                raise ValueError("Project not found.")
        token = f"sacm_service_{secrets.token_urlsafe(48)}"
        credential = ServiceCredential(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            project_id=project_id,
            name=name,
            token_hash=hash_service_token(token),
            token_prefix=token[:20],
            role=role,
            permissions=sorted(set(permissions)),
            expires_at=(
                _utcnow() + timedelta(seconds=expires_in_seconds)
                if expires_in_seconds
                else None
            ),
            created_by=actor_id,
        )
        self.db.add(credential)
        self.db.commit()
        self.db.refresh(credential)
        self.tenancy.audit_sensitive(
            organization_id,
            project_id,
            actor_id,
            "service_credential.create",
            "service_credential",
            credential.id,
            "Service credential created.",
            {"role": role, "permissions": permissions},
        )
        return IssuedServiceCredential(credential, token)

    def list(
        self, organization_id: str, actor_id: str
    ) -> list[ServiceCredential]:
        self.tenancy.require_permission(
            organization_id,
            actor_id,
            "data.manage",
            resource_type="service_credential",
            resource_id=organization_id,
        )
        return (
            self.db.query(ServiceCredential)
            .filter(ServiceCredential.organization_id == organization_id)
            .order_by(ServiceCredential.created_at.desc())
            .all()
        )

    def revoke(
        self, organization_id: str, credential_id: str, actor_id: str
    ) -> ServiceCredential:
        self.tenancy.require_permission(
            organization_id,
            actor_id,
            "data.manage",
            resource_type="service_credential",
            resource_id=credential_id,
        )
        credential = self.db.get(ServiceCredential, credential_id)
        if credential is None or credential.organization_id != organization_id:
            raise ValueError("Service credential not found.")
        if credential.revoked_at is None:
            credential.revoked_at = _utcnow()
            self.db.commit()
            self.db.refresh(credential)
            self.tenancy.audit_sensitive(
                organization_id,
                credential.project_id,
                actor_id,
                "service_credential.revoke",
                "service_credential",
                credential.id,
                "Service credential revoked.",
            )
        return credential


class TenantBackfillService:
    """Idempotently attributes legacy records through task/run/project bridges."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.resources = ResourceAuthorizationService(db)

    def run(self, organization_id: str | None = None) -> dict[str, Any]:
        updated: dict[str, int] = {}
        unresolved: list[str] = []
        for task in self.db.query(Task).all():
            context = self.resources.task_context(task)
            if context is None:
                if organization_id is None:
                    unresolved.append(f"task:{task.id}")
                continue
            if organization_id and context.organization_id != organization_id:
                continue
            self._attribute(task, context)
            updated["tasks"] = updated.get("tasks", 0) + 1

        for run in self.db.query(Run).all():
            context = self.resources.run_context(run)
            if context is None:
                if organization_id is None:
                    unresolved.append(f"run:{run.id}")
                continue
            if organization_id and context.organization_id != organization_id:
                continue
            self._attribute(run, context)
            updated["runs"] = updated.get("runs", 0) + 1

        bridges: tuple[tuple[type[Any], str, str], ...] = (
            (ContextEvent, "task_id", "context_events"),
            (MemoryChunk, "task_id", "memory_chunks"),
            (Artifact, "task_id", "artifacts"),
            (ExecutionPlan, "task_id", "execution_plans"),
            (RunSnapshot, "run_id", "run_snapshots"),
            (EvidencePack, "run_id", "evidence_packs"),
            (Approval, "run_id", "approvals"),
            (ExecutionJob, "run_id", "execution_jobs"),
        )
        for model, bridge_name, label in bridges:
            for record in self.db.query(model).all():
                bridge_id = getattr(record, bridge_name)
                bridge = (
                    self.db.get(Task, bridge_id)
                    if bridge_name == "task_id"
                    else self.db.get(Run, bridge_id)
                )
                context = (
                    self.resources.task_context(bridge)
                    if isinstance(bridge, Task)
                    else self.resources.run_context(bridge)
                    if isinstance(bridge, Run)
                    else None
                )
                if context is None:
                    if organization_id is None:
                        unresolved.append(f"{label}:{record.id}")
                    continue
                if organization_id and context.organization_id != organization_id:
                    continue
                self._attribute(record, context)
                updated[label] = updated.get(label, 0) + 1

        for replay in self.db.query(RunReplay).all():
            source = self.db.get(Run, replay.source_run_id)
            context = self.resources.run_context(source) if source else None
            if context is None:
                if organization_id is None:
                    unresolved.append(f"run_replays:{replay.id}")
            elif organization_id and context.organization_id != organization_id:
                continue
            else:
                self._attribute(replay, context)
                updated["run_replays"] = updated.get("run_replays", 0) + 1

        for model, label in (
            (ExecutorEnrollmentToken, "executor_enrollment_tokens"),
            (ExecutorRegistration, "executor_registrations"),
        ):
            for record in self.db.query(model).all():
                scoped_record: Any = record
                context = self._scope_context(record)
                if context:
                    if organization_id and context.organization_id != organization_id:
                        continue
                    if not scoped_record.tenant_attribution:
                        scoped_record.tenant_attribution = {
                            "schema_version": "tenant-attribution/v1",
                            "source": context.source,
                            "organization_id": context.organization_id,
                            "project_id": context.project_id,
                            "backfilled_at": _utcnow().isoformat(),
                        }
                    updated[label] = updated.get(label, 0) + 1

        self.db.commit()
        return {
            "organization_id": organization_id,
            "updated": updated,
            "unresolved": sorted(unresolved),
        }

    @staticmethod
    def _attribute(record: Any, context: TenantContext) -> None:
        if hasattr(record, "organization_id") and not record.organization_id:
            record.organization_id = context.organization_id
        if hasattr(record, "project_id") and not record.project_id:
            record.project_id = context.project_id
        if hasattr(record, "tenant_attribution") and not record.tenant_attribution:
            record.tenant_attribution = {
                "schema_version": "tenant-attribution/v1",
                "source": context.source,
                "backfilled_at": _utcnow().isoformat(),
            }

    def _scope_context(self, record: Any) -> TenantContext | None:
        if record.project_id:
            project = self.db.get(Project, record.project_id)
            if project:
                return TenantContext(
                    project.organization_id, project.id, "executor/project bridge"
                )
        if record.organization_id:
            return TenantContext(record.organization_id, None, "executor scope")
        return None
