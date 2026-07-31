from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from sacm.schemas.contracts import AgentResultV1

ExecutorStatus = Literal["ACTIVE", "OFFLINE", "REVOKED"]
ExecutionJobState = Literal[
    "QUEUED",
    "LEASED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "EXPIRED",
    "CANCELLED",
    "DEAD_LETTER",
]


class ExecutorScope(BaseModel):
    organization_id: str | None = None
    project_id: str | None = None
    customer_deployment_id: str | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "ExecutorScope":
        if (
            sum(
                value is not None
                for value in (
                    self.organization_id,
                    self.project_id,
                    self.customer_deployment_id,
                )
            )
            != 1
        ):
            raise ValueError(
                "Exactly one organization_id, project_id, or "
                "customer_deployment_id is required."
            )
        return self


class ExecutorEnrollmentTokenCreate(ExecutorScope):
    expires_in_seconds: int = Field(default=900, ge=60, le=86400)


class ExecutorEnrollmentTokenIssued(BaseModel):
    id: str
    enrollment_token: str
    scope_key: str
    expires_at: datetime


class SandboxPolicyV1(BaseModel):
    schema_version: Literal["sandbox-policy/v1"] = "sandbox-policy/v1"
    runtime: str = Field(min_length=1, max_length=100)
    host_runtime_verified: bool = False
    verification_command: str | None = Field(default=None, max_length=1000)
    isolation: Literal["user-space-kernel", "microvm", "policy-approved"]
    network_mode: Literal["deny-by-default", "restricted-egress"]
    no_new_privileges: bool = True


class ExecutorEnroll(BaseModel):
    enrollment_token: str = Field(min_length=32)
    executor_identity: str = Field(
        min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9_.:@/-]+$"
    )
    display_name: str = Field(min_length=1, max_length=255)
    capabilities: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    runtime_kind: str = Field(min_length=1, max_length=100)
    sandbox_runtime: str = Field(min_length=1, max_length=100)
    sandbox_policy: SandboxPolicyV1 | None = None
    public_signing_key: str = Field(min_length=32)
    public_encryption_key: str | None = Field(default=None, min_length=32)
    version: str = Field(min_length=1, max_length=100)
    network_boundary: dict[str, Any] = Field(default_factory=dict)
    storage_region: str | None = Field(default=None, min_length=1, max_length=100)
    storage_classification: Literal[
        "Public", "Internal", "Confidential", "Restricted"
    ] | None = None
    storage_class: str | None = Field(default=None, min_length=1, max_length=100)


class ExecutorRead(BaseModel):
    id: str
    organization_id: str | None
    project_id: str | None
    customer_deployment_id: str | None
    scope_key: str
    executor_identity: str
    display_name: str
    capabilities: list[str]
    labels: dict[str, str]
    runtime_kind: str
    sandbox_runtime: str
    sandbox_policy: dict[str, Any]
    signing_key_fingerprint: str
    encryption_key_fingerprint: str | None
    status: ExecutorStatus
    last_heartbeat_at: datetime | None
    version: str
    network_boundary: dict[str, Any]
    storage_region: str | None
    storage_classification: str | None
    storage_class: str | None
    enrolled_at: datetime
    updated_at: datetime
    revoked_at: datetime | None
    revoked_by: str | None
    revocation_reason: str | None
    control: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class ExecutorEnrollmentResult(BaseModel):
    executor: ExecutorRead
    auth_token: str


class ExecutorRevoke(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class ExecutorHeartbeat(BaseModel):
    version: str | None = Field(default=None, min_length=1, max_length=100)
    network_boundary: dict[str, Any] | None = None
    capabilities: list[str] | None = None
    labels: dict[str, str] | None = None
    capacity: dict[str, Any] | None = None


class ExecutorRotate(BaseModel):
    public_signing_key: str = Field(min_length=32)
    signing_key_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExecutorRotationResult(BaseModel):
    executor: ExecutorRead
    auth_token: str


class ExecutorFleetHealth(BaseModel):
    scope_key: str
    total: int
    active: int
    offline: int
    revoked: int
    draining: int
    capacity: dict[str, int]
    slo: dict[str, Any]


class ExecutionJobRead(BaseModel):
    id: str
    organization_id: str | None
    project_id: str | None
    customer_deployment_id: str | None
    scope_key: str
    run_id: str
    run_step_id: str
    task_id: str
    state: ExecutionJobState
    idempotency_key: str
    required_capabilities: list[str]
    required_labels: dict[str, str]
    secret_requirements: list[dict[str, Any]]
    payload_hash: str
    attempt: int
    max_attempts: int
    lease_owner_id: str | None
    lease_expires_at: datetime | None
    lease_heartbeat_at: datetime | None
    result_hash: str | None
    failure: dict[str, Any] | None
    queued_at: datetime
    leased_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    expired_at: datetime | None
    cancelled_at: datetime | None

    model_config = {"from_attributes": True}


class JobLeaseRequest(BaseModel):
    lease_seconds: int | None = Field(default=None, ge=15, le=3600)


class JobLease(BaseModel):
    job: ExecutionJobRead
    lease_token: str
    payload_contract: dict[str, Any]
    payload_hash: str
    payload_signature: str
    payload_signature_metadata: dict[str, Any]


class JobLeaseMutation(BaseModel):
    lease_token: str = Field(min_length=32)
    lease_seconds: int | None = Field(default=None, ge=15, le=3600)


class SignedJobResult(BaseModel):
    lease_token: str = Field(min_length=32)
    result: AgentResultV1
    result_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    signature: str = Field(min_length=16)
    signature_algorithm: Literal["Ed25519"] = "Ed25519"
    signing_key_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


SecretProviderName = Literal[
    "environment",
    "vault",
    "aws-secrets-manager",
    "aws-sts",
    "azure-key-vault",
    "azure-managed-identity",
]


class SecretProviderConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    project_id: str | None = None
    provider: SecretProviderName
    enabled: bool = True
    approved_for_production: bool = False
    config_metadata: dict[str, Any] = Field(default_factory=dict)


class SecretProviderConfigRead(BaseModel):
    id: str
    organization_id: str
    project_id: str | None
    name: str
    provider: SecretProviderName
    enabled: bool
    approved_for_production: bool
    config_metadata: dict[str, Any]
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SecretProviderHealth(BaseModel):
    id: str
    provider: SecretProviderName
    healthy: bool
    details: dict[str, Any] = Field(default_factory=dict)


class CredentialLeaseIssue(BaseModel):
    job_id: str
    lease_token: str = Field(min_length=32)
    requirement_name: str = Field(min_length=1, max_length=255)
    ttl_seconds: int = Field(default=300, ge=15, le=900)


class CredentialLeaseRead(BaseModel):
    id: str
    organization_id: str | None
    project_id: str | None
    task_id: str
    run_id: str
    job_id: str
    executor_id: str
    requirement_name: str
    requested_permissions: list[str]
    resource: str | None
    provider: SecretProviderName
    provider_config_id: str | None
    opaque_handle: str
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None
    use_count: int
    audience: str | None
    policy_decision: dict[str, Any]
    provider_lease_id_hash: str | None
    revoked_by: str | None
    revocation_reason: str | None

    model_config = {"from_attributes": True}


class CredentialExchangeRequest(BaseModel):
    lease_token: str = Field(min_length=32)


class WrappedCredential(BaseModel):
    ciphertext: str
    wrapping_algorithm: Literal["RSA-OAEP-256+A256GCM"]
    encryption_key_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_type: str
    expires_at: datetime


class CredentialLeaseRenew(BaseModel):
    lease_token: str = Field(min_length=32)
    ttl_seconds: int = Field(ge=15, le=900)


class CredentialLeaseRevoke(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
