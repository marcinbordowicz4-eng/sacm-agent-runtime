from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

ContextSignal = Annotated[str, Field(max_length=500)]


class CodeIntelligenceSnapshotV1(BaseModel):
    schema_version: Literal["code-intelligence-snapshot/v1"] = (
        "code-intelligence-snapshot/v1"
    )
    status: Literal[
        "COMPLETE",
        "PARTIAL",
        "TRUNCATED",
        "STALE",
        "INVALID",
        "UNAVAILABLE",
    ]
    source: str
    fingerprint: str | None = None
    expected_fingerprint: str | None = None
    tool_name: str | None = None
    tool_version: str | None = None
    indexers: list[dict[str, str]] = Field(default_factory=list)
    document_count: int = Field(ge=0)
    symbol_count: int = Field(ge=0)
    occurrence_count: int = Field(ge=0)
    repository_revision: str | None = None
    index_revision: str | None = None
    workspace_hash: str | None = None
    index_workspace_hash: str | None = None
    workspace_complete: bool
    index_workspace_complete: bool | None = None
    generated_at: str | None = None
    dirty: bool
    errors: list[str] = Field(default_factory=list)


class GraphNode(BaseModel):
    id: str
    type: str
    repository: str
    label: str
    path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str


class ApplicationGraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    truncated: bool = False
    limits: dict[str, int] = Field(default_factory=dict)


class RepositoryContextRead(BaseModel):
    position: int
    full_name: str | None
    requested_path: str | None
    resolved_path: str | None
    base_revision: str | None
    status: Literal["available", "unavailable"]
    error_code: str | None
    error_message: str | None
    file_count: int
    skipped_file_count: int
    scan_metadata: dict[str, Any]

    model_config = {"from_attributes": True}


class ImpactNode(BaseModel):
    node_id: str
    score: int = Field(ge=0)
    matched_terms: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class ImpactAnalysis(BaseModel):
    query_terms: list[str] = Field(default_factory=list)
    impacted_nodes: list[ImpactNode] = Field(default_factory=list)
    impacted_repository_count: int = Field(ge=0)
    truncated: bool = False


class RiskFactor(BaseModel):
    code: str
    contribution: int = Field(ge=0)
    explanation: str


class RiskAnalysis(BaseModel):
    score: int = Field(ge=0, le=100)
    level: Literal["low", "medium", "high", "critical"]
    factors: list[RiskFactor] = Field(default_factory=list)


class ApplicationContextRead(BaseModel):
    id: str
    task_id: str
    schema_version: Literal["application-context/v1"]
    status: Literal["complete", "partial", "unavailable"]
    scanner_version: Literal[
        "deterministic-scanner/v1",
        "deterministic-scanner/v2",
        "deterministic-scanner/v2.1",
        "deterministic-scanner/v2.2",
    ]
    graph: ApplicationGraph
    graph_hash: str
    impact_analysis: ImpactAnalysis
    risk_analysis: RiskAnalysis
    repositories: list[RepositoryContextRead]
    created_at: datetime
    updated_at: datetime


class ImpactRiskRead(BaseModel):
    task_id: str
    application_context_id: str
    graph_hash: str
    impact_analysis: ImpactAnalysis
    risk_analysis: RiskAnalysis


class ContextExpansionRequest(BaseModel):
    run_id: str | None = Field(default=None, max_length=100)
    step_id: str | None = Field(default=None, max_length=100)
    role: Literal["reasoner", "coder", "reviewer", "tester", "security"] = "coder"
    reason: str = Field(default="task_execution", max_length=100)
    refresh_graph: bool = True
    changed_symbols: list[ContextSignal] = Field(
        default_factory=list, max_length=50
    )
    failing_symbols: list[ContextSignal] = Field(
        default_factory=list, max_length=50
    )
    changed_files: list[ContextSignal] = Field(default_factory=list, max_length=50)
    failed_tests: list[ContextSignal] = Field(default_factory=list, max_length=50)
    affected_requirements: list[ContextSignal] = Field(
        default_factory=list, max_length=50
    )
    max_depth: int = Field(default=2, ge=1, le=4)
    max_nodes: int = Field(default=48, ge=1, le=200)


class ContextNodeReference(BaseModel):
    node_id: str
    type: str
    label: str
    repository: str
    path: str | None = None
    distance: int = Field(ge=0)
    reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextFileExcerpt(BaseModel):
    repository: str
    path: str
    content_hash: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    content: str = ""
    content_included: bool = True
    node_ids: list[str] = Field(default_factory=list)


class ContextPackageV2(BaseModel):
    schema_version: Literal["context-package/v2"] = "context-package/v2"
    task_id: str
    run_id: str | None = None
    step_id: str | None = None
    role: str
    reason: str
    graph_hash: str
    seed_node_ids: list[str] = Field(default_factory=list)
    nodes: list[ContextNodeReference] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    files: list[ContextFileExcerpt] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    max_depth: int = Field(ge=1)
    max_nodes: int = Field(ge=1)
    truncated: bool = False
    package_hash: str
