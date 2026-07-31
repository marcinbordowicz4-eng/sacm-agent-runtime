import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from sacm.core.auth_service import production_mode
from sacm.core.run_service import RunService
from sacm.core.secret_broker import (
    EnterpriseSecretBroker,
    provider_lease_id_hash,
    validate_provider_config,
    wrap_for_executor,
)
from sacm.core.tenancy_service import AuthorizationError, TenancyService
from sacm.infrastructure.db.models import (
    CredentialLease,
    ExecutionJob,
    ExecutionPlan,
    ExecutorRegistration,
    Project,
    SecretProviderConfig,
)
from sacm.schemas.execution_plan import SecretRequestV1
from sacm.schemas.execution_plane import SecretProviderConfigCreate


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _lease_token_hash(token: str) -> str:
    return hashlib.sha256(f"sacm:lease:v1:{token}".encode()).hexdigest()


class CredentialLeaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class WrappedCredentialResult:
    ciphertext: str
    wrapping_algorithm: str
    encryption_key_fingerprint: str
    content_type: str
    expires_at: datetime


class CredentialLeaseService:
    def __init__(
        self, db: Session, broker: EnterpriseSecretBroker | None = None
    ) -> None:
        self.db = db
        self.broker = broker or EnterpriseSecretBroker()

    def create_provider_config(
        self,
        organization_id: str,
        actor_id: str,
        payload: SecretProviderConfigCreate,
    ) -> SecretProviderConfig:
        tenancy = TenancyService(self.db)
        tenancy.require_permission(
            organization_id,
            actor_id,
            "secrets.manage",
            project_id=payload.project_id,
            resource_type="secret_provider_config",
            resource_id=organization_id,
        )
        if payload.project_id:
            project = self.db.get(Project, payload.project_id)
            if project is None or project.organization_id != organization_id:
                raise AuthorizationError("Resource is not accessible.")
        validate_provider_config(payload.provider, payload.config_metadata)
        if payload.provider == "environment" and payload.approved_for_production:
            raise ValueError(
                "The environment provider cannot be approved for production."
            )
        existing = (
            self.db.query(SecretProviderConfig)
            .filter(
                SecretProviderConfig.organization_id == organization_id,
                SecretProviderConfig.project_id == payload.project_id,
                SecretProviderConfig.name == payload.name,
            )
            .first()
        )
        if existing:
            existing.provider = payload.provider
            existing.enabled = payload.enabled
            existing.approved_for_production = payload.approved_for_production
            existing.config_metadata = payload.config_metadata
            existing.updated_by = actor_id
            record = existing
        else:
            record = SecretProviderConfig(
                id=str(uuid.uuid4()),
                organization_id=organization_id,
                project_id=payload.project_id,
                name=payload.name,
                provider=payload.provider,
                enabled=payload.enabled,
                approved_for_production=payload.approved_for_production,
                config_metadata=payload.config_metadata,
                created_by=actor_id,
                updated_by=actor_id,
            )
            self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        tenancy.audit_sensitive(
            organization_id,
            payload.project_id,
            actor_id,
            "secret_provider.configure",
            "secret_provider_config",
            record.id,
            "Secret provider metadata configured.",
            {
                "provider": record.provider,
                "enabled": record.enabled,
                "approved_for_production": record.approved_for_production,
            },
        )
        return record

    def list_provider_configs(
        self,
        organization_id: str,
        actor_id: str,
        *,
        project_id: str | None = None,
    ) -> list[SecretProviderConfig]:
        TenancyService(self.db).require_permission(
            organization_id,
            actor_id,
            "secrets.read",
            project_id=project_id,
            resource_type="secret_provider_config",
            resource_id=organization_id,
        )
        query = self.db.query(SecretProviderConfig).filter(
            SecretProviderConfig.organization_id == organization_id
        )
        if project_id:
            query = query.filter(
                SecretProviderConfig.project_id.in_((None, project_id))
            )
        return query.order_by(
            SecretProviderConfig.project_id,
            SecretProviderConfig.name,
        ).all()

    def provider_health(
        self,
        organization_id: str,
        config_id: str,
        actor_id: str,
    ) -> tuple[SecretProviderConfig, dict[str, Any]]:
        config = self._authorized_config(
            organization_id, config_id, actor_id, "secrets.read"
        )
        details = self.broker.provider(config.provider).health(
            config.config_metadata
        )
        return config, details

    def issue(
        self,
        executor: ExecutorRegistration,
        *,
        job_id: str,
        lease_token: str,
        requirement_name: str,
        ttl_seconds: int,
    ) -> CredentialLease:
        job = self._owned_active_job(executor, job_id, lease_token)
        requirement = self._declared_requirement(job, requirement_name)
        config = self._provider_config(job, requirement)
        now = _utcnow()
        maximum = min(
            int(os.getenv("SACM_CREDENTIAL_LEASE_MAX_SECONDS", "300")), 900
        )
        effective_ttl = min(ttl_seconds, maximum)
        expires_at = now + timedelta(seconds=effective_ttl)
        if job.lease_expires_at is not None:
            expires_at = min(expires_at, job.lease_expires_at)
        if expires_at <= now:
            raise CredentialLeaseError("Execution job lease is too close to expiry.")
        existing = (
            self.db.query(CredentialLease)
            .filter(
                CredentialLease.job_id == job.id,
                CredentialLease.executor_id == executor.id,
                CredentialLease.requirement_name == requirement.name,
                CredentialLease.revoked_at.is_(None),
                CredentialLease.expires_at > now,
                CredentialLease.use_count == 0,
            )
            .first()
        )
        if existing:
            return existing
        policy_decision = {
            "schema_version": "credential-policy-decision/v1",
            "allow": True,
            "reason": "Declared execution-plan requirement for owned active job.",
            "least_privilege": True,
            "one_time_exchange": True,
            "max_uses": 1,
        }
        record = CredentialLease(
            id=str(uuid.uuid4()),
            organization_id=job.organization_id,
            project_id=job.project_id,
            task_id=job.task_id,
            run_id=job.run_id,
            job_id=job.id,
            executor_id=executor.id,
            requirement_name=requirement.name,
            requested_permissions=sorted(set(requirement.permissions)),
            resource=requirement.resource or requirement.environment_variable,
            provider=requirement.provider,
            provider_config_id=config.id if config else None,
            opaque_handle=f"credlease_{secrets.token_urlsafe(32)}",
            issued_at=now,
            expires_at=expires_at,
            audience=requirement.audience or f"executor:{executor.id}",
            policy_decision=policy_decision,
        )
        self.db.add(record)
        self._append_runtime_event(
            job,
            "CredentialLeaseIssued",
            executor,
            {
                "credential_lease_id": record.id,
                "requirement_name": requirement.name,
                "provider": requirement.provider,
                "resource_hash": hashlib.sha256(
                    str(record.resource).encode()
                ).hexdigest(),
                "expires_at": expires_at.isoformat(),
                "policy_decision": policy_decision,
            },
        )
        self.db.commit()
        self.db.refresh(record)
        self._audit(
            record,
            f"executor:{executor.id}",
            "credential_lease.issue",
            "Credential lease issued for a declared job requirement.",
        )
        return record

    def exchange(
        self,
        executor: ExecutorRegistration,
        lease_id: str,
        lease_token: str,
    ) -> WrappedCredentialResult:
        record = self._active_lease(executor, lease_id, lease_token)
        if record.use_count != 0:
            raise CredentialLeaseError(
                "Credential material exchange is one-time and already consumed."
            )
        if not executor.public_encryption_key:
            raise CredentialLeaseError(
                "Executor did not enroll a credential wrapping public key."
            )
        job = self.db.get(ExecutionJob, record.job_id)
        if job is None:
            raise CredentialLeaseError("Execution job not found.")
        requirement = self._declared_requirement(job, record.requirement_name)
        config = (
            self.db.get(SecretProviderConfig, record.provider_config_id)
            if record.provider_config_id
            else None
        )
        ttl = max(1, int((record.expires_at - _utcnow()).total_seconds()))
        material = self.broker.fetch(
            requirement,
            config.config_metadata if config else {},
            ttl_seconds=ttl,
        )
        ciphertext, algorithm, fingerprint = wrap_for_executor(
            executor.public_encryption_key, material.value
        )
        if (
            executor.encryption_key_fingerprint
            and not hmac.compare_digest(
                executor.encryption_key_fingerprint, fingerprint
            )
        ):
            raise CredentialLeaseError(
                "Executor credential wrapping key fingerprint changed."
            )
        now = _utcnow()
        effective_expiry = min(
            record.expires_at,
            material.expires_at or record.expires_at,
        )
        record.last_used_at = now
        record.use_count = 1
        record.expires_at = effective_expiry
        record.provider_lease_id_hash = provider_lease_id_hash(
            record.provider, material.provider_lease_id
        )
        self._append_runtime_event(
            job,
            "CredentialMaterialWrapped",
            executor,
            {
                "credential_lease_id": record.id,
                "provider": record.provider,
                "provider_lease_id_hash": record.provider_lease_id_hash,
                "wrapping_algorithm": algorithm,
                "encryption_key_fingerprint": fingerprint,
                "expires_at": effective_expiry.isoformat(),
            },
        )
        self.db.commit()
        self._audit(
            record,
            f"executor:{executor.id}",
            "credential_lease.exchange",
            "Credential material wrapped to the owning executor public key.",
        )
        return WrappedCredentialResult(
            ciphertext=ciphertext,
            wrapping_algorithm=algorithm,
            encryption_key_fingerprint=fingerprint,
            content_type=material.content_type,
            expires_at=effective_expiry,
        )

    def renew(
        self,
        executor: ExecutorRegistration,
        lease_id: str,
        lease_token: str,
        ttl_seconds: int,
    ) -> CredentialLease:
        record = self._active_lease(executor, lease_id, lease_token)
        if record.use_count:
            raise CredentialLeaseError(
                "A consumed one-time credential lease cannot be renewed."
            )
        job = self.db.get(ExecutionJob, record.job_id)
        if job is None or job.lease_expires_at is None:
            raise CredentialLeaseError("Execution job lease is unavailable.")
        maximum = min(
            int(os.getenv("SACM_CREDENTIAL_LEASE_MAX_SECONDS", "300")), 900
        )
        requested = _utcnow() + timedelta(seconds=min(ttl_seconds, maximum))
        absolute = record.issued_at + timedelta(seconds=900)
        record.expires_at = min(requested, absolute, job.lease_expires_at)
        if record.expires_at <= _utcnow():
            raise CredentialLeaseError("Credential lease cannot be renewed.")
        self.db.commit()
        self.db.refresh(record)
        self._audit(
            record,
            f"executor:{executor.id}",
            "credential_lease.renew",
            "Unused credential lease renewed within job and absolute TTL bounds.",
        )
        return record

    def list_leases(
        self,
        organization_id: str,
        actor_id: str,
        *,
        project_id: str | None = None,
        job_id: str | None = None,
    ) -> list[CredentialLease]:
        TenancyService(self.db).require_permission(
            organization_id,
            actor_id,
            "secrets.read",
            project_id=project_id,
            resource_type="credential_lease",
            resource_id=organization_id,
        )
        query = self.db.query(CredentialLease).filter(
            CredentialLease.organization_id == organization_id
        )
        if project_id:
            query = query.filter(CredentialLease.project_id == project_id)
        if job_id:
            query = query.filter(CredentialLease.job_id == job_id)
        return query.order_by(
            CredentialLease.issued_at.desc(), CredentialLease.id
        ).all()

    def revoke(
        self,
        organization_id: str,
        lease_id: str,
        actor_id: str,
        reason: str,
    ) -> CredentialLease:
        record = self.db.get(CredentialLease, lease_id) or (
            self.db.query(CredentialLease)
            .filter(CredentialLease.opaque_handle == lease_id)
            .first()
        )
        if record is None or record.organization_id != organization_id:
            raise AuthorizationError("Resource is not accessible.")
        TenancyService(self.db).require_permission(
            organization_id,
            actor_id,
            "secrets.manage",
            project_id=record.project_id,
            resource_type="credential_lease",
            resource_id=record.id,
        )
        self._revoke_record(record, actor_id, reason)
        self.db.commit()
        self.db.refresh(record)
        self._audit(
            record,
            actor_id,
            "credential_lease.revoke",
            "Credential lease revoked.",
        )
        return record

    def revoke_for_job(
        self, job_id: str, actor_id: str, reason: str, *, commit: bool = False
    ) -> int:
        records = (
            self.db.query(CredentialLease)
            .filter(
                CredentialLease.job_id == job_id,
                CredentialLease.revoked_at.is_(None),
            )
            .all()
        )
        for record in records:
            self._revoke_record(record, actor_id, reason)
        if commit:
            self.db.commit()
        return len(records)

    def revoke_for_executor(
        self, executor_id: str, actor_id: str, reason: str
    ) -> int:
        records = (
            self.db.query(CredentialLease)
            .filter(
                CredentialLease.executor_id == executor_id,
                CredentialLease.revoked_at.is_(None),
            )
            .all()
        )
        for record in records:
            self._revoke_record(record, actor_id, reason)
        return len(records)

    def _authorized_config(
        self,
        organization_id: str,
        config_id: str,
        actor_id: str,
        permission: str,
    ) -> SecretProviderConfig:
        config = self.db.get(SecretProviderConfig, config_id)
        if config is None or config.organization_id != organization_id:
            raise AuthorizationError("Resource is not accessible.")
        TenancyService(self.db).require_permission(
            organization_id,
            actor_id,
            permission,
            project_id=config.project_id,
            resource_type="secret_provider_config",
            resource_id=config.id,
        )
        return config

    def _provider_config(
        self, job: ExecutionJob, request: SecretRequestV1
    ) -> SecretProviderConfig | None:
        if request.provider == "environment":
            if production_mode():
                raise CredentialLeaseError(
                    "Environment-backed credentials are prohibited in production."
                )
            return None
        query = self.db.query(SecretProviderConfig).filter(
            SecretProviderConfig.organization_id == job.organization_id,
            SecretProviderConfig.provider == request.provider,
            SecretProviderConfig.enabled.is_(True),
        )
        if request.provider_config:
            query = query.filter(
                SecretProviderConfig.name == request.provider_config
            )
        configs = query.order_by(
            SecretProviderConfig.project_id.desc(),
            SecretProviderConfig.updated_at.desc(),
        ).all()
        config = next(
            (
                item
                for item in configs
                if item.project_id in {None, job.project_id}
            ),
            None,
        )
        if config is None:
            raise CredentialLeaseError(
                f"No enabled {request.provider} provider configuration is available."
            )
        if production_mode() and not config.approved_for_production:
            raise CredentialLeaseError(
                "The selected secret provider is not approved for production."
            )
        validate_provider_config(config.provider, config.config_metadata)
        return config

    def _declared_requirement(
        self, job: ExecutionJob, requirement_name: str
    ) -> SecretRequestV1:
        requirements = list(job.secret_requirements or [])
        if not requirements:
            plan = (
                self.db.query(ExecutionPlan)
                .filter(ExecutionPlan.task_id == job.task_id)
                .order_by(ExecutionPlan.revision.desc())
                .first()
            )
            if plan:
                requirements = [item.request for item in plan.secret_requirements]
        for raw in requirements:
            request = SecretRequestV1.model_validate(raw)
            if request.name == requirement_name:
                return request
        raise AuthorizationError(
            "Credential request is not declared by the execution plan for this job."
        )

    def _owned_active_job(
        self,
        executor: ExecutorRegistration,
        job_id: str,
        lease_token: str,
    ) -> ExecutionJob:
        if executor.status != "ACTIVE":
            raise AuthorizationError("Executor is not active.")
        job = self.db.get(ExecutionJob, job_id)
        if job is None:
            raise CredentialLeaseError("Execution job not found.")
        if job.lease_owner_id != executor.id:
            raise AuthorizationError(
                "The execution job lease belongs to another executor."
            )
        if not job.lease_token_hash or not hmac.compare_digest(
            job.lease_token_hash, _lease_token_hash(lease_token)
        ):
            raise AuthorizationError("Invalid execution job lease token.")
        if job.state not in {"LEASED", "RUNNING"}:
            raise CredentialLeaseError("Execution job is not active.")
        if job.lease_expires_at is None or job.lease_expires_at <= _utcnow():
            raise CredentialLeaseError("Execution job lease has expired.")
        return job

    def _active_lease(
        self,
        executor: ExecutorRegistration,
        lease_id: str,
        lease_token: str,
    ) -> CredentialLease:
        record = self.db.get(CredentialLease, lease_id) or (
            self.db.query(CredentialLease)
            .filter(CredentialLease.opaque_handle == lease_id)
            .first()
        )
        if record is None:
            raise CredentialLeaseError("Credential lease not found.")
        self._owned_active_job(executor, record.job_id, lease_token)
        if record.executor_id != executor.id:
            raise AuthorizationError(
                "Credential lease belongs to another executor."
            )
        if record.revoked_at is not None:
            raise CredentialLeaseError("Credential lease is revoked.")
        if record.expires_at <= _utcnow():
            raise CredentialLeaseError("Credential lease is expired.")
        return record

    @staticmethod
    def _revoke_record(
        record: CredentialLease, actor_id: str, reason: str
    ) -> None:
        if record.revoked_at is None:
            record.revoked_at = _utcnow()
            record.revoked_by = actor_id
            record.revocation_reason = reason

    def _append_runtime_event(
        self,
        job: ExecutionJob,
        event_type: str,
        executor: ExecutorRegistration,
        payload: dict[str, Any],
    ) -> None:
        run = RunService(self.db).get(job.run_id)
        if run:
            RunService(self.db)._append_event(
                run,
                event_type=event_type,
                actor=f"executor:{executor.id}",
                payload=payload,
                step_id=job.run_step_id,
            )

    def _audit(
        self,
        record: CredentialLease,
        actor_id: str,
        action: str,
        reason: str,
    ) -> None:
        if not record.organization_id:
            return
        TenancyService(self.db).audit_sensitive(
            record.organization_id,
            record.project_id,
            actor_id,
            action,
            "credential_lease",
            record.id,
            reason,
            {
                "job_id": record.job_id,
                "executor_id": record.executor_id,
                "requirement_name": record.requirement_name,
                "provider": record.provider,
                "resource_hash": hashlib.sha256(
                    str(record.resource).encode()
                ).hexdigest(),
                "provider_lease_id_hash": record.provider_lease_id_hash,
                "use_count": record.use_count,
            },
        )
