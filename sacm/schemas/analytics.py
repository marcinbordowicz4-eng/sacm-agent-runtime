from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Outcome = Literal["success", "failure", "cancelled"]
DataState = Literal["complete", "partial", "legacy"]


class StepOutcomeAnalyticsV1(BaseModel):
    schema_version: Literal["step-outcome-analytics/v1"]
    step_id: str
    sequence: int
    name: str
    status: str
    outcome: Outcome | None
    latency_ms: int | None
    retry_count: int
    agent_name: str | None
    provider: str | None
    model: str | None
    framework: str | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: float | None
    evidence_count: int
    requirement_count: int
    changed_file_count: int
    test_count: int
    verification_count: int
    failure: dict[str, Any] | None
    details: dict[str, Any] = Field(default_factory=dict)
    computed_at: datetime


class AgentOutcomeAnalyticsV1(BaseModel):
    schema_version: Literal["agent-outcome-analytics/v1"]
    invocation_id: str
    source_event_id: str
    step_id: str | None
    agent_name: str
    role: str | None
    provider: str | None
    model: str | None
    framework: str | None
    status: str | None
    outcome: Outcome | None
    latency_ms: int | None
    retry_count: int
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: float | None
    evidence_count: int
    requirement_count: int
    security_finding_count: int
    changed_file_count: int
    test_count: int
    verification_count: int
    failure: dict[str, Any] | None
    legacy_attribution: bool
    details: dict[str, Any] = Field(default_factory=dict)
    computed_at: datetime


class RunOutcomeAnalyticsV1(BaseModel):
    schema_version: Literal["outcome-analytics/v1"]
    run_id: str
    task_id: str
    project_id: str | None
    organization_id: str | None
    status: str
    outcome: Outcome | None
    latency_ms: int | None
    retry_count: int
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: float | None
    cost_estimation_available: bool
    evidence_pack_count: int
    evidence_coverage_percent: float | None
    requirement_coverage_percent: float | None
    policy_blocked: bool | None
    approval_count: int
    pending_approval_count: int
    approved_approval_count: int
    rejected_approval_count: int
    security_finding_count: int | None
    open_security_finding_count: int | None
    high_critical_security_finding_count: int | None
    source_run_id: str | None
    source_snapshot_id: str | None
    replay_count: int
    changed_file_count: int
    test_count: int
    verification_count: int
    step_count: int
    agent_invocation_count: int
    legacy_data: bool
    data_state: DataState
    data_completeness: dict[str, bool]
    details: dict[str, Any] = Field(default_factory=dict)
    steps: list[StepOutcomeAnalyticsV1] = Field(default_factory=list)
    agents: list[AgentOutcomeAnalyticsV1] = Field(default_factory=list)
    computed_at: datetime


class AggregateOutcomeAnalyticsV1(BaseModel):
    schema_version: Literal["aggregate-outcome-analytics/v1"] = (
        "aggregate-outcome-analytics/v1"
    )
    scope_type: Literal["task", "project", "organization"]
    scope_id: str
    scope_name: str | None
    run_count: int
    terminal_run_count: int
    success_count: int
    failure_count: int
    cancelled_count: int
    success_rate_percent: float | None
    average_latency_ms: float | None
    retry_count: int
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: float | None
    cost_estimation_available: bool
    evidence_pack_count: int
    average_evidence_coverage_percent: float | None
    average_requirement_coverage_percent: float | None
    policy_blocked_run_count: int
    approval_count: int
    pending_approval_count: int
    security_finding_count: int | None
    open_security_finding_count: int | None
    changed_file_count: int
    test_count: int
    verification_count: int
    step_count: int
    agent_invocation_count: int
    legacy_run_count: int
    incomplete_run_count: int
    runs: list[RunOutcomeAnalyticsV1] = Field(default_factory=list)
    computed_at: datetime
