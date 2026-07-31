from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BackupCreate(BaseModel):
    organization_id: str | None = None
    source_database: str = Field(min_length=1, max_length=255)
    storage_uri: str = Field(min_length=1)
    storage_region: str | None = Field(default=None, min_length=1, max_length=100)
    storage_classification: Literal[
        "Public", "Internal", "Confidential", "Restricted"
    ] | None = None
    storage_class: str | None = Field(default=None, min_length=1, max_length=100)
    rpo_target_seconds: int = Field(gt=0)
    rto_target_seconds: int = Field(gt=0)
    encryption_metadata: dict[str, Any] = Field(default_factory=dict)
    artifact_metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_metadata: dict[str, Any] = Field(default_factory=dict)
    execute: bool = False


class BackupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    schema_version: str
    scope_type: str
    organization_id: str | None
    backup_type: str
    source_database: str
    storage_uri: str
    storage_region: str | None
    storage_classification: str | None
    storage_class: str | None
    status: str
    checksum_algorithm: str
    checksum: str | None
    encryption_metadata: dict[str, Any]
    artifact_metadata: dict[str, Any]
    evidence_metadata: dict[str, Any]
    rpo_target_seconds: int
    rto_target_seconds: int
    snapshot_at: datetime | None
    size_bytes: int | None
    failure: dict[str, Any] | None
    requested_by: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class RestoreVerificationRequest(BaseModel):
    destructive_restore: bool = False
    target_database: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")
    guard_token: str | None = Field(default=None, min_length=16)
    keep_isolated_database: bool = False


class DRDrillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    schema_version: str
    backup_id: str
    organization_id: str | None
    status: str
    target_database: str
    isolated_target: bool
    destructive_restore: bool
    checks: dict[str, Any]
    measured_rpo_seconds: float | None
    measured_rto_seconds: float | None
    failure: dict[str, Any] | None
    requested_by: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


SLOMetric = Literal[
    "availability",
    "job_start_latency",
    "completion_rate",
    "evidence_coverage",
    "audit_delivery",
    "governance_backlog",
    "backup_freshness",
    "rpo",
    "rto",
    "governance_backlog",
]


class SLOContractUpsert(BaseModel):
    metric: SLOMetric
    objective_percent: float = Field(gt=0, le=100)
    threshold_seconds: float | None = Field(default=None, gt=0)
    window_seconds: int = Field(ge=60)
    enabled: bool = True
    description: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_threshold(self) -> "SLOContractUpsert":
        if self.metric in {
            "job_start_latency",
            "backup_freshness",
            "rpo",
            "rto",
        } and self.threshold_seconds is None:
            raise ValueError(f"{self.metric} requires threshold_seconds.")
        return self


class SLOContractRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    schema_version: str
    scope_key: str
    organization_id: str | None
    metric: str
    objective_percent: float
    threshold_seconds: float | None
    window_seconds: int
    enabled: bool
    description: str
    created_by: str
    created_at: datetime
    updated_at: datetime


class SLOEvaluationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contract_id: str
    organization_id: str | None
    metric: str
    status: str
    observed_percent: float | None
    total_events: int
    bad_events: int
    error_budget_allowed: float
    error_budget_remaining: float
    details: dict[str, Any]
    window_started_at: datetime
    window_ended_at: datetime
    computed_at: datetime


class RecoveryRequest(BaseModel):
    organization_id: str | None = None


class DeadLetterRequeueRequest(BaseModel):
    reason: str = Field(min_length=1)
    reset_attempts: bool = False


class RecoveryReport(BaseModel):
    recovered: int
    requeued: int
    dead_lettered: int
    reconciled_steps: int
    job_ids: list[str]


class OperationalHealthRead(BaseModel):
    status: str
    generated_at: datetime
    checks: dict[str, Any]
    queue: dict[str, Any]
    executors: dict[str, Any]
    backup: dict[str, Any]
    audit: dict[str, Any]
    governance: dict[str, Any]
    signing: dict[str, Any]
