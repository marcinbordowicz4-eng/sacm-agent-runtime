from typing import Any, Literal

from pydantic import BaseModel, Field

AgentRole = Literal["reasoner", "coder", "reviewer", "tester", "security"]


class ArtifactReference(BaseModel):
    artifact_type: str
    uri: str | None = None
    sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UsageRecord(BaseModel):
    provider: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)


class AgentTaskV1(BaseModel):
    schema_version: Literal["agent-task/v1"] = "agent-task/v1"
    run_id: str
    step_id: str
    role: AgentRole
    objective: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(default_factory=list)
    context_references: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    token_budget: int = Field(gt=0)
    cost_budget_usd: float | None = Field(default=None, ge=0)
    timeout_seconds: int = Field(gt=0)
    output_schema: str = "agent-result/v1"
    execution_context: dict[str, Any] = Field(default_factory=dict)


class AgentResultV1(BaseModel):
    schema_version: Literal["agent-result/v1"] = "agent-result/v1"
    run_id: str
    step_id: str
    status: Literal["COMPLETED", "FAILED", "NEEDS_APPROVAL"]
    summary: str
    artifacts: list[ArtifactReference] = Field(default_factory=list)
    evidence: list[ArtifactReference] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    usage: list[UsageRecord] = Field(default_factory=list)
    failure: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    next_state_hint: str = ""
    memory_update: str | None = None
    skills_contributed: list[dict[str, Any]] = Field(default_factory=list)


class ExternalAgentStepCreate(BaseModel):
    framework: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    agent_name: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=255)
    role: AgentRole
    objective: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(default_factory=list)
    context_references: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    token_budget: int = Field(gt=0)
    cost_budget_usd: float | None = Field(default=None, ge=0)
    timeout_seconds: int = Field(gt=0)
    execution_context: dict[str, Any] = Field(default_factory=dict)


class ExternalAgentResultSubmit(BaseModel):
    result: AgentResultV1
