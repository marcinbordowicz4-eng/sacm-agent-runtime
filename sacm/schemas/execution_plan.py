from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

AgentRole = Literal["reasoner", "coder", "reviewer", "tester", "security"]
PolicyPackName = Literal["default", "strict"]


class AgentConfigurationV1(BaseModel):
    """Portable agent configuration; execution adapters own framework integration."""

    schema_version: Literal["agent-configuration/v1"] = "agent-configuration/v1"
    runtime_kind: Literal["registered", "external"] = "registered"
    agent_name: str = Field(min_length=1, max_length=255)
    role: AgentRole
    implementation_ref: str = Field(min_length=1, max_length=500)
    task_contract: Literal["agent-task/v1"] = "agent-task/v1"
    result_contract: Literal["agent-result/v1"] = "agent-result/v1"
    capabilities: list[str] = Field(default_factory=list)
    configuration: dict[str, Any] = Field(default_factory=dict)


class RiskDecisionV1(BaseModel):
    schema_version: Literal["risk-decision/v1"] = "risk-decision/v1"
    score: int = Field(ge=0, le=100)
    level: Literal["low", "medium", "high", "critical"]
    factors: list[dict[str, Any]] = Field(default_factory=list)
    required_controls: list[str] = Field(default_factory=list)


class PolicyRuleMatchV1(BaseModel):
    rule_id: str
    effect: Literal["allow", "deny", "approval", "security_review"]
    reason: str
    step_ids: list[str] = Field(default_factory=list)


class PolicyApprovalRequirementV1(BaseModel):
    gate_type: str
    action: str
    reason: str
    step_ids: list[str] = Field(default_factory=list)


class PolicyDecisionV1(BaseModel):
    """OPA-compatible structured result stored exactly as an execution decision."""

    schema_version: Literal["policy-decision/v1"] = "policy-decision/v1"
    pack: PolicyPackName
    allow: bool
    requires_approval: bool
    requires_security_review: bool
    matched_rules: list[PolicyRuleMatchV1] = Field(default_factory=list)
    approval_gates: list[PolicyApprovalRequirementV1] = Field(
        default_factory=list
    )
    denials: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)


class SecurityFindingV1(BaseModel):
    schema_version: Literal["security-finding/v1"] = "security-finding/v1"
    finding_id: str
    severity: Literal["info", "low", "medium", "high", "critical"]
    code: str
    title: str
    description: str
    step_ids: list[str] = Field(default_factory=list)
    status: Literal["open", "accepted", "resolved"] = "open"
    remediation: str | None = None


class SecurityReviewV1(BaseModel):
    schema_version: Literal["security-review/v1"] = "security-review/v1"
    required: bool = True
    status: Literal["PENDING", "APPROVED", "CHANGES_REQUIRED"]
    reviewer: AgentConfigurationV1
    findings: list[SecurityFindingV1] = Field(default_factory=list)
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None


class SecretRequestV1(BaseModel):
    schema_version: Literal["secret-request/v1"] = "secret-request/v1"
    name: str = Field(min_length=1, max_length=255)
    purpose: str = Field(min_length=1, max_length=1000)
    environment_variable: str = Field(
        min_length=1, max_length=255, pattern=r"^[A-Z][A-Z0-9_]*$"
    )
    required: bool = True
    step_keys: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class SecretReferenceV1(BaseModel):
    schema_version: Literal["secret-reference/v1"] = "secret-reference/v1"
    request_name: str
    handle: str
    source: Literal["environment"]
    available: bool
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def prohibit_secret_material(cls, value: dict[str, Any]) -> dict[str, Any]:
        prohibited = {"value", "secret", "secret_value", "token", "password"}
        if any(str(key).lower() in prohibited for key in value):
            raise ValueError("Secret reference metadata cannot contain secret material.")
        return value


class ApprovalGateV1(BaseModel):
    schema_version: Literal["approval-gate/v1"] = "approval-gate/v1"
    id: str
    gate_type: str
    action: str
    reason: str
    status: Literal["PENDING", "APPROVED", "REJECTED", "NOT_REQUIRED"]
    step_ids: list[str] = Field(default_factory=list)
    approval_id: str | None = None


class ExecutionPlanStepV1(BaseModel):
    schema_version: Literal["execution-plan-step/v1"] = "execution-plan-step/v1"
    id: str
    sequence: int = Field(ge=1)
    stable_key: str
    kind: Literal["implementation", "verification", "security_review"]
    title: str
    objective: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    context_references: list[str] = Field(default_factory=list)
    impacted_node_ids: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    agent: AgentConfigurationV1


class ExecutionPlanV1(BaseModel):
    schema_version: Literal["execution-plan/v1"] = "execution-plan/v1"
    id: str
    task_id: str
    application_context_id: str
    revision: int = Field(ge=1)
    planner_version: Literal["deterministic-planner/v1"]
    source_hash: str
    status: Literal["READY", "GATED", "BLOCKED"]
    policy_pack: PolicyPackName
    steps: list[ExecutionPlanStepV1]
    risk_decision: RiskDecisionV1
    policy_decision: PolicyDecisionV1
    security_review: SecurityReviewV1
    secret_requirements: list[SecretRequestV1] = Field(default_factory=list)
    secret_references: list[SecretReferenceV1] = Field(default_factory=list)
    approval_gates: list[ApprovalGateV1] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ExecutionPlanBuildRequest(BaseModel):
    policy_pack: PolicyPackName = "default"


class ExecutionPlanPolicyRead(BaseModel):
    plan_id: str
    risk_decision: RiskDecisionV1
    policy_decision: PolicyDecisionV1
    approval_gates: list[ApprovalGateV1] = Field(default_factory=list)


class ExecutionPlanSecretsRead(BaseModel):
    plan_id: str
    requirements: list[SecretRequestV1] = Field(default_factory=list)
    references: list[SecretReferenceV1] = Field(default_factory=list)
