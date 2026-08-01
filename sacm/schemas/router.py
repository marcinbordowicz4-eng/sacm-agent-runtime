from typing import Literal

from pydantic import BaseModel, Field

from sacm.schemas.contracts import AgentRole


class RouterRankRequestV1(BaseModel):
    schema_version: Literal["router-rank-request/v1"] = "router-rank-request/v1"
    task_id: str
    role: AgentRole | None = None
    risk_level: str | None = None
    cost_budget_usd: float | None = Field(default=None, gt=0)
    latency_budget_ms: int | None = Field(default=None, gt=0)
    previous_failure_classification: str | None = None


class RouterCandidateV1(BaseModel):
    schema_version: Literal["router-candidate/v1"] = "router-candidate/v1"
    agent_name: str
    role: AgentRole
    provider: str | None = None
    model_name: str | None = None
    framework: str | None = None
    samples: int = Field(ge=0)
    successes: int = Field(ge=0)
    failures: int = Field(ge=0)
    neural_prior: float = Field(ge=0, le=1)
    posterior_success_probability: float = Field(ge=0, le=1)
    confidence_weight: float = Field(ge=0, le=1)
    average_cost_usd: float | None = Field(default=None, ge=0)
    average_latency_ms: float | None = Field(default=None, ge=0)
    average_retries: float = Field(ge=0)
    score: float
    trusted_outcomes: bool
    data_scope: Literal["project", "task_tags", "global", "none"]
    reasons: list[str] = Field(default_factory=list)


class RouterDecisionV1(BaseModel):
    schema_version: Literal["router-decision/v1"] = "router-decision/v1"
    task_id: str
    selected_agent_name: str
    selected_agent_index: int = Field(ge=0)
    strategy: Literal["OUTCOME_ADAPTIVE", "NEURAL_FALLBACK"]
    minimum_samples: int = Field(ge=1)
    role: AgentRole | None = None
    risk_level: str | None = None
    task_tags: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
    candidates: list[RouterCandidateV1]
    outcome_semantics: str = (
        "success is an execution outcome proxy, not human acceptance"
    )
