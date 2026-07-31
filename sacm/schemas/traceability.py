from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

TraceabilityTargetType = Literal[
    "execution_plan_step",
    "agent",
    "context_event",
    "runtime_event",
    "run_step",
    "commit",
    "diff",
    "changed_file",
    "test",
    "verification",
    "security_finding",
    "policy_decision",
    "approval",
    "artifact",
    "evidence_pack",
]
RequirementLinkSource = Literal["derived", "external"]


class RequirementV1(BaseModel):
    schema_version: Literal["requirement/v1"] = "requirement/v1"
    id: str
    task_id: str
    stable_hash: str
    position: int = Field(ge=1)
    title: str
    text: str
    normalized_text: str
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class RequirementLinkV1(BaseModel):
    schema_version: Literal["requirement-link/v1"] = "requirement-link/v1"
    id: str
    task_id: str
    requirement_id: str
    run_id: str | None = None
    target_type: TraceabilityTargetType
    target_id: str
    relation: str
    source: RequirementLinkSource
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class RequirementLinkCreateV1(BaseModel):
    schema_version: Literal["requirement-link-create/v1"] = (
        "requirement-link-create/v1"
    )
    requirement_id: str
    run_id: str | None = None
    target_type: TraceabilityTargetType
    target_id: str = Field(min_length=1, max_length=2000)
    relation: str = Field(default="supports", min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RequirementCoverageV1(BaseModel):
    schema_version: Literal["requirement-coverage/v1"] = "requirement-coverage/v1"
    total_requirements: int = Field(ge=0)
    covered_requirements: int = Field(ge=0)
    uncovered_requirements: list[RequirementV1] = Field(default_factory=list)
    coverage_percent: float = Field(ge=0, le=100)
    evidence_covered_requirements: int = Field(ge=0)
    evidence_coverage_percent: float = Field(ge=0, le=100)
    linked_requirements_by_target_type: dict[str, int] = Field(default_factory=dict)
    link_count_by_target_type: dict[str, int] = Field(default_factory=dict)


class TraceabilityV1(BaseModel):
    schema_version: Literal["traceability/v1"] = "traceability/v1"
    task_id: str
    requirements: list[RequirementV1]
    links: list[RequirementLinkV1]
    coverage: RequirementCoverageV1
    refreshed_at: datetime
