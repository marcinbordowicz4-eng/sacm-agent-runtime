from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

CognitiveSnapshotType = Literal["MICRO", "FEATURE", "RELEASE", "PRODUCTION", "MANUAL"]


class CognitiveEventCreateV1(BaseModel):
    """An immutable observation in the project cognitive history."""

    schema_version: Literal["cognitive-event-create/v1"] = "cognitive-event-create/v1"
    event_type: str = Field(min_length=1, max_length=120)
    agent_id: str | None = Field(default=None, max_length=200)
    commit_hash: str | None = Field(default=None, max_length=128)
    snapshot_id: str | None = Field(default=None, max_length=100)
    trace_id: str | None = Field(default=None, max_length=200)
    parent_event_id: str | None = Field(default=None, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class CognitiveEventV1(CognitiveEventCreateV1):
    schema_version: Literal["cognitive-event/v1"] = "cognitive-event/v1"
    id: str
    project_id: str
    event_hash: str
    previous_event_hash: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CognitiveRelationCreateV1(BaseModel):
    schema_version: Literal["cognitive-relation-create/v1"] = (
        "cognitive-relation-create/v1"
    )
    source_type: str = Field(min_length=1, max_length=80)
    source_id: str = Field(min_length=1, max_length=500)
    target_type: str = Field(min_length=1, max_length=80)
    target_id: str = Field(min_length=1, max_length=500)
    relation: str = Field(min_length=1, max_length=100)
    commit_hash: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CognitiveRelationV1(CognitiveRelationCreateV1):
    schema_version: Literal["cognitive-relation/v1"] = "cognitive-relation/v1"
    id: str
    project_id: str
    created_event_id: str
    valid_from: datetime
    valid_until: datetime | None = None

    model_config = {"from_attributes": True}


class CommitIngestV1(BaseModel):
    schema_version: Literal["cognitive-commit-ingest/v1"] = "cognitive-commit-ingest/v1"
    commit_hash: str = Field(min_length=7, max_length=128)
    parent_hash: str | None = Field(default=None, max_length=128)
    branch: str | None = Field(default=None, max_length=300)
    message: str = Field(default="", max_length=20_000)
    agent_id: str | None = Field(default=None, max_length=200)
    trace_id: str | None = Field(default=None, max_length=200)
    requirements: list[str] = Field(default_factory=list, max_length=100)
    decisions: list[str] = Field(default_factory=list, max_length=100)
    skills: list[str] = Field(default_factory=list, max_length=100)
    files: list[str] = Field(default_factory=list, max_length=10_000)
    tests: list[str] = Field(default_factory=list, max_length=10_000)
    dependencies: list[tuple[str, str]] = Field(default_factory=list, max_length=10_000)
    semantic_summary: str | None = Field(default=None, max_length=50_000)
    risk: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"


class GitCommitAnalyzeV1(BaseModel):
    """Request Git-native collection before cognitive commit ingestion."""

    schema_version: Literal["cognitive-git-commit-analyze/v1"] = (
        "cognitive-git-commit-analyze/v1"
    )
    commit_hash: str = Field(pattern=r"^[0-9a-fA-F]{7,128}$")
    agent_id: str | None = Field(default=None, max_length=200)
    trace_id: str | None = Field(default=None, max_length=200)
    semantic_summary: str | None = Field(default=None, max_length=50_000)


class GitCommitJobV1(GitCommitAnalyzeV1):
    schema_version: Literal["cognitive-git-commit-job/v1"] = "cognitive-git-commit-job/v1"
    id: str
    project_id: str
    state: Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED"]
    attempt: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    last_error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class CognitiveSnapshotCreateV1(BaseModel):
    schema_version: Literal["cognitive-snapshot-create/v1"] = (
        "cognitive-snapshot-create/v1"
    )
    commit_hash: str | None = Field(default=None, max_length=128)
    snapshot_type: CognitiveSnapshotType = "MANUAL"
    reason: str | None = Field(default=None, max_length=2_000)


class CognitiveSnapshotV1(BaseModel):
    schema_version: Literal["cognitive-snapshot/v1"] = "cognitive-snapshot/v1"
    id: str
    project_id: str
    commit_hash: str | None = None
    snapshot_type: CognitiveSnapshotType
    state: dict[str, Any]
    state_hash: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ContextReconstructV1(BaseModel):
    schema_version: Literal["cognitive-context-reconstruct/v1"] = (
        "cognitive-context-reconstruct/v1"
    )
    agent_id: str = Field(min_length=1, max_length=200)
    task: str = Field(min_length=1, max_length=10_000)
    commit_hash: str | None = Field(default=None, max_length=128)
    snapshot_id: str | None = Field(default=None, max_length=100)
    max_items: int = Field(default=20, ge=1, le=100)


class AgentHandoffCreateV1(BaseModel):
    schema_version: Literal["cognitive-agent-handoff-create/v1"] = (
        "cognitive-agent-handoff-create/v1"
    )
    from_agent: str = Field(min_length=1, max_length=200)
    to_agent: str = Field(min_length=1, max_length=200)
    snapshot_id: str | None = Field(default=None, max_length=100)
    requirement_ids: list[str] = Field(default_factory=list, max_length=100)
    decision_ids: list[str] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    context_hash: str = Field(min_length=32, max_length=128)


class AgentHandoffV1(AgentHandoffCreateV1):
    schema_version: Literal["cognitive-agent-handoff/v1"] = "cognitive-agent-handoff/v1"
    id: str
    project_id: str
    created_at: datetime


class CognitiveContextV1(BaseModel):
    schema_version: Literal["cognitive-context/v1"] = "cognitive-context/v1"
    project_id: str
    snapshot: CognitiveSnapshotV1 | None = None
    task: str
    requirements: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    code: list[str] = Field(default_factory=list)
    dependencies: list[dict[str, str]] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    memories: list[dict[str, Any]] = Field(default_factory=list)
    handoffs: list[dict[str, Any]] = Field(default_factory=list)
    provenance: list[dict[str, Any]] = Field(default_factory=list)


class CognitiveDeliveryPassportV1(BaseModel):
    """Read-only evidence view for one completed software delivery."""

    schema_version: Literal["cognitive-delivery-passport/v1"] = (
        "cognitive-delivery-passport/v1"
    )
    project_id: str
    delivery_id: str
    task_id: str
    run_id: str
    source_revision: str | None = None
    requirements: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    pull_request: dict[str, str | None] = Field(default_factory=dict)
    traceability: dict[str, Any] = Field(default_factory=dict)
    event: CognitiveEventV1
    snapshot: CognitiveSnapshotV1 | None = None
    provenance: list[dict[str, Any]] = Field(default_factory=list)


class ExplainFileV1(BaseModel):
    schema_version: Literal["cognitive-explain-file/v1"] = "cognitive-explain-file/v1"
    project_id: str
    file: str
    requirements: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    commits: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ImpactAnalysisRequestV1(BaseModel):
    schema_version: Literal["cognitive-impact-analysis/v1"] = (
        "cognitive-impact-analysis/v1"
    )
    file: str = Field(min_length=1, max_length=2_000)
    change: str = Field(min_length=1, max_length=10_000)
    commit_hash: str | None = Field(default=None, max_length=128)


class ImpactAnalysisV1(BaseModel):
    schema_version: Literal["cognitive-impact-result/v1"] = "cognitive-impact-result/v1"
    project_id: str
    file: str
    affected_files: list[str] = Field(default_factory=list)
    affected_requirements: list[str] = Field(default_factory=list)
    affected_tests: list[str] = Field(default_factory=list)
    affected_services: list[str] = Field(default_factory=list)
    architectural_decisions: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
