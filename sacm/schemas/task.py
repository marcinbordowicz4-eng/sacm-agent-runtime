from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskStatus:
    PENDING = "pending"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    PLANNING = "planning"
    CODING = "coding"
    TESTING = "testing"
    DEBUGGING = "debugging"
    REVIEWING = "reviewing"
    BLOCKED = "blocked"
    DONE = "done"


class TaskCreate(BaseModel):
    title: str
    description: str
    target_repo_path: str | None = None


class TaskRead(BaseModel):
    id: str
    title: str
    description: str
    status: str
    target_repo_path: str | None
    contract_version: str | None = None
    connector_type: str | None = None
    external_id: str | None = None
    external_url: str | None = None
    task_contract: dict[str, Any] | None = None
    readiness_score: float | None = None
    readiness_details: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


ConnectorType = Literal["jira", "linear", "github", "azure_devops", "generic"]


class RepositoryReference(BaseModel):
    full_name: str | None = None
    path: str | None = None
    base_revision: str | None = None


class TaskContractV1(BaseModel):
    schema_version: Literal["task-contract/v1"] = "task-contract/v1"
    connector_type: ConnectorType
    external_id: str = Field(min_length=1, max_length=255)
    external_url: str | None = None
    title: str = Field(min_length=1, max_length=500)
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    repositories: list[RepositoryReference] = Field(default_factory=list)
    priority: str | None = None
    labels: list[str] = Field(default_factory=list)
    requested_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReadinessAssessment(BaseModel):
    score: float = Field(ge=0, le=1)
    ready: bool
    missing_fields: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)


class TaskClarificationRead(BaseModel):
    id: str
    task_id: str
    field_name: str
    question: str
    status: str
    answer: Any | None
    created_at: datetime
    answered_at: datetime | None

    model_config = {"from_attributes": True}


class ClarificationAnswer(BaseModel):
    answer: Any


class TaskIntakeRead(BaseModel):
    task: TaskRead
    readiness: ReadinessAssessment
    clarifications: list[TaskClarificationRead]


class JiraIssueFields(BaseModel):
    summary: str = Field(min_length=1)
    description: Any | None = None
    labels: list[str] = Field(default_factory=list)
    priority: dict[str, Any] | None = None
    reporter: dict[str, Any] | None = None


class JiraIssue(BaseModel):
    key: str = Field(min_length=1)
    self: str | None = None
    fields: JiraIssueFields


class JiraWebhook(BaseModel):
    webhookEvent: str | None = None
    issue: JiraIssue
