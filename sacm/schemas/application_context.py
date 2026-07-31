from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


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
    scanner_version: Literal["deterministic-scanner/v1"]
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
