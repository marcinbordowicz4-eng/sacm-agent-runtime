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
