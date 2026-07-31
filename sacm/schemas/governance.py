from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DataClassification = Literal["Public", "Internal", "Confidential", "Restricted"]
ResourceCategory = Literal[
    "source_context",
    "task_metadata",
    "runtime_events",
    "logs",
    "artifacts",
    "evidence",
    "backups",
    "analytics",
    "audit",
]


class GovernanceRuleInput(BaseModel):
    resource_category: ResourceCategory
    classification: DataClassification
    retention_days: int | None = Field(default=None, ge=0)
    legal_hold: bool = False
    deletion_mode: Literal["TOMBSTONE", "CRYPTOGRAPHIC", "HARD_DELETE"] = "TOMBSTONE"
    exportable: bool = True
    allowed_regions: list[str] = Field(min_length=1)
    storage_classes: list[str] = Field(min_length=1)
    evidence_preservation: Literal["PRESERVE", "TOMBSTONE", "DELETE"] = "PRESERVE"
    metadata: dict[str, Any] = Field(default_factory=dict)


class GovernancePolicyCreate(BaseModel):
    project_id: str | None = None
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    rules: list[GovernanceRuleInput] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_categories(self) -> "GovernancePolicyCreate":
        categories = [rule.resource_category for rule in self.rules]
        if len(categories) != len(set(categories)):
            raise ValueError("Policy resource categories must be unique.")
        return self


class GovernancePolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    project_id: str | None
    scope_key: str
    name: str
    version: int
    status: str
    description: str
    created_by: str
    activated_by: str | None
    created_at: datetime
    activated_at: datetime | None
    retired_at: datetime | None
    rules: list[dict[str, Any]] = Field(default_factory=list)


class GovernanceRequestCreate(BaseModel):
    project_id: str | None = None
    request_type: Literal["EXPORT", "DELETION"]
    subject_type: Literal["TENANT", "DATA_SUBJECT"] = "TENANT"
    subject_id: str | None = Field(default=None, min_length=1, max_length=1024)
    requested_categories: list[ResourceCategory] = Field(default_factory=list)
    evidence_preservation_policy: Literal["PRESERVE", "TOMBSTONE", "DELETE"] = (
        "PRESERVE"
    )

    @model_validator(mode="after")
    def require_subject(self) -> "GovernanceRequestCreate":
        if self.subject_type == "DATA_SUBJECT" and not self.subject_id:
            raise ValueError("DATA_SUBJECT requests require subject_id.")
        return self


class GovernanceApproval(BaseModel):
    approved: bool = True
    reason: str = Field(min_length=1, max_length=4000)


class GovernanceProcessRequest(BaseModel):
    batch_size: int = Field(default=100, ge=1, le=1000)


class GovernanceLegalHoldCreate(BaseModel):
    project_id: str | None = None
    resource_category: ResourceCategory | None = None
    resource_type: str | None = Field(default=None, max_length=255)
    resource_id: str | None = Field(default=None, max_length=255)
    subject_id: str | None = Field(default=None, max_length=1024)
    reason: str = Field(min_length=1, max_length=4000)


class AuditExportCreate(BaseModel):
    project_id: str | None = None
    start_sequence: int | None = Field(default=None, ge=1)
    end_sequence: int | None = Field(default=None, ge=1)
    limit: int = Field(default=1000, ge=1, le=10000)


class SIEMSinkCreate(BaseModel):
    project_id: str | None = None
    name: str = Field(min_length=1, max_length=255)
    sink_type: Literal["HTTP_WEBHOOK", "SYSLOG", "FILE", "OBJECT_STORAGE"]
    endpoint: str | None = None
    allowed_hosts: list[str] = Field(default_factory=list)
    storage_metadata: dict[str, Any] = Field(default_factory=dict)
    credential_reference: str | None = Field(default=None, min_length=1)
    signing_reference: str | None = Field(default=None, min_length=1)
    batch_size: int = Field(default=100, ge=1, le=1000)
    max_attempts: int = Field(default=5, ge=1, le=20)
    backoff_seconds: int = Field(default=30, ge=1, le=86400)


class SIEMSinkUpdate(BaseModel):
    status: Literal["ACTIVE", "PAUSED", "DISABLED"] | None = None
    endpoint: str | None = None
    allowed_hosts: list[str] | None = None
    storage_metadata: dict[str, Any] | None = None
    credential_reference: str | None = None
    signing_reference: str | None = None
    batch_size: int | None = Field(default=None, ge=1, le=1000)
    max_attempts: int | None = Field(default=None, ge=1, le=20)
    backoff_seconds: int | None = Field(default=None, ge=1, le=86400)


class SIEMDrainRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=1000)
