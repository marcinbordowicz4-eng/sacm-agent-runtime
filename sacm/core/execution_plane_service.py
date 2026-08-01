import hashlib
import hmac
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import update
from sqlalchemy.orm import Session

from sacm.core.auth_service import production_mode
from sacm.core.execution_signing import (
    canonical_hash,
    public_key_fingerprint,
    sign_control_plane_payload,
    verify_ed25519,
)
from sacm.core.external_agent_service import ExternalAgentService
from sacm.core.governance_service import ResidencyService
from sacm.core.run_service import RunService
from sacm.core.secret_broker import (
    SecretProviderError,
    encryption_key_fingerprint,
)
from sacm.core.tenancy_service import AuthorizationError, TenancyService
from sacm.infrastructure.db.models import (
    ExecutionJob,
    ExecutionPlan,
    ExecutorEnrollmentToken,
    ExecutorRegistration,
    Project,
    RunStep,
)
from sacm.schemas.contracts import AgentTaskV1
from sacm.schemas.execution_plane import (
    ExecutorEnroll,
    ExecutorEnrollmentTokenCreate,
    ExecutorRotate,
    SignedJobResult,
)
from sacm.schemas.recovery import FailureInputV1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _token_hash(kind: str, token: str) -> str:
    return hashlib.sha256(f"sacm:{kind}:v1:{token}".encode()).hexdigest()


class ExecutionPlaneError(Exception):
    pass


class ExecutionConflict(ExecutionPlaneError):
    pass


class ExecutorAuthenticationError(ExecutionPlaneError):
    pass


class ExecutionAuthorizationError(ExecutionPlaneError):
    pass


@dataclass(frozen=True)
class IssuedEnrollmentToken:
    record: ExecutorEnrollmentToken
    token: str


@dataclass(frozen=True)
class EnrolledExecutor:
    executor: ExecutorRegistration
    auth_token: str


@dataclass(frozen=True)
class LeasedJob:
    job: ExecutionJob
    lease_token: str


class ExecutionPlaneService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.runs = RunService(db)

    def issue_enrollment_token(
        self, payload: ExecutorEnrollmentTokenCreate, actor: str
    ) -> IssuedEnrollmentToken:
        scope_key = self._authorize_scope_admin(payload, actor)
        project = self.db.get(Project, payload.project_id) if payload.project_id else None
        derived_organization_id = (
            payload.organization_id
            or (project.organization_id if project else None)
        )
        token = secrets.token_urlsafe(48)
        record = ExecutorEnrollmentToken(
            id=str(uuid.uuid4()),
            organization_id=payload.organization_id,
            project_id=payload.project_id,
            customer_deployment_id=payload.customer_deployment_id,
            scope_key=scope_key,
            tenant_attribution={
                "schema_version": "tenant-attribution/v1",
                "source": "executor enrollment scope",
                "organization_id": derived_organization_id,
                "project_id": payload.project_id,
            },
            token_hash=_token_hash("enrollment", token),
            expires_at=_utcnow() + timedelta(seconds=payload.expires_in_seconds),
            created_by=actor,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        if derived_organization_id:
            TenancyService(self.db).audit_sensitive(
                derived_organization_id,
                payload.project_id,
                actor,
                "executor.enrollment_token.issue",
                "executor_enrollment_token",
                record.id,
                "Executor enrollment token issued.",
            )
        return IssuedEnrollmentToken(record=record, token=token)

    def enroll(self, payload: ExecutorEnroll) -> EnrolledExecutor:
        now = _utcnow()
        token_hash = _token_hash("enrollment", payload.enrollment_token)
        query = self.db.query(ExecutorEnrollmentToken).filter(
            ExecutorEnrollmentToken.token_hash == token_hash
        )
        if self._dialect() == "postgresql":
            query = query.with_for_update()
        enrollment = query.first()
        if (
            enrollment is None
            or enrollment.revoked_at is not None
            or enrollment.used_at is not None
            or enrollment.expires_at <= now
        ):
            raise ExecutorAuthenticationError(
                "Enrollment token is invalid, expired, revoked, or already used."
            )
        sandbox_policy = payload.sandbox_policy.model_dump(mode="json") if (
            payload.sandbox_policy
        ) else {
            "schema_version": "sandbox-policy/v1",
            "runtime": payload.sandbox_runtime,
            "host_runtime_verified": False,
            "verification_command": None,
            "isolation": "policy-approved",
            "network_mode": "deny-by-default",
            "no_new_privileges": True,
        }
        self._validate_sandbox_runtime(payload.sandbox_runtime, sandbox_policy)
        try:
            fingerprint = public_key_fingerprint(payload.public_signing_key)
        except (RuntimeError, ValueError) as exc:
            raise ExecutionConflict(f"Invalid executor public signing key: {exc}") from exc
        try:
            encryption_fingerprint = (
                encryption_key_fingerprint(payload.public_encryption_key)
                if payload.public_encryption_key
                else None
            )
        except SecretProviderError as exc:
            raise ExecutionConflict(str(exc)) from exc
        existing = (
            self.db.query(ExecutorRegistration)
            .filter(
                ExecutorRegistration.scope_key == enrollment.scope_key,
                ExecutorRegistration.executor_identity == payload.executor_identity,
            )
            .first()
        )
        if existing is not None:
            raise ExecutionConflict(
                "An executor with this identity is already enrolled in the scope."
            )
        auth_token = secrets.token_urlsafe(48)
        storage = self._executor_storage(enrollment, payload)
        self._validate_network_boundary(
            enrollment.organization_id,
            enrollment.project_id,
            payload.network_boundary,
            storage.get("region"),
        )
        executor = ExecutorRegistration(
            id=str(uuid.uuid4()),
            organization_id=enrollment.organization_id,
            project_id=enrollment.project_id,
            customer_deployment_id=enrollment.customer_deployment_id,
            scope_key=enrollment.scope_key,
            tenant_attribution=enrollment.tenant_attribution,
            storage_region=storage.get("region"),
            storage_classification=storage.get("classification"),
            storage_class=storage.get("storage_class"),
            executor_identity=payload.executor_identity,
            display_name=payload.display_name,
            capabilities=sorted(set(payload.capabilities)),
            labels=dict(sorted(payload.labels.items())),
            runtime_kind=payload.runtime_kind,
            sandbox_runtime=payload.sandbox_runtime,
            sandbox_policy=sandbox_policy,
            public_signing_key=payload.public_signing_key,
            signing_key_fingerprint=fingerprint,
            public_encryption_key=payload.public_encryption_key,
            encryption_key_fingerprint=encryption_fingerprint,
            auth_token_hash=_token_hash("executor-auth", auth_token),
            status="ACTIVE",
            last_heartbeat_at=now,
            version=payload.version,
            network_boundary=payload.network_boundary,
            enrolled_at=now,
            updated_at=now,
        )
        enrollment.used_at = now
        self.db.add(executor)
        self.db.commit()
        self.db.refresh(executor)
        organization_id = executor.organization_id
        if executor.project_id:
            project = self.db.get(Project, executor.project_id)
            organization_id = project.organization_id if project else None
        if organization_id:
            TenancyService(self.db).audit_sensitive(
                organization_id,
                executor.project_id,
                f"executor:{executor.id}",
                "executor.enroll",
                "executor",
                executor.id,
                "Executor enrolled.",
            )
        return EnrolledExecutor(executor=executor, auth_token=auth_token)

    def _executor_storage(
        self, enrollment: ExecutorEnrollmentToken, payload: ExecutorEnroll
    ) -> dict[str, str | None]:
        organization_id = enrollment.organization_id
        project_id = enrollment.project_id
        if project_id:
            project = self.db.get(Project, project_id)
            organization_id = project.organization_id if project else None
        if organization_id:
            return ResidencyService(self.db).resolve(
                organization_id=organization_id,
                project_id=project_id,
                category="artifacts",
                region=payload.storage_region,
                classification=payload.storage_classification,
                storage_class=payload.storage_class,
            )
        allowed = {
            item.strip()
            for item in os.getenv(
                "SACM_CUSTOMER_EXECUTOR_ALLOWED_REGIONS", ""
            ).split(",")
            if item.strip()
        }
        if payload.storage_region and (
            payload.storage_region not in allowed
            and (production_mode() or bool(allowed))
        ):
            raise ExecutionConflict("Customer executor storage region is not allowed.")
        return {
            "region": payload.storage_region,
            "classification": payload.storage_classification or "Confidential",
            "storage_class": payload.storage_class or "standard",
        }

    def authenticate_executor(self, token: str) -> ExecutorRegistration:
        token_hash = _token_hash("executor-auth", token)
        executor = (
            self.db.query(ExecutorRegistration)
            .filter(ExecutorRegistration.auth_token_hash == token_hash)
            .first()
        )
        if executor is None or executor.status == "REVOKED":
            raise ExecutorAuthenticationError("Invalid or revoked executor token.")
        offline_after = int(os.getenv("SACM_EXECUTOR_OFFLINE_SECONDS", "300"))
        if (
            executor.status == "ACTIVE"
            and executor.last_heartbeat_at is not None
            and executor.last_heartbeat_at
            <= _utcnow() - timedelta(seconds=offline_after)
        ):
            executor.status = "OFFLINE"
            self.db.commit()
        return executor

    def heartbeat_executor(
        self,
        executor: ExecutorRegistration,
        *,
        version: str | None = None,
        network_boundary: dict[str, Any] | None = None,
        capabilities: list[str] | None = None,
        labels: dict[str, str] | None = None,
        capacity: dict[str, Any] | None = None,
    ) -> ExecutorRegistration:
        if executor.status == "REVOKED":
            raise ExecutorAuthenticationError("Executor is revoked.")
        executor.last_heartbeat_at = _utcnow()
        executor.status = "ACTIVE"
        if version is not None:
            executor.version = version
        if network_boundary is not None:
            self._validate_network_boundary(
                executor.organization_id,
                executor.project_id,
                network_boundary,
                executor.storage_region,
            )
            executor.network_boundary = network_boundary
        boundary = dict(executor.network_boundary)
        if capacity is not None:
            boundary["reported_capacity"] = self._validated_capacity(capacity)
            executor.network_boundary = boundary
        if capabilities is not None:
            executor.capabilities = sorted(set(capabilities))
        if labels is not None:
            executor.labels = dict(sorted(labels.items()))
        self.db.commit()
        self.db.refresh(executor)
        return executor

    def rotate_executor_identity(
        self, executor: ExecutorRegistration, payload: ExecutorRotate
    ) -> EnrolledExecutor:
        try:
            actual_fingerprint = public_key_fingerprint(payload.public_signing_key)
        except (RuntimeError, ValueError) as exc:
            raise ExecutionConflict(f"Invalid executor public signing key: {exc}") from exc
        if not hmac.compare_digest(actual_fingerprint, payload.signing_key_fingerprint):
            raise ExecutionConflict("Signing key fingerprint does not match the public key.")
        auth_token = secrets.token_urlsafe(48)
        executor.public_signing_key = payload.public_signing_key
        executor.signing_key_fingerprint = actual_fingerprint
        executor.auth_token_hash = _token_hash("executor-auth", auth_token)
        executor.updated_at = _utcnow()
        self.db.commit()
        self.db.refresh(executor)
        organization_id = self._organization_id(executor)
        if organization_id:
            TenancyService(self.db).audit_sensitive(
                organization_id,
                executor.project_id,
                f"executor:{executor.id}",
                "executor.identity.rotate",
                "executor",
                executor.id,
                "Executor signing key and authentication token rotated.",
                {"signing_key_fingerprint": actual_fingerprint},
            )
        return EnrolledExecutor(executor=executor, auth_token=auth_token)

    def control_directives(self, executor: ExecutorRegistration) -> dict[str, Any]:
        current = os.getenv("SACM_EXECUTOR_CURRENT_VERSION", executor.version)
        minimum = os.getenv("SACM_EXECUTOR_MINIMUM_VERSION", "0.0.0")
        drain = executor.status != "ACTIVE"
        reason = "executor is not active" if drain else None
        try:
            if self._version_tuple(executor.version) < self._version_tuple(minimum):
                drain = True
                reason = "executor version is below the enforced minimum"
        except ValueError:
            drain = True
            reason = "executor reported an invalid semantic version"
        manifest = self._signed_update_manifest(current, minimum)
        return {
            "current_version": current,
            "minimum_version": minimum,
            "drain": drain,
            "revoked": executor.status == "REVOKED",
            "reason": reason,
            "update_manifest": manifest,
            "automatic_update": False,
        }

    def fleet_health(self, executors: list[ExecutorRegistration]) -> dict[str, Any]:
        now = _utcnow()
        offline_seconds = int(os.getenv("SACM_EXECUTOR_OFFLINE_SECONDS", "300"))
        active = 0
        offline = 0
        revoked = 0
        draining = 0
        max_jobs = 0
        active_jobs = 0
        for executor in executors:
            boundary = executor.network_boundary or {}
            capacity = boundary.get("reported_capacity") or {}
            max_jobs += int(capacity.get("max_concurrent_jobs") or 0)
            active_jobs += int(capacity.get("active_jobs") or 0)
            draining += int(bool(capacity.get("draining")))
            if executor.status == "REVOKED":
                revoked += 1
            elif (
                executor.last_heartbeat_at is None
                or executor.last_heartbeat_at
                <= now - timedelta(seconds=offline_seconds)
            ):
                offline += 1
            else:
                active += 1
        total = len(executors)
        target = float(os.getenv("SACM_EXECUTOR_AVAILABILITY_SLO", "0.99"))
        availability = active / max(1, total - revoked)
        from sacm.core.observability import OpenTelemetryService

        OpenTelemetryService(
            os.getenv("SACM_OTEL_ENABLED", "false").lower() == "true"
        ).record_resilience_event(
            "executor_capacity", max_jobs - active_jobs, {"active": active}
        )
        return {
            "total": total,
            "active": active,
            "offline": offline,
            "revoked": revoked,
            "draining": draining,
            "capacity": {
                "max_concurrent_jobs": max_jobs,
                "active_jobs": active_jobs,
                "available_slots": max(0, max_jobs - active_jobs),
            },
            "slo": {
                "availability": availability,
                "target": target,
                "met": availability >= target,
            },
        }

    def list_executors(
        self,
        actor: str,
        *,
        organization_id: str | None = None,
        project_id: str | None = None,
        customer_deployment_id: str | None = None,
    ) -> list[ExecutorRegistration]:
        scope = ExecutorEnrollmentTokenCreate(
            organization_id=organization_id,
            project_id=project_id,
            customer_deployment_id=customer_deployment_id,
        )
        scope_key = self._authorize_scope_admin(scope, actor)
        return (
            self.db.query(ExecutorRegistration)
            .filter(ExecutorRegistration.scope_key == scope_key)
            .order_by(ExecutorRegistration.enrolled_at, ExecutorRegistration.id)
            .all()
        )

    def revoke_executor(
        self, executor_id: str, actor: str, reason: str
    ) -> ExecutorRegistration:
        executor = self.db.get(ExecutorRegistration, executor_id)
        if executor is None:
            raise ValueError("Executor not found.")
        self._authorize_executor_admin(executor, actor)
        if executor.status == "REVOKED":
            return executor
        now = _utcnow()
        executor.status = "REVOKED"
        executor.revoked_at = now
        executor.revoked_by = actor
        executor.revocation_reason = reason
        active_jobs = (
            self.db.query(ExecutionJob)
            .filter(
                ExecutionJob.lease_owner_id == executor.id,
                ExecutionJob.state.in_(("LEASED", "RUNNING")),
            )
            .all()
        )
        for job in active_jobs:
            self._release_or_expire(job, now, "ExecutorRevoked")
        from sacm.core.credential_lease_service import CredentialLeaseService

        CredentialLeaseService(self.db).revoke_for_executor(
            executor.id, actor, "Owning executor revoked."
        )
        self.db.commit()
        self.db.refresh(executor)
        return executor

    def schedule(
        self,
        *,
        run_id: str,
        run_step_id: str,
        task: AgentTaskV1,
        idempotency_key: str,
        required_capabilities: list[str],
        required_labels: dict[str, str] | None = None,
        max_attempts: int = 3,
        customer_deployment_id: str | None = None,
        commit: bool = True,
    ) -> ExecutionJob:
        run = self.runs.get(run_id)
        if run is None:
            raise ValueError("Run not found.")
        step = self.runs.get_step(run_id, run_step_id)
        if step is None:
            raise ValueError("Run step not found.")
        if task.run_id != run_id or task.step_id != run_step_id:
            raise ExecutionConflict("AgentTaskV1 identity does not match the job.")
        organization_id: str | None = None
        project_id = run.project_id
        if project_id is not None:
            project = self.db.get(Project, project_id)
            if project is None:
                raise ValueError("Run project not found.")
            organization_id = project.organization_id
            scope_key = f"project:{project_id}"
        else:
            customer_deployment_id = customer_deployment_id or os.getenv(
                "SACM_DEFAULT_CUSTOMER_DEPLOYMENT_ID", "local-development"
            )
            scope_key = f"customer-deployment:{customer_deployment_id}"
        contract = task.model_dump(mode="json")
        payload_hash = canonical_hash(contract)
        existing = (
            self.db.query(ExecutionJob)
            .filter(
                ExecutionJob.scope_key == scope_key,
                ExecutionJob.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise ExecutionConflict(
                    "The execution job idempotency key has a different payload."
                )
            return existing
        signature, signature_metadata = sign_control_plane_payload(contract)
        plan = (
            self.db.query(ExecutionPlan)
            .filter(ExecutionPlan.task_id == run.task_id)
            .order_by(ExecutionPlan.revision.desc())
            .first()
        )
        secret_requirements = (
            [item.request for item in plan.secret_requirements] if plan else []
        )
        now = _utcnow()
        job = ExecutionJob(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            project_id=project_id,
            customer_deployment_id=customer_deployment_id,
            scope_key=scope_key,
            tenant_attribution={
                "schema_version": "tenant-attribution/v1",
                "source": "execution job run scope",
                "organization_id": organization_id,
                "project_id": project_id,
            },
            run_id=run_id,
            run_step_id=run_step_id,
            task_id=run.task_id,
            state="QUEUED",
            idempotency_key=idempotency_key,
            required_capabilities=sorted(set(required_capabilities)),
            required_labels=dict(sorted((required_labels or {}).items())),
            secret_requirements=secret_requirements,
            payload_contract=contract,
            payload_hash=payload_hash,
            payload_signature=signature,
            payload_signature_metadata=signature_metadata,
            max_attempts=max_attempts,
            queued_at=now,
            created_at=now,
            updated_at=now,
        )
        self.db.add(job)
        self.runs._append_event(
            run,
            event_type="ExecutionJobQueued",
            actor="control-plane",
            payload={
                "job_id": job.id,
                "required_capabilities": job.required_capabilities,
                "payload_hash": payload_hash,
            },
            step_id=step.id,
        )
        self.runs._checkpoint(run, f"execution_job_queued:{job.id}")
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        self.db.refresh(job)
        return job

    def acquire_lease(
        self, executor: ExecutorRegistration, lease_seconds: int | None = None
    ) -> LeasedJob | None:
        self._require_active(executor)
        now = _utcnow()
        lease_seconds = lease_seconds or int(
            os.getenv("SACM_EXECUTION_LEASE_SECONDS", "120")
        )
        self.recover_expired(now=now, commit=False)
        query = (
            self.db.query(ExecutionJob)
            .filter(
                ExecutionJob.state == "QUEUED",
                ExecutionJob.attempt < ExecutionJob.max_attempts,
                self._scope_predicate(executor),
            )
            .order_by(ExecutionJob.queued_at, ExecutionJob.id)
            .limit(100)
        )
        if self._dialect() == "postgresql":
            query = query.with_for_update(skip_locked=True)
        for candidate in query.all():
            if not self._matches(executor, candidate):
                continue
            lease_token = secrets.token_urlsafe(48)
            values = {
                "state": "LEASED",
                "attempt": candidate.attempt + 1,
                "lease_owner_id": executor.id,
                "lease_token_hash": _token_hash("lease", lease_token),
                "leased_at": now,
                "lease_heartbeat_at": now,
                "lease_expires_at": now + timedelta(seconds=lease_seconds),
                "updated_at": now,
            }
            if self._dialect() == "sqlite":
                claimed = self.db.execute(
                    update(ExecutionJob)
                    .where(
                        ExecutionJob.id == candidate.id,
                        ExecutionJob.state == "QUEUED",
                    )
                    .values(**values)
                )
                if getattr(claimed, "rowcount", 0) != 1:
                    self.db.expire(candidate)
                    continue
                for key, value in values.items():
                    setattr(candidate, key, value)
            else:
                for key, value in values.items():
                    setattr(candidate, key, value)
            executor.last_heartbeat_at = now
            self.db.commit()
            self.db.refresh(candidate)
            return LeasedJob(job=candidate, lease_token=lease_token)
        self.db.commit()
        return None

    def start_job(
        self,
        executor: ExecutorRegistration,
        job_id: str,
        lease_token: str,
    ) -> ExecutionJob:
        job = self._leased_job(executor, job_id, lease_token)
        if job.state == "RUNNING":
            return job
        if job.state != "LEASED":
            raise ExecutionConflict(f"Job cannot start from {job.state}.")
        now = _utcnow()
        job.state = "RUNNING"
        job.started_at = now
        job.updated_at = now
        step = self.db.get(RunStep, job.run_step_id)
        if step is None:
            raise ValueError("Run step not found.")
        if step.status == "PENDING":
            self.runs.start_step(job.run_id, job.run_step_id)
        run = self.runs.get(job.run_id)
        if run is not None and run.status == "PLANNING":
            self.runs.transition(
                job.run_id,
                "IMPLEMENTING",
                "RemoteExecutionStarted",
                actor=f"executor:{executor.id}",
                step_id=job.run_step_id,
                payload={"job_id": job.id, "attempt": job.attempt},
            )
        else:
            self.db.commit()
        self.db.refresh(job)
        return job

    def heartbeat_job(
        self,
        executor: ExecutorRegistration,
        job_id: str,
        lease_token: str,
        lease_seconds: int | None = None,
    ) -> ExecutionJob:
        job = self._leased_job(executor, job_id, lease_token)
        if job.state not in {"LEASED", "RUNNING"}:
            raise ExecutionConflict(f"Job cannot heartbeat from {job.state}.")
        now = _utcnow()
        lease_seconds = lease_seconds or int(
            os.getenv("SACM_EXECUTION_LEASE_SECONDS", "120")
        )
        job.lease_heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.updated_at = now
        executor.last_heartbeat_at = now
        self.db.commit()
        self.db.refresh(job)
        return job

    def complete_job(
        self,
        executor: ExecutorRegistration,
        job_id: str,
        submission: SignedJobResult,
    ) -> ExecutionJob:
        return self._finish_job(executor, job_id, submission, failed=False)

    def fail_job(
        self,
        executor: ExecutorRegistration,
        job_id: str,
        submission: SignedJobResult,
    ) -> ExecutionJob:
        return self._finish_job(executor, job_id, submission, failed=True)

    def recover_expired(
        self, *, now: datetime | None = None, commit: bool = True
    ) -> list[ExecutionJob]:
        now = now or _utcnow()
        query = self.db.query(ExecutionJob).filter(
            ExecutionJob.state.in_(("LEASED", "RUNNING")),
            ExecutionJob.lease_expires_at.is_not(None),
            ExecutionJob.lease_expires_at <= now,
        )
        if self._dialect() == "postgresql":
            query = query.with_for_update(skip_locked=True)
        recovered = query.all()
        for job in recovered:
            self._release_or_expire(job, now, "LeaseExpired")
        if commit:
            self.db.commit()
        return recovered

    def recover_orphaned(
        self,
        *,
        now: datetime | None = None,
        organization_id: str | None = None,
        commit: bool = True,
    ) -> dict[str, Any]:
        now = now or _utcnow()
        query = self.db.query(ExecutionJob).filter(
            ExecutionJob.state.in_(("LEASED", "RUNNING"))
        )
        if organization_id:
            query = query.filter(ExecutionJob.organization_id == organization_id)
        if self._dialect() == "postgresql":
            query = query.with_for_update(skip_locked=True)
        jobs = query.all()
        recovered: list[ExecutionJob] = []
        requeued = 0
        dead_lettered = 0
        reconciled_steps = 0
        for job in jobs:
            owner = (
                self.db.get(ExecutorRegistration, job.lease_owner_id)
                if job.lease_owner_id
                else None
            )
            reason: str | None = None
            if job.lease_expires_at is None or job.lease_expires_at <= now:
                reason = "LeaseExpired"
            elif owner is None:
                reason = "OrphanedLeaseOwner"
            elif owner.status == "REVOKED":
                reason = "ExecutorRevoked"
            elif not job.lease_token_hash:
                reason = "OrphanedLeaseToken"
            if reason is None:
                continue
            self._release_or_expire(job, now, reason)
            recovered.append(job)
            if job.state == "DEAD_LETTER":
                dead_lettered += 1
            else:
                requeued += 1
            step = self.db.get(RunStep, job.run_step_id)
            if step and step.status == "RUNNING":
                step.retry_count += 1
                step.status = "FAILED" if job.state == "DEAD_LETTER" else "PENDING"
                if step.status == "PENDING":
                    step.started_at = None
                else:
                    step.completed_at = now
                reconciled_steps += 1
        if commit:
            self.db.commit()
        if recovered:
            from sacm.core.observability import OpenTelemetryService

            OpenTelemetryService(
                os.getenv("SACM_OTEL_ENABLED", "false").lower() == "true"
            ).record_resilience_event(
                "lease_recovery",
                len(recovered),
                {"dead_lettered": dead_lettered},
            )
        return {
            "recovered": len(recovered),
            "requeued": requeued,
            "dead_lettered": dead_lettered,
            "reconciled_steps": reconciled_steps,
            "job_ids": [job.id for job in recovered],
        }

    def list_jobs(
        self,
        *,
        organization_id: str | None = None,
        state: str | None = None,
        limit: int = 200,
    ) -> list[ExecutionJob]:
        query = self.db.query(ExecutionJob)
        if organization_id:
            query = query.filter(ExecutionJob.organization_id == organization_id)
        if state:
            query = query.filter(ExecutionJob.state == state)
        return query.order_by(ExecutionJob.created_at.desc()).limit(min(limit, 1000)).all()

    def requeue_dead_letter(
        self,
        job_id: str,
        *,
        reason: str,
        reset_attempts: bool = False,
    ) -> ExecutionJob:
        job = self.db.get(ExecutionJob, job_id)
        if job is None:
            raise ValueError("Execution job not found.")
        if job.state != "DEAD_LETTER":
            raise ExecutionConflict("Only dead-lettered jobs can be requeued.")
        now = _utcnow()
        job.state = "QUEUED"
        job.queued_at = now
        job.dead_lettered_at = None
        job.expired_at = None
        job.failure = None
        job.last_recovery_reason = f"ManualRequeue:{reason}"
        if reset_attempts:
            job.attempt = 0
        step = self.db.get(RunStep, job.run_step_id)
        if step and step.status == "FAILED":
            step.status = "PENDING"
            step.started_at = None
            step.completed_at = None
        self.db.commit()
        self.db.refresh(job)
        return job

    def _finish_job(
        self,
        executor: ExecutorRegistration,
        job_id: str,
        submission: SignedJobResult,
        *,
        failed: bool,
    ) -> ExecutionJob:
        job = self._owned_job(executor, job_id, submission.lease_token)
        result_data = submission.result.model_dump(mode="json")
        actual_hash = canonical_hash(result_data)
        if not hmac.compare_digest(actual_hash, submission.result_hash):
            raise ExecutionConflict("Result hash does not match the canonical result.")
        if job.state in {"COMPLETED", "FAILED"}:
            expected_state = "FAILED" if failed else "COMPLETED"
            if job.state == expected_state and hmac.compare_digest(
                job.result_hash or "", actual_hash
            ):
                if (job.result_signature_metadata or {}).get(
                    "analytics_status"
                ) == "FAILED":
                    self._refresh_job_analytics(job)
                return job
            raise ExecutionConflict("The job already has a different terminal result.")
        if job.state not in {"LEASED", "RUNNING"}:
            raise ExecutionConflict(f"Job cannot finish from {job.state}.")
        if submission.result.run_id != job.run_id or (
            submission.result.step_id != job.run_step_id
        ):
            raise ExecutionConflict("Result run_id and step_id do not match the job.")
        if failed and submission.result.status != "FAILED":
            raise ExecutionConflict("The fail endpoint requires a FAILED result.")
        if not failed and submission.result.status == "FAILED":
            raise ExecutionConflict("FAILED results must use the fail endpoint.")
        if not hmac.compare_digest(
            executor.signing_key_fingerprint,
            submission.signing_key_fingerprint,
        ):
            raise ExecutorAuthenticationError(
                "Result signing key fingerprint does not match the executor."
            )
        try:
            verify_ed25519(
                executor.public_signing_key,
                result_data,
                submission.signature,
            )
        except ValueError as exc:
            raise ExecutionConflict(str(exc)) from exc
        now = _utcnow()
        job.state = "FAILED" if failed else "COMPLETED"
        job.result_contract = result_data
        job.result_hash = actual_hash
        job.result_signature = submission.signature
        job.result_signature_metadata = {
            "algorithm": submission.signature_algorithm,
            "key_fingerprint": submission.signing_key_fingerprint,
            "executor_id": executor.id,
        }
        result_failure = submission.result.failure
        serialized_failure: dict[str, Any] | None = None
        if failed:
            if isinstance(result_failure, FailureInputV1):
                serialized_failure = result_failure.model_dump(mode="json")
            elif isinstance(result_failure, dict):
                serialized_failure = result_failure
        job.failure = serialized_failure
        job.updated_at = now
        if failed:
            job.failed_at = now
        else:
            job.completed_at = now
        from sacm.core.credential_lease_service import CredentialLeaseService

        CredentialLeaseService(self.db).revoke_for_job(
            job.id,
            f"executor:{executor.id}",
            "Execution job reached a terminal state.",
        )
        try:
            agent_submission = ExternalAgentService(self.db).submit(
                job.run_id,
                job.run_step_id,
                submission.result,
                trusted_internal=True,
                commit=not failed,
            )
            if (
                failed
                and agent_submission.recovery is not None
                and agent_submission.recovery.status == "SCHEDULED"
            ):
                retry_task = AgentTaskV1.model_validate(
                    agent_submission.step.input_["agent_task"]
                )
                self.schedule(
                    run_id=job.run_id,
                    run_step_id=job.run_step_id,
                    task=retry_task,
                    idempotency_key=(
                        f"{job.run_id}:remote-workflow:recovery:"
                        f"{agent_submission.recovery.attempt}"
                    ),
                    required_capabilities=job.required_capabilities,
                    required_labels=job.required_labels,
                    max_attempts=job.max_attempts,
                    customer_deployment_id=job.customer_deployment_id,
                    commit=False,
                )
            run = self.runs.get(job.run_id)
            if run is not None:
                self.runs._append_event(
                    run,
                    event_type=(
                        "ExecutionJobFailed" if failed else "ExecutionJobCompleted"
                    ),
                    actor=f"executor:{executor.id}",
                    payload={
                        "job_id": job.id,
                        "attempt": job.attempt,
                        "result_hash": actual_hash,
                        "signing_key_fingerprint": submission.signing_key_fingerprint,
                    },
                    step_id=job.run_step_id,
                )
                self.runs._checkpoint(
                    run,
                    (
                        f"execution_job_failed:{job.id}"
                        if failed
                        else f"execution_job_completed:{job.id}"
                    ),
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        analytics_error = (
            ExternalAgentService(self.db).refresh_analytics(job.run_id)
            if failed
            else agent_submission.analytics_error
        )
        if analytics_error:
            job.result_signature_metadata = {
                **(job.result_signature_metadata or {}),
                "analytics_status": "FAILED",
                "analytics_error": analytics_error,
            }
            self.db.commit()
        self.db.refresh(job)
        return job

    def _refresh_job_analytics(self, job: ExecutionJob) -> None:
        analytics_error = ExternalAgentService(self.db).refresh_analytics(job.run_id)
        metadata = dict(job.result_signature_metadata or {})
        if analytics_error:
            metadata.update(
                analytics_status="FAILED",
                analytics_error=analytics_error,
            )
        else:
            metadata["analytics_status"] = "COMPLETED"
            metadata.pop("analytics_error", None)
        job.result_signature_metadata = metadata
        self.db.commit()
        self.db.refresh(job)

    def _owned_job(
        self,
        executor: ExecutorRegistration,
        job_id: str,
        lease_token: str,
    ) -> ExecutionJob:
        self._require_active(executor)
        job = self.db.get(ExecutionJob, job_id)
        if job is None:
            raise ValueError("Execution job not found.")
        if job.lease_owner_id != executor.id:
            raise ExecutionAuthorizationError(
                "The execution job lease belongs to another executor."
            )
        if not job.lease_token_hash or not hmac.compare_digest(
            job.lease_token_hash, _token_hash("lease", lease_token)
        ):
            raise ExecutorAuthenticationError("Invalid execution job lease token.")
        return job

    def _leased_job(
        self,
        executor: ExecutorRegistration,
        job_id: str,
        lease_token: str,
    ) -> ExecutionJob:
        job = self._owned_job(executor, job_id, lease_token)
        if job.state in {"LEASED", "RUNNING"} and (
            job.lease_expires_at is None or job.lease_expires_at <= _utcnow()
        ):
            self._release_or_expire(job, _utcnow(), "LeaseExpired")
            self.db.commit()
            raise ExecutionConflict("Execution job lease has expired.")
        return job

    def _release_or_expire(
        self, job: ExecutionJob, now: datetime, reason: str
    ) -> None:
        terminal = job.attempt >= job.max_attempts
        job.state = "DEAD_LETTER" if terminal else "QUEUED"
        job.recovery_count += 1
        job.last_recovery_reason = reason
        job.failure = {
            "type": reason,
            "message": (
                "Execution job exhausted its lease attempts."
                if terminal
                else "Execution job lease was released for retry."
            ),
            "attempt": job.attempt,
        }
        if terminal:
            job.expired_at = now
            job.dead_lettered_at = now
        else:
            job.queued_at = now
        job.lease_owner_id = None
        job.lease_token_hash = None
        job.lease_expires_at = None
        job.lease_heartbeat_at = None
        job.leased_at = None
        job.updated_at = now
        from sacm.core.credential_lease_service import CredentialLeaseService

        CredentialLeaseService(self.db).revoke_for_job(
            job.id,
            "control-plane",
            reason,
        )

    def _scope_predicate(self, executor: ExecutorRegistration) -> Any:
        if executor.project_id:
            return ExecutionJob.project_id == executor.project_id
        if executor.organization_id:
            return ExecutionJob.organization_id == executor.organization_id
        return (
            ExecutionJob.customer_deployment_id
            == executor.customer_deployment_id
        )

    @staticmethod
    def _matches(
        executor: ExecutorRegistration, job: ExecutionJob
    ) -> bool:
        if not set(job.required_capabilities).issubset(set(executor.capabilities)):
            return False
        return all(
            executor.labels.get(key) == value
            for key, value in job.required_labels.items()
        )

    def _authorize_scope_admin(
        self, scope: ExecutorEnrollmentTokenCreate, actor: str
    ) -> str:
        tenancy = TenancyService(self.db)
        if scope.project_id:
            tenancy.require_project_permission(
                scope.project_id,
                actor,
                "executors.manage",
                resource_type="executor_scope",
                resource_id=scope.project_id,
            )
            return f"project:{scope.project_id}"
        if scope.organization_id:
            tenancy.require_permission(
                scope.organization_id,
                actor,
                "executors.manage",
                resource_type="executor_scope",
                resource_id=scope.organization_id,
            )
            return f"organization:{scope.organization_id}"
        if not self._platform_admin(actor):
            raise AuthorizationError(
                "Customer deployment executor administration requires a platform admin."
            )
        return f"customer-deployment:{scope.customer_deployment_id}"

    def _authorize_executor_admin(
        self, executor: ExecutorRegistration, actor: str
    ) -> None:
        scope = ExecutorEnrollmentTokenCreate(
            organization_id=executor.organization_id,
            project_id=executor.project_id,
            customer_deployment_id=executor.customer_deployment_id,
        )
        self._authorize_scope_admin(scope, actor)

    @staticmethod
    def _platform_admin(actor: str) -> bool:
        subjects = {
            item.strip()
            for item in os.getenv("SACM_PLATFORM_ADMIN_SUBJECTS", "").split(",")
            if item.strip()
        }
        return actor in subjects or (not production_mode() and not subjects)

    @staticmethod
    def _validate_sandbox_runtime(
        runtime: str, sandbox_policy: dict[str, Any]
    ) -> None:
        if not production_mode():
            return
        approved = {
            value.strip()
            for value in os.getenv(
                "SACM_APPROVED_SANDBOX_RUNTIMES", "runsc"
            ).split(",")
            if value.strip()
        }
        if runtime not in approved:
            raise ExecutionConflict(
                f"Sandbox runtime {runtime!r} is not approved; approved runtimes: "
                + ", ".join(sorted(approved))
            )
        if (
            sandbox_policy.get("runtime") != runtime
            or sandbox_policy.get("host_runtime_verified") is not True
            or sandbox_policy.get("no_new_privileges") is not True
            or not sandbox_policy.get("verification_command")
        ):
            raise ExecutionConflict(
                "Production enrollment requires a verified, fail-closed "
                "sandbox-policy/v1 contract for the selected runtime."
            )

    def _validate_network_boundary(
        self,
        organization_id: str | None,
        project_id: str | None,
        boundary: dict[str, Any],
        storage_region: str | None,
    ) -> None:
        if boundary.get("schema_version") != "executor-network-boundary/v1":
            if production_mode():
                raise ExecutionConflict(
                    "Production enrollment requires executor-network-boundary/v1."
                )
            return
        deployment_type = boundary.get("deployment_type")
        if deployment_type not in {"vpc", "vnet", "on-premises", "air-gapped"}:
            raise ExecutionConflict("Executor network boundary type is invalid.")
        if boundary.get("metadata_service_blocked") is not True:
            raise ExecutionConflict("Executor must block cloud metadata services.")
        region = boundary.get("residency_region")
        if not isinstance(region, str) or not region or (
            storage_region and region != storage_region
        ):
            raise ExecutionConflict(
                "Executor boundary residency must match its approved storage region."
            )
        boundary_id = boundary.get("boundary_id")
        if not isinstance(boundary_id, str) or not boundary_id or len(boundary_id) > 255:
            raise ExecutionConflict("Executor network boundary ID is invalid.")
        outbound_values = boundary.get("outbound_allowlist", [])
        if (
            not isinstance(outbound_values, list)
            or len(outbound_values) > 256
            or not all(isinstance(value, str) for value in outbound_values)
        ):
            raise ExecutionConflict("Executor outbound allowlist is invalid.")
        outbound = {
            str(value).lower().split(":", 1)[0]
            for value in outbound_values
        }
        if outbound & {
            "169.254.169.254",
            "metadata.google.internal",
            "100.100.100.200",
        }:
            raise ExecutionConflict("Executor boundary allows a metadata service.")
        if deployment_type == "air-gapped" and outbound:
            raise ExecutionConflict(
                "Air-gapped executor boundaries cannot allow outbound destinations."
            )
        proxy_url = boundary.get("proxy_url")
        if proxy_url:
            parsed_proxy = urlsplit(str(proxy_url))
            if (
                parsed_proxy.scheme not in {"http", "https"}
                or not parsed_proxy.hostname
                or parsed_proxy.username
                or parsed_proxy.password
            ):
                raise ExecutionConflict(
                    "Executor proxy metadata must be credential-free HTTP(S)."
                )
        tls = boundary.get("tls") or {}
        if not isinstance(tls, dict):
            raise ExecutionConflict("Executor TLS boundary metadata is invalid.")
        fingerprint = tls.get("server_certificate_sha256")
        signing_fingerprint = tls.get("signing_key_sha256")
        if production_mode() and (not fingerprint or not signing_fingerprint):
            raise ExecutionConflict(
                "Production executor boundaries require TLS and signing-key pins."
            )
        if fingerprint and (
            len(str(fingerprint)) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in fingerprint)
        ):
            raise ExecutionConflict("Control-plane TLS fingerprint metadata is invalid.")
        client_fingerprint = tls.get("client_certificate_sha256")
        if tls.get("mtls") is True and (
            not client_fingerprint
            or len(str(client_fingerprint)) != 64
            or any(
                character not in "0123456789abcdefABCDEF"
                for character in str(client_fingerprint)
            )
        ):
            raise ExecutionConflict("Executor mTLS certificate fingerprint is invalid.")
        if signing_fingerprint:
            if (
                len(str(signing_fingerprint)) != 64
                or any(
                    character not in "0123456789abcdefABCDEF"
                    for character in str(signing_fingerprint)
                )
            ):
                raise ExecutionConflict(
                    "Control-plane signing fingerprint metadata is invalid."
                )
            _, signing_metadata = sign_control_plane_payload(
                {"schema_version": "executor-signing-key-proof/v1"}
            )
            if not hmac.compare_digest(
                str(signing_fingerprint).lower(),
                signing_metadata["key_fingerprint"].lower(),
            ):
                raise ExecutionConflict(
                    "Executor declared the wrong control-plane signing key."
                )
        organization: Any | None = None
        project = self.db.get(Project, project_id) if project_id else None
        if project:
            organization_id = project.organization_id
        if organization_id:
            from sacm.infrastructure.db.models import Organization

            organization = self.db.get(Organization, organization_id)
        metadata: dict[str, Any] = {}
        if organization and organization.governance_metadata:
            metadata.update(organization.governance_metadata)
        if project and project.governance_metadata:
            metadata.update(project.governance_metadata)
        policy = metadata.get("executor_network_policy") or {}
        allowed_types = set(policy.get("allowed_boundary_types") or [])
        if allowed_types and deployment_type not in allowed_types:
            raise ExecutionConflict(
                "Executor boundary type is not allowed by organization policy."
            )
        allowed_regions = set(policy.get("allowed_regions") or [])
        if allowed_regions and region not in allowed_regions:
            raise ExecutionConflict(
                "Executor residency is not allowed by organization policy."
            )
        allowed_hosts = {
            str(value).lower().split(":", 1)[0]
            for value in policy.get("allowed_outbound_hosts") or []
        }
        if allowed_hosts and not outbound.issubset(allowed_hosts):
            raise ExecutionConflict(
                "Executor outbound allowlist exceeds organization policy."
            )
        if policy.get("require_proxy") and not boundary.get("proxy_url"):
            raise ExecutionConflict("Organization policy requires an executor proxy.")
        allowed_fingerprints = {
            str(value).lower()
            for value in policy.get("control_plane_tls_fingerprints") or []
        }
        if allowed_fingerprints and str(fingerprint).lower() not in allowed_fingerprints:
            raise ExecutionConflict(
                "Executor control-plane TLS fingerprint is not organization-approved."
            )
        allowed_signing_fingerprints = {
            str(value).lower()
            for value in policy.get("control_plane_signing_fingerprints") or []
        }
        if (
            allowed_signing_fingerprints
            and str(signing_fingerprint).lower()
            not in allowed_signing_fingerprints
        ):
            raise ExecutionConflict(
                "Executor control-plane signing key is not organization-approved."
            )

    @staticmethod
    def _validated_capacity(capacity: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "max_concurrent_jobs",
            "active_jobs",
            "cpu_units",
            "memory_mb",
            "workspace_bytes",
            "draining",
        }
        if set(capacity) - allowed:
            raise ExecutionConflict("Executor capacity report contains unknown fields.")
        result = {key: value for key, value in capacity.items() if value is not None}
        for key in allowed - {"draining"}:
            if key in result and (
                not isinstance(result[key], int) or result[key] < 0
            ):
                raise ExecutionConflict("Executor capacity values must be non-negative integers.")
        if "draining" in result and not isinstance(result["draining"], bool):
            raise ExecutionConflict("Executor draining capacity flag must be boolean.")
        return result

    def _organization_id(self, executor: ExecutorRegistration) -> str | None:
        if executor.organization_id:
            return executor.organization_id
        if executor.project_id:
            project = self.db.get(Project, executor.project_id)
            return project.organization_id if project else None
        return None

    @staticmethod
    def _version_tuple(value: str) -> tuple[int, int, int]:
        core = value.split("+", 1)[0].split("-", 1)[0]
        parts = core.split(".")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            raise ValueError("Executor versions must use semantic versioning.")
        return int(parts[0]), int(parts[1]), int(parts[2])

    @staticmethod
    def _signed_update_manifest(
        current_version: str, minimum_version: str
    ) -> dict[str, Any]:
        raw = os.getenv("SACM_EXECUTOR_UPDATE_MANIFEST")
        if raw:
            try:
                manifest = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ExecutionConflict(
                    "SACM_EXECUTOR_UPDATE_MANIFEST is not valid JSON."
                ) from exc
        else:
            manifest = {
                "schema_version": "executor-update-manifest/v1",
                "current_version": current_version,
                "minimum_version": minimum_version,
                "released_at": os.getenv(
                    "SACM_EXECUTOR_RELEASED_AT", "1970-01-01T00:00:00Z"
                ),
                "artifacts": [],
                "compatibility": {
                    "job_contracts": ["agent-task/v1"],
                    "result_contracts": ["agent-result/v1"],
                },
                "release_notes_uri": None,
            }
        if (
            manifest.get("schema_version") != "executor-update-manifest/v1"
            or manifest.get("current_version") != current_version
            or manifest.get("minimum_version") != minimum_version
        ):
            raise ExecutionConflict(
                "Executor update manifest versions do not match control-plane policy."
            )
        signature, signature_metadata = sign_control_plane_payload(manifest)
        return {
            "manifest": manifest,
            "manifest_hash": canonical_hash(manifest),
            "signature": signature,
            "signature_metadata": signature_metadata,
        }

    @staticmethod
    def _require_active(executor: ExecutorRegistration) -> None:
        if executor.status != "ACTIVE":
            raise ExecutorAuthenticationError("Executor is not active.")

    def _dialect(self) -> str:
        bind = self.db.get_bind()
        return bind.dialect.name
