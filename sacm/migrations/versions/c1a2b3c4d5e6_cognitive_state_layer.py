"""Add the project-level Cognitive State Layer.

Revision ID: c1a2b3c4d5e6
Revises: c9d1e2f3a4b5
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "c9d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "cognitive_events" not in tables:
        op.create_table(
            "cognitive_events",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("agent_id", sa.String(), nullable=True),
            sa.Column("commit_hash", sa.String(), nullable=True),
            sa.Column("snapshot_id", sa.String(), nullable=True),
            sa.Column("trace_id", sa.String(), nullable=True),
            sa.Column("parent_event_id", sa.String(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("previous_event_hash", sa.String(), nullable=True),
            sa.Column("event_hash", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in (
            "project_id", "event_type", "agent_id", "commit_hash", "snapshot_id",
            "trace_id", "created_at",
        ):
            op.create_index(
                f"ix_cognitive_events_{column}", "cognitive_events", [column]
            )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "cognitive_relations" not in tables:
        op.create_table(
            "cognitive_relations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("source_type", sa.String(), nullable=False),
            sa.Column("source_id", sa.String(), nullable=False),
            sa.Column("target_type", sa.String(), nullable=False),
            sa.Column("target_id", sa.String(), nullable=False),
            sa.Column("relation", sa.String(), nullable=False),
            sa.Column("commit_hash", sa.String(), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_event_id", sa.String(), nullable=False),
            sa.Column("valid_from", sa.DateTime(), nullable=False),
            sa.Column("valid_until", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.ForeignKeyConstraint(["created_event_id"], ["cognitive_events.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "project_id", "source_type", "source_id", "target_type",
                "target_id", "relation", "created_event_id",
                name="uq_cognitive_relation_event",
            ),
        )
        for column in (
            "project_id", "source_type", "source_id", "target_type", "target_id",
            "relation", "commit_hash", "created_event_id", "valid_from",
        ):
            op.create_index(
                f"ix_cognitive_relations_{column}", "cognitive_relations", [column]
            )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "cognitive_snapshots" not in tables:
        op.create_table(
            "cognitive_snapshots",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("commit_hash", sa.String(), nullable=True),
            sa.Column("snapshot_type", sa.String(), nullable=False),
            sa.Column("state", sa.JSON(), nullable=False),
            sa.Column("state_hash", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in ("project_id", "commit_hash", "snapshot_type", "created_at"):
            op.create_index(
                f"ix_cognitive_snapshots_{column}", "cognitive_snapshots", [column]
            )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "cognitive_memories" not in tables:
        op.create_table(
            "cognitive_memories",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("memory_type", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("embedding", sa.JSON(), nullable=True),
            sa.Column("agent_id", sa.String(), nullable=True),
            sa.Column("requirement_id", sa.String(), nullable=True),
            sa.Column("decision_id", sa.String(), nullable=True),
            sa.Column("commit_hash", sa.String(), nullable=True),
            sa.Column("snapshot_id", sa.String(), nullable=True),
            sa.Column("importance", sa.Float(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in (
            "project_id", "memory_type", "agent_id", "requirement_id", "decision_id",
            "commit_hash", "snapshot_id", "created_at",
        ):
            op.create_index(
                f"ix_cognitive_memories_{column}", "cognitive_memories", [column]
            )
        if op.get_bind().dialect.name == "postgresql":
            op.execute("CREATE EXTENSION IF NOT EXISTS vector")
            op.execute("ALTER TABLE cognitive_memories ALTER COLUMN embedding TYPE vector(1536) USING NULL")
            op.execute(
                "CREATE INDEX cognitive_memories_embedding_idx ON cognitive_memories "
                "USING hnsw (embedding vector_cosine_ops)"
            )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "cognitive_commit_jobs" not in tables:
        op.create_table(
            "cognitive_commit_jobs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("commit_hash", sa.String(), nullable=False),
            sa.Column("agent_id", sa.String(), nullable=True),
            sa.Column("trace_id", sa.String(), nullable=True),
            sa.Column("semantic_summary", sa.Text(), nullable=True),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("available_at", sa.DateTime(), nullable=False),
            sa.Column("lease_token", sa.String(), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "project_id", "commit_hash", name="uq_cognitive_commit_job_project_commit"
            ),
        )
        for column in (
            "project_id", "commit_hash", "trace_id", "state", "available_at",
            "lease_token", "lease_expires_at",
        ):
            op.create_index(
                f"ix_cognitive_commit_jobs_{column}", "cognitive_commit_jobs", [column]
            )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in (
        "cognitive_commit_jobs",
        "cognitive_memories",
        "cognitive_snapshots",
        "cognitive_relations",
        "cognitive_events",
    ):
        if table in tables:
            op.drop_table(table)
