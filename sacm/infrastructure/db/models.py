import os
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

try:
    from pgvector.sqlalchemy import Vector
except Exception:  # pragma: no cover - dependency fallback
    Vector = None


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


def embedding_column() -> Any:
    database_url = os.getenv("DATABASE_URL", "")
    if Vector is not None and database_url.startswith("postgresql"):
        return Vector(1536)
    return JSON


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint(
            "connector_type",
            "external_id",
            name="uq_tasks_connector_external_id",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")
    target_repo_path: Mapped[str | None] = mapped_column(String, nullable=True)
    contract_version: Mapped[str | None] = mapped_column(String, nullable=True)
    connector_type: Mapped[str | None] = mapped_column(String, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)
    external_url: Mapped[str | None] = mapped_column(String, nullable=True)
    task_contract: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    readiness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    readiness_details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    events: Mapped[list["ContextEvent"]] = relationship("ContextEvent", back_populates="task")
    memory_chunks: Mapped[list["MemoryChunk"]] = relationship("MemoryChunk", back_populates="task")
    agent_states: Mapped[list["AgentState"]] = relationship("AgentState", back_populates="task")
    artifacts: Mapped[list["Artifact"]] = relationship("Artifact", back_populates="task")
    runs: Mapped[list["Run"]] = relationship("Run", back_populates="task")
    clarifications: Mapped[list["TaskClarification"]] = relationship(
        "TaskClarification",
        back_populates="task",
        cascade="all, delete-orphan",
    )
    application_context: Mapped["ApplicationContext | None"] = relationship(
        "ApplicationContext",
        back_populates="task",
        cascade="all, delete-orphan",
        uselist=False,
    )
    execution_plans: Mapped[list["ExecutionPlan"]] = relationship(
        "ExecutionPlan",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="ExecutionPlan.revision",
    )
    requirements: Mapped[list["Requirement"]] = relationship(
        "Requirement",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="Requirement.position",
    )


class TaskClarification(Base):
    __tablename__ = "task_clarifications"
    __table_args__ = (
        UniqueConstraint("task_id", "field_name", name="uq_task_clarification_field"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    answer: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    task: Mapped["Task"] = relationship("Task", back_populates="clarifications")


class Requirement(Base):
    __tablename__ = "requirements"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "stable_hash",
            name="uq_requirements_task_stable_hash",
        ),
        UniqueConstraint(
            "task_id",
            "position",
            name="uq_requirements_task_position",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id"), nullable=False, index=True
    )
    schema_version: Mapped[str] = mapped_column(
        String, nullable=False, default="requirement/v1"
    )
    stable_hash: Mapped[str] = mapped_column(String, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    task: Mapped["Task"] = relationship("Task", back_populates="requirements")
    links: Mapped[list["RequirementLink"]] = relationship(
        "RequirementLink",
        back_populates="requirement",
        cascade="all, delete-orphan",
        order_by="RequirementLink.target_type, RequirementLink.target_id",
    )


class RequirementLink(Base):
    __tablename__ = "requirement_links"
    __table_args__ = (
        UniqueConstraint(
            "requirement_id",
            "target_type",
            "target_id",
            "relation",
            name="uq_requirement_links_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id"), nullable=False, index=True
    )
    requirement_id: Mapped[str] = mapped_column(
        ForeignKey("requirements.id"), nullable=False, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id"), nullable=True, index=True
    )
    schema_version: Mapped[str] = mapped_column(
        String, nullable=False, default="requirement-link/v1"
    )
    target_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String, nullable=False)
    relation: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False, default="derived")
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    requirement: Mapped["Requirement"] = relationship(
        "Requirement", back_populates="links"
    )
    run: Mapped["Run | None"] = relationship("Run")


class ApplicationContext(Base):
    __tablename__ = "application_contexts"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_application_contexts_task_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id"), nullable=False, index=True
    )
    schema_version: Mapped[str] = mapped_column(
        String, nullable=False, default="application-context/v1"
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    scanner_version: Mapped[str] = mapped_column(
        String, nullable=False, default="deterministic-scanner/v1"
    )
    graph: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    graph_hash: Mapped[str] = mapped_column(String, nullable=False)
    impact_analysis: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    risk_analysis: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    task: Mapped["Task"] = relationship("Task", back_populates="application_context")
    repositories: Mapped[list["ApplicationContextRepository"]] = relationship(
        "ApplicationContextRepository",
        back_populates="application_context",
        cascade="all, delete-orphan",
        order_by="ApplicationContextRepository.position",
    )


class ApplicationContextRepository(Base):
    __tablename__ = "application_context_repositories"
    __table_args__ = (
        UniqueConstraint(
            "application_context_id",
            "position",
            name="uq_application_context_repositories_position",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    application_context_id: Mapped[str] = mapped_column(
        ForeignKey("application_contexts.id"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    requested_path: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_path: Mapped[str | None] = mapped_column(String, nullable=True)
    base_revision: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_file_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    scan_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    application_context: Mapped["ApplicationContext"] = relationship(
        "ApplicationContext", back_populates="repositories"
    )


class ExecutionPlan(Base):
    __tablename__ = "execution_plans"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "revision", name="uq_execution_plans_task_revision"
        ),
        UniqueConstraint(
            "task_id", "source_hash", name="uq_execution_plans_task_source"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id"), nullable=False, index=True
    )
    application_context_id: Mapped[str] = mapped_column(
        ForeignKey("application_contexts.id"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(
        String, nullable=False, default="execution-plan/v1"
    )
    planner_version: Mapped[str] = mapped_column(
        String, nullable=False, default="deterministic-planner/v1"
    )
    source_hash: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    policy_pack: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    task: Mapped["Task"] = relationship("Task", back_populates="execution_plans")
    application_context: Mapped["ApplicationContext"] = relationship(
        "ApplicationContext"
    )
    steps: Mapped[list["ExecutionPlanStep"]] = relationship(
        "ExecutionPlanStep",
        back_populates="execution_plan",
        cascade="all, delete-orphan",
        order_by="ExecutionPlanStep.sequence",
    )
    risk_decision: Mapped["ExecutionPlanRiskDecision"] = relationship(
        "ExecutionPlanRiskDecision",
        back_populates="execution_plan",
        cascade="all, delete-orphan",
        uselist=False,
    )
    policy_decision: Mapped["ExecutionPlanPolicyDecision"] = relationship(
        "ExecutionPlanPolicyDecision",
        back_populates="execution_plan",
        cascade="all, delete-orphan",
        uselist=False,
    )
    security_review: Mapped["ExecutionPlanSecurityReview"] = relationship(
        "ExecutionPlanSecurityReview",
        back_populates="execution_plan",
        cascade="all, delete-orphan",
        uselist=False,
    )
    secret_requirements: Mapped[list["ExecutionPlanSecretRequirement"]] = relationship(
        "ExecutionPlanSecretRequirement",
        back_populates="execution_plan",
        cascade="all, delete-orphan",
        order_by="ExecutionPlanSecretRequirement.position",
    )
    approval_gates: Mapped[list["ExecutionPlanApprovalGate"]] = relationship(
        "ExecutionPlanApprovalGate",
        back_populates="execution_plan",
        cascade="all, delete-orphan",
        order_by="ExecutionPlanApprovalGate.position",
    )


class ExecutionPlanStep(Base):
    __tablename__ = "execution_plan_steps"
    __table_args__ = (
        UniqueConstraint(
            "execution_plan_id",
            "sequence",
            name="uq_execution_plan_steps_sequence",
        ),
        UniqueConstraint(
            "execution_plan_id",
            "stable_key",
            name="uq_execution_plan_steps_stable_key",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    execution_plan_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plans.id"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stable_key: Mapped[str] = mapped_column(String, nullable=False)
    schema_version: Mapped[str] = mapped_column(
        String, nullable=False, default="execution-plan-step/v1"
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance_criteria: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    context_references: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    impacted_node_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    required_tools: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    risk_tags: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    depends_on: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    assigned_agent_name: Mapped[str] = mapped_column(String, nullable=False)
    assigned_agent_role: Mapped[str] = mapped_column(String, nullable=False)
    agent_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    execution_plan: Mapped["ExecutionPlan"] = relationship(
        "ExecutionPlan", back_populates="steps"
    )


class ExecutionPlanRiskDecision(Base):
    __tablename__ = "execution_plan_risk_decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    execution_plan_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plans.id"), nullable=False, unique=True, index=True
    )
    schema_version: Mapped[str] = mapped_column(
        String, nullable=False, default="risk-decision/v1"
    )
    decision: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    execution_plan: Mapped["ExecutionPlan"] = relationship(
        "ExecutionPlan", back_populates="risk_decision"
    )


class ExecutionPlanPolicyDecision(Base):
    __tablename__ = "execution_plan_policy_decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    execution_plan_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plans.id"), nullable=False, unique=True, index=True
    )
    schema_version: Mapped[str] = mapped_column(
        String, nullable=False, default="policy-decision/v1"
    )
    policy_pack: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    execution_plan: Mapped["ExecutionPlan"] = relationship(
        "ExecutionPlan", back_populates="policy_decision"
    )


class ExecutionPlanSecurityReview(Base):
    __tablename__ = "execution_plan_security_reviews"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    execution_plan_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plans.id"), nullable=False, unique=True, index=True
    )
    schema_version: Mapped[str] = mapped_column(
        String, nullable=False, default="security-review/v1"
    )
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    reviewer_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )
    findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    execution_plan: Mapped["ExecutionPlan"] = relationship(
        "ExecutionPlan", back_populates="security_review"
    )


class ExecutionPlanSecretRequirement(Base):
    __tablename__ = "execution_plan_secret_requirements"
    __table_args__ = (
        UniqueConstraint(
            "execution_plan_id",
            "position",
            name="uq_execution_plan_secret_requirements_position",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    execution_plan_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plans.id"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(
        String, nullable=False, default="secret-request/v1"
    )
    request: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reference: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    execution_plan: Mapped["ExecutionPlan"] = relationship(
        "ExecutionPlan", back_populates="secret_requirements"
    )


class ExecutionPlanApprovalGate(Base):
    __tablename__ = "execution_plan_approval_gates"
    __table_args__ = (
        UniqueConstraint(
            "execution_plan_id",
            "position",
            name="uq_execution_plan_approval_gates_position",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    execution_plan_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plans.id"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(
        String, nullable=False, default="approval-gate/v1"
    )
    gate_type: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    step_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    approval_id: Mapped[str | None] = mapped_column(
        ForeignKey("approvals.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    execution_plan: Mapped["ExecutionPlan"] = relationship(
        "ExecutionPlan", back_populates="approval_gates"
    )
    approval: Mapped["Approval | None"] = relationship("Approval")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    projects: Mapped[list["Project"]] = relationship(
        "Project", back_populates="organization", cascade="all, delete-orphan"
    )
    memberships: Mapped[list["Membership"]] = relationship(
        "Membership", back_populates="organization", cascade="all, delete-orphan"
    )


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_projects_organization_slug"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    repository_full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    repository_path: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="projects"
    )
    runs: Mapped[list["Run"]] = relationship("Run", back_populates="project")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "actor_id", name="uq_memberships_organization_actor"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    actor_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="memberships"
    )


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    cost_weight: Mapped[float] = mapped_column(Float, default=1.0)
    quality_score: Mapped[float] = mapped_column(Float, default=0.5)
    latency_score: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ContextEvent(Base):
    __tablename__ = "context_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped["Task"] = relationship("Task", back_populates="events")


class MemoryChunk(Base):
    __tablename__ = "memory_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str | None] = mapped_column(String, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any | None] = mapped_column(embedding_column(), nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped["Task"] = relationship("Task", back_populates="memory_chunks")


class AgentState(Base):
    __tablename__ = "agent_states"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    belief_state: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    context_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped["Task"] = relationship("Task", back_populates="agent_states")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str | None] = mapped_column(String, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_: Mapped[Any | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped["Task"] = relationship("Task", back_populates="artifacts")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="CREATED", index=True)
    workflow_version: Mapped[str] = mapped_column(String, nullable=False, default="run/v1")
    source_revision: Mapped[str | None] = mapped_column(String, nullable=True)
    target_repo_path: Mapped[str | None] = mapped_column(String, nullable=True)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    task: Mapped["Task"] = relationship("Task", back_populates="runs")
    project: Mapped["Project | None"] = relationship("Project", back_populates="runs")
    steps: Mapped[list["RunStep"]] = relationship(
        "RunStep", back_populates="run", cascade="all, delete-orphan"
    )
    events: Mapped[list["RuntimeEvent"]] = relationship(
        "RuntimeEvent", back_populates="run", cascade="all, delete-orphan"
    )
    evidence_packs: Mapped[list["EvidencePack"]] = relationship(
        "EvidencePack", back_populates="run", cascade="all, delete-orphan"
    )
    approvals: Mapped[list["Approval"]] = relationship(
        "Approval", back_populates="run", cascade="all, delete-orphan"
    )
    webhook_deliveries: Mapped[list["GitHubWebhookDelivery"]] = relationship(
        "GitHubWebhookDelivery", back_populates="run", cascade="all, delete-orphan"
    )
    snapshots: Mapped[list["RunSnapshot"]] = relationship(
        "RunSnapshot",
        back_populates="run",
        cascade="all, delete-orphan",
        foreign_keys="RunSnapshot.run_id",
        order_by="RunSnapshot.created_at",
    )
    replay_source_links: Mapped[list["RunReplay"]] = relationship(
        "RunReplay",
        back_populates="source_run",
        foreign_keys="RunReplay.source_run_id",
    )
    replay_link: Mapped["RunReplay | None"] = relationship(
        "RunReplay",
        back_populates="replay_run",
        foreign_keys="RunReplay.replay_run_id",
        uselist=False,
    )
    analytics: Mapped["RunOutcomeAnalytics | None"] = relationship(
        "RunOutcomeAnalytics",
        back_populates="run",
        cascade="all, delete-orphan",
        uselist=False,
    )
    step_analytics: Mapped[list["RunStepOutcomeAnalytics"]] = relationship(
        "RunStepOutcomeAnalytics",
        back_populates="run",
        cascade="all, delete-orphan",
    )
    agent_analytics: Mapped[list["AgentOutcomeAnalytics"]] = relationship(
        "AgentOutcomeAnalytics",
        back_populates="run",
        cascade="all, delete-orphan",
    )


class RunStep(Base):
    __tablename__ = "run_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_run_steps_sequence"),
        UniqueConstraint("run_id", "idempotency_key", name="uq_run_steps_idempotency"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    input_: Mapped[dict[str, Any]] = mapped_column("input", JSON, default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    run: Mapped["Run"] = relationship("Run", back_populates="steps")


class RuntimeEvent(Base):
    __tablename__ = "runtime_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_runtime_events_sequence"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    step_id: Mapped[str | None] = mapped_column(ForeignKey("run_steps.id"), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String, nullable=False)
    causation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    previous_event_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    event_hash: Mapped[str] = mapped_column(String, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    run: Mapped["Run"] = relationship("Run", back_populates="events")


class RunSnapshot(Base):
    __tablename__ = "run_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "checksum",
            name="uq_run_snapshots_run_checksum",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    schema_version: Mapped[str] = mapped_column(
        String, nullable=False, default="run-snapshot/v1"
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id"), nullable=False, index=True
    )
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_hash: Mapped[str] = mapped_column(String, nullable=False)
    workflow_version: Mapped[str] = mapped_column(String, nullable=False)
    run_state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    step_state: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    execution_plan_summary: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    context_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String, nullable=False)
    parent_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("run_snapshots.id"), nullable=True, index=True
    )
    creation_reason: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    run: Mapped["Run"] = relationship(
        "Run", back_populates="snapshots", foreign_keys=[run_id]
    )
    task: Mapped["Task"] = relationship("Task")
    parent: Mapped["RunSnapshot | None"] = relationship(
        "RunSnapshot", remote_side=[id], foreign_keys=[parent_snapshot_id]
    )


class RunReplay(Base):
    __tablename__ = "run_replays"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    schema_version: Mapped[str] = mapped_column(
        String, nullable=False, default="run-replay/v1"
    )
    source_run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id"), nullable=False, index=True
    )
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("run_snapshots.id"), nullable=False, index=True
    )
    replay_run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id"), nullable=False, unique=True, index=True
    )
    overrides: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    replay_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    source_run: Mapped["Run"] = relationship(
        "Run",
        back_populates="replay_source_links",
        foreign_keys=[source_run_id],
    )
    replay_run: Mapped["Run"] = relationship(
        "Run", back_populates="replay_link", foreign_keys=[replay_run_id]
    )
    source_snapshot: Mapped["RunSnapshot"] = relationship("RunSnapshot")


class EvidencePack(Base):
    __tablename__ = "evidence_packs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    path: Mapped[str] = mapped_column(String, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    run: Mapped["Run"] = relationship("Run", back_populates="evidence_packs")


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    resource: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped["Run"] = relationship("Run", back_populates="approvals")


class RunOutcomeAnalytics(Base):
    __tablename__ = "run_outcome_analytics"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_run_outcome_analytics_run"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id"), nullable=False, index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    schema_version: Mapped[str] = mapped_column(
        String, nullable=False, default="outcome-analytics/v1"
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_estimation_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    evidence_pack_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    evidence_coverage_percent: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    requirement_coverage_percent: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    policy_blocked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    approval_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_approval_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    approved_approval_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    rejected_approval_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    security_finding_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    open_security_finding_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    high_critical_security_finding_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    source_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_snapshot_id: Mapped[str | None] = mapped_column(String, nullable=True)
    replay_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    changed_file_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    test_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verification_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_invocation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    legacy_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data_completeness: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    source_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    run: Mapped["Run"] = relationship("Run", back_populates="analytics")


class RunStepOutcomeAnalytics(Base):
    __tablename__ = "run_step_outcome_analytics"
    __table_args__ = (
        UniqueConstraint("step_id", name="uq_run_step_outcome_analytics_step"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id"), nullable=False, index=True
    )
    step_id: Mapped[str] = mapped_column(
        ForeignKey("run_steps.id"), nullable=False, index=True
    )
    schema_version: Mapped[str] = mapped_column(
        String, nullable=False, default="step-outcome-analytics/v1"
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String, nullable=True)
    framework: Mapped[str | None] = mapped_column(String, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requirement_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    changed_file_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    test_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verification_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    failure: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    source_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    run: Mapped["Run"] = relationship("Run", back_populates="step_analytics")
    step: Mapped["RunStep"] = relationship("RunStep")


class AgentOutcomeAnalytics(Base):
    __tablename__ = "agent_outcome_analytics"
    __table_args__ = (
        UniqueConstraint(
            "source_event_id",
            name="uq_agent_outcome_analytics_source_event",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id"), nullable=False, index=True
    )
    step_id: Mapped[str | None] = mapped_column(
        ForeignKey("run_steps.id"), nullable=True, index=True
    )
    source_event_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(
        String, nullable=False, default="agent-outcome-analytics/v1"
    )
    agent_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String, nullable=True)
    framework: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requirement_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    security_finding_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    changed_file_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    test_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verification_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    failure: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    legacy_attribution: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    source_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    run: Mapped["Run"] = relationship("Run", back_populates="agent_analytics")
    step: Mapped["RunStep | None"] = relationship("RunStep")


class GitHubWebhookDelivery(Base):
    __tablename__ = "github_webhook_deliveries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    delivery_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    run: Mapped["Run"] = relationship("Run", back_populates="webhook_deliveries")


class SkillRecord(Base):
    """Persisted proof-of-state: a skill that an agent has demonstrated.

    Accuracy improves via EMA with each task cycle.  Only skills whose
    accuracy exceeds a threshold are injected into future agent contexts,
    so the system gradually concentrates on what actually works.
    """

    __tablename__ = "skill_records"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    skill_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    accuracy: Mapped[float] = mapped_column(Float, default=0.5)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reward: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
