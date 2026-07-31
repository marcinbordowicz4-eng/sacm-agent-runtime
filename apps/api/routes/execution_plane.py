from typing import Never

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from sacm.core.auth_service import require_authenticated_actor
from sacm.core.credential_lease_service import (
    CredentialLeaseError,
    CredentialLeaseService,
)
from sacm.core.execution_plane_service import (
    ExecutionAuthorizationError,
    ExecutionConflict,
    ExecutionPlaneService,
    ExecutorAuthenticationError,
)
from sacm.core.secret_broker import SecretProviderError
from sacm.core.tenancy_service import AuthorizationError
from sacm.infrastructure.db.models import ExecutorRegistration
from sacm.infrastructure.db.session import get_db
from sacm.schemas.execution_plane import (
    CredentialExchangeRequest,
    CredentialLeaseIssue,
    CredentialLeaseRead,
    CredentialLeaseRenew,
    CredentialLeaseRevoke,
    ExecutionJobRead,
    ExecutorEnroll,
    ExecutorEnrollmentResult,
    ExecutorEnrollmentTokenCreate,
    ExecutorEnrollmentTokenIssued,
    ExecutorFleetHealth,
    ExecutorHeartbeat,
    ExecutorRead,
    ExecutorRevoke,
    ExecutorRotate,
    ExecutorRotationResult,
    JobLease,
    JobLeaseMutation,
    JobLeaseRequest,
    SecretProviderConfigCreate,
    SecretProviderConfigRead,
    SecretProviderHealth,
    SignedJobResult,
    WrappedCredential,
)

router = APIRouter()


def _executor_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="Executor bearer token is required."
        )
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(
            status_code=401, detail="Executor bearer token is required."
        )
    return token


def _executor(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> ExecutorRegistration:
    try:
        return ExecutionPlaneService(db).authenticate_executor(
            _executor_token(authorization)
        )
    except ExecutorAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _raise_execution_error(exc: Exception) -> Never:
    if isinstance(exc, ExecutorAuthenticationError):
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if isinstance(exc, (ExecutionAuthorizationError, AuthorizationError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ValueError) and "not found" in str(exc).lower():
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/organizations/{organization_id}/secret-providers",
    response_model=SecretProviderConfigRead,
    status_code=201,
)
def configure_secret_provider(
    organization_id: str,
    payload: SecretProviderConfigCreate,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> SecretProviderConfigRead:
    try:
        record = CredentialLeaseService(db).create_provider_config(
            organization_id, actor, payload
        )
    except (AuthorizationError, SecretProviderError, ValueError) as exc:
        _raise_execution_error(exc)
    return SecretProviderConfigRead.model_validate(record)


@router.get(
    "/organizations/{organization_id}/secret-providers",
    response_model=list[SecretProviderConfigRead],
)
def list_secret_providers(
    organization_id: str,
    project_id: str | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[SecretProviderConfigRead]:
    try:
        records = CredentialLeaseService(db).list_provider_configs(
            organization_id, actor, project_id=project_id
        )
    except AuthorizationError as exc:
        _raise_execution_error(exc)
    return [SecretProviderConfigRead.model_validate(item) for item in records]


@router.get(
    "/organizations/{organization_id}/secret-providers/{config_id}/health",
    response_model=SecretProviderHealth,
)
def secret_provider_health(
    organization_id: str,
    config_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> SecretProviderHealth:
    try:
        config, details = CredentialLeaseService(db).provider_health(
            organization_id, config_id, actor
        )
    except (AuthorizationError, SecretProviderError, ValueError) as exc:
        _raise_execution_error(exc)
    return SecretProviderHealth.model_validate(
        {
            "id": config.id,
            "provider": config.provider,
            "healthy": bool(details.get("healthy")),
            "details": {
                key: value
                for key, value in details.items()
                if key != "healthy"
            },
        }
    )


@router.get(
    "/organizations/{organization_id}/credential-leases",
    response_model=list[CredentialLeaseRead],
)
def list_credential_leases(
    organization_id: str,
    project_id: str | None = None,
    job_id: str | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[CredentialLeaseRead]:
    try:
        records = CredentialLeaseService(db).list_leases(
            organization_id,
            actor,
            project_id=project_id,
            job_id=job_id,
        )
    except AuthorizationError as exc:
        _raise_execution_error(exc)
    return [CredentialLeaseRead.model_validate(item) for item in records]


@router.post(
    "/organizations/{organization_id}/credential-leases/{lease_id}/revoke",
    response_model=CredentialLeaseRead,
)
def revoke_credential_lease(
    organization_id: str,
    lease_id: str,
    payload: CredentialLeaseRevoke,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> CredentialLeaseRead:
    try:
        record = CredentialLeaseService(db).revoke(
            organization_id, lease_id, actor, payload.reason
        )
    except (AuthorizationError, CredentialLeaseError, ValueError) as exc:
        _raise_execution_error(exc)
    return CredentialLeaseRead.model_validate(record)


@router.post(
    "/executors/enrollment-tokens",
    response_model=ExecutorEnrollmentTokenIssued,
    status_code=201,
)
def issue_enrollment_token(
    payload: ExecutorEnrollmentTokenCreate,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> ExecutorEnrollmentTokenIssued:
    try:
        issued = ExecutionPlaneService(db).issue_enrollment_token(payload, actor)
    except (AuthorizationError, ValueError) as exc:
        _raise_execution_error(exc)
    return ExecutorEnrollmentTokenIssued(
        id=issued.record.id,
        enrollment_token=issued.token,
        scope_key=issued.record.scope_key,
        expires_at=issued.record.expires_at,
    )


@router.post(
    "/executors/enroll",
    response_model=ExecutorEnrollmentResult,
    status_code=201,
)
def enroll_executor(
    payload: ExecutorEnroll,
    db: Session = Depends(get_db),
) -> ExecutorEnrollmentResult:
    try:
        enrolled = ExecutionPlaneService(db).enroll(payload)
    except (ExecutorAuthenticationError, ExecutionConflict, RuntimeError) as exc:
        _raise_execution_error(exc)
    return ExecutorEnrollmentResult(
        executor=ExecutorRead.model_validate(enrolled.executor),
        auth_token=enrolled.auth_token,
    )


@router.get("/executors", response_model=list[ExecutorRead])
def list_executors(
    organization_id: str | None = None,
    project_id: str | None = None,
    customer_deployment_id: str | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[ExecutorRead]:
    try:
        executors = ExecutionPlaneService(db).list_executors(
            actor,
            organization_id=organization_id,
            project_id=project_id,
            customer_deployment_id=customer_deployment_id,
        )
    except (AuthorizationError, ValueError) as exc:
        _raise_execution_error(exc)
    return [ExecutorRead.model_validate(executor) for executor in executors]


@router.get("/executors/health", response_model=ExecutorFleetHealth)
def executor_fleet_health(
    organization_id: str | None = None,
    project_id: str | None = None,
    customer_deployment_id: str | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> ExecutorFleetHealth:
    try:
        service = ExecutionPlaneService(db)
        executors = service.list_executors(
            actor,
            organization_id=organization_id,
            project_id=project_id,
            customer_deployment_id=customer_deployment_id,
        )
        health = service.fleet_health(executors)
    except (AuthorizationError, ValueError) as exc:
        _raise_execution_error(exc)
    scope_key = (
        f"organization:{organization_id}"
        if organization_id
        else f"project:{project_id}"
        if project_id
        else f"customer-deployment:{customer_deployment_id}"
    )
    return ExecutorFleetHealth(scope_key=scope_key, **health)


@router.post("/executors/{executor_id}/revoke", response_model=ExecutorRead)
def revoke_executor(
    executor_id: str,
    payload: ExecutorRevoke,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> ExecutorRead:
    try:
        executor = ExecutionPlaneService(db).revoke_executor(
            executor_id, actor, payload.reason
        )
    except (AuthorizationError, ValueError) as exc:
        _raise_execution_error(exc)
    return ExecutorRead.model_validate(executor)


@router.post("/executor/heartbeat", response_model=ExecutorRead)
def heartbeat_executor(
    payload: ExecutorHeartbeat,
    executor: ExecutorRegistration = Depends(_executor),
    db: Session = Depends(get_db),
) -> ExecutorRead:
    try:
        result = ExecutionPlaneService(db).heartbeat_executor(
            executor,
            version=payload.version,
            network_boundary=payload.network_boundary,
            capabilities=payload.capabilities,
            labels=payload.labels,
            capacity=payload.capacity,
        )
        response = ExecutorRead.model_validate(result)
        return response.model_copy(
            update={
                "control": ExecutionPlaneService(db).control_directives(result)
            }
        )
    except (ExecutorAuthenticationError, ExecutionConflict) as exc:
        _raise_execution_error(exc)


@router.post("/executor/rotate", response_model=ExecutorRotationResult)
def rotate_executor(
    payload: ExecutorRotate,
    executor: ExecutorRegistration = Depends(_executor),
    db: Session = Depends(get_db),
) -> ExecutorRotationResult:
    try:
        rotated = ExecutionPlaneService(db).rotate_executor_identity(executor, payload)
    except (ExecutorAuthenticationError, ExecutionConflict) as exc:
        _raise_execution_error(exc)
    return ExecutorRotationResult(
        executor=ExecutorRead.model_validate(rotated.executor),
        auth_token=rotated.auth_token,
    )


@router.post(
    "/executor/credential-leases",
    response_model=CredentialLeaseRead,
    status_code=201,
)
def issue_credential_lease(
    payload: CredentialLeaseIssue,
    executor: ExecutorRegistration = Depends(_executor),
    db: Session = Depends(get_db),
) -> CredentialLeaseRead:
    try:
        record = CredentialLeaseService(db).issue(
            executor,
            job_id=payload.job_id,
            lease_token=payload.lease_token,
            requirement_name=payload.requirement_name,
            ttl_seconds=payload.ttl_seconds,
        )
    except (
        AuthorizationError,
        CredentialLeaseError,
        SecretProviderError,
        ValueError,
    ) as exc:
        _raise_execution_error(exc)
    return CredentialLeaseRead.model_validate(record)


@router.post(
    "/executor/credential-leases/{lease_id}/exchange",
    response_model=WrappedCredential,
)
def exchange_credential_material(
    lease_id: str,
    payload: CredentialExchangeRequest,
    executor: ExecutorRegistration = Depends(_executor),
    db: Session = Depends(get_db),
) -> WrappedCredential:
    try:
        wrapped = CredentialLeaseService(db).exchange(
            executor, lease_id, payload.lease_token
        )
    except (
        AuthorizationError,
        CredentialLeaseError,
        SecretProviderError,
        ValueError,
    ) as exc:
        _raise_execution_error(exc)
    return WrappedCredential.model_validate(
        {
            "ciphertext": wrapped.ciphertext,
            "wrapping_algorithm": wrapped.wrapping_algorithm,
            "encryption_key_fingerprint": wrapped.encryption_key_fingerprint,
            "content_type": wrapped.content_type,
            "expires_at": wrapped.expires_at,
        }
    )


@router.post(
    "/executor/credential-leases/{lease_id}/renew",
    response_model=CredentialLeaseRead,
)
def renew_credential_lease(
    lease_id: str,
    payload: CredentialLeaseRenew,
    executor: ExecutorRegistration = Depends(_executor),
    db: Session = Depends(get_db),
) -> CredentialLeaseRead:
    try:
        record = CredentialLeaseService(db).renew(
            executor,
            lease_id,
            payload.lease_token,
            payload.ttl_seconds,
        )
    except (
        AuthorizationError,
        CredentialLeaseError,
        SecretProviderError,
        ValueError,
    ) as exc:
        _raise_execution_error(exc)
    return CredentialLeaseRead.model_validate(record)


@router.post("/executor/jobs/lease", response_model=JobLease | None)
def lease_job(
    payload: JobLeaseRequest,
    response: Response,
    executor: ExecutorRegistration = Depends(_executor),
    db: Session = Depends(get_db),
) -> JobLease | None:
    try:
        leased = ExecutionPlaneService(db).acquire_lease(
            executor, payload.lease_seconds
        )
    except (ExecutorAuthenticationError, ExecutionConflict) as exc:
        _raise_execution_error(exc)
    if leased is None:
        response.status_code = 204
        return None
    job = leased.job
    return JobLease(
        job=ExecutionJobRead.model_validate(job),
        lease_token=leased.lease_token,
        payload_contract=job.payload_contract,
        payload_hash=job.payload_hash,
        payload_signature=job.payload_signature,
        payload_signature_metadata=job.payload_signature_metadata,
    )


@router.post("/executor/jobs/{job_id}/start", response_model=ExecutionJobRead)
def start_job(
    job_id: str,
    payload: JobLeaseMutation,
    executor: ExecutorRegistration = Depends(_executor),
    db: Session = Depends(get_db),
) -> ExecutionJobRead:
    try:
        job = ExecutionPlaneService(db).start_job(
            executor, job_id, payload.lease_token
        )
    except (
        ExecutorAuthenticationError,
        ExecutionAuthorizationError,
        ExecutionConflict,
        ValueError,
    ) as exc:
        _raise_execution_error(exc)
    return ExecutionJobRead.model_validate(job)


@router.post("/executor/jobs/{job_id}/heartbeat", response_model=ExecutionJobRead)
def heartbeat_job(
    job_id: str,
    payload: JobLeaseMutation,
    executor: ExecutorRegistration = Depends(_executor),
    db: Session = Depends(get_db),
) -> ExecutionJobRead:
    try:
        job = ExecutionPlaneService(db).heartbeat_job(
            executor,
            job_id,
            payload.lease_token,
            payload.lease_seconds,
        )
    except (
        ExecutorAuthenticationError,
        ExecutionAuthorizationError,
        ExecutionConflict,
        ValueError,
    ) as exc:
        _raise_execution_error(exc)
    return ExecutionJobRead.model_validate(job)


@router.post("/executor/jobs/{job_id}/complete", response_model=ExecutionJobRead)
def complete_job(
    job_id: str,
    payload: SignedJobResult,
    executor: ExecutorRegistration = Depends(_executor),
    db: Session = Depends(get_db),
) -> ExecutionJobRead:
    try:
        job = ExecutionPlaneService(db).complete_job(executor, job_id, payload)
    except (
        ExecutorAuthenticationError,
        ExecutionAuthorizationError,
        ExecutionConflict,
        ValueError,
    ) as exc:
        _raise_execution_error(exc)
    return ExecutionJobRead.model_validate(job)


@router.post("/executor/jobs/{job_id}/fail", response_model=ExecutionJobRead)
def fail_job(
    job_id: str,
    payload: SignedJobResult,
    executor: ExecutorRegistration = Depends(_executor),
    db: Session = Depends(get_db),
) -> ExecutionJobRead:
    try:
        job = ExecutionPlaneService(db).fail_job(executor, job_id, payload)
    except (
        ExecutorAuthenticationError,
        ExecutionAuthorizationError,
        ExecutionConflict,
        ValueError,
    ) as exc:
        _raise_execution_error(exc)
    return ExecutionJobRead.model_validate(job)
