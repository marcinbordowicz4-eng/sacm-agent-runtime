from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator


class JiraConnectorCreate(BaseModel):
    organization_id: str
    project_id: str
    base_url: AnyHttpUrl
    jira_project_key: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,49}$")
    username: str = Field(min_length=1)
    api_token_ref: str = Field(min_length=1)
    webhook_secret_ref: str | None = None
    field_mapping: dict[str, str] = Field(default_factory=dict)
    status_mapping: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=10.0, ge=0.1, le=120)
    max_attempts: int = Field(default=3, ge=1, le=10)

    @field_validator("base_url")
    @classmethod
    def jira_cloud_https(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("Jira Cloud base_url must use HTTPS.")
        return value


class JiraConnectorRead(BaseModel):
    id: str
    organization_id: str
    project_id: str
    base_url: str
    jira_project_key: str
    username: str
    api_token_ref: str
    webhook_secret_ref: str | None
    field_mapping: dict[str, Any]
    status_mapping: dict[str, str]
    timeout_seconds: float
    max_attempts: int
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JiraDeliveryRead(BaseModel):
    task_id: str
    run_id: str | None
    status: str
    jira_status: str | None
    status_comment_id: str | None
    pr_status: str
    pr_url: str | None
    context: dict[str, Any]
    last_error: str | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class JiraWebhookResult(BaseModel):
    duplicate: bool = False
    event_type: str
    task_id: str | None = None
    readiness_score: float | None = None
    readiness_ready: bool | None = None
    clarification_id: str | None = None


class JiraOrchestrationRequest(BaseModel):
    policy_pack: Literal["default", "strict"] = "default"
    create_pull_request: bool = True


class JiraOrchestrationRead(BaseModel):
    task_id: str
    run_id: str | None
    status: str
    pr_status: str
    execution_job_ids: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
