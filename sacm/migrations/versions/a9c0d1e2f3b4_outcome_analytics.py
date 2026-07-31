"""Add durable outcome analytics.

Revision ID: a9c0d1e2f3b4
Revises: f8b2c3d4e5f6
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9c0d1e2f3b4"
down_revision: Union[str, Sequence[str], None] = "f8b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if "run_outcome_analytics" not in tables:
        op.create_table(
            "run_outcome_analytics",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("schema_version", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("outcome", sa.String(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=True),
            sa.Column("output_tokens", sa.Integer(), nullable=True),
            sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
            sa.Column("cost_estimation_available", sa.Boolean(), nullable=False),
            sa.Column("evidence_pack_count", sa.Integer(), nullable=False),
            sa.Column("evidence_coverage_percent", sa.Float(), nullable=True),
            sa.Column("requirement_coverage_percent", sa.Float(), nullable=True),
            sa.Column("policy_blocked", sa.Boolean(), nullable=True),
            sa.Column("approval_count", sa.Integer(), nullable=False),
            sa.Column("pending_approval_count", sa.Integer(), nullable=False),
            sa.Column("approved_approval_count", sa.Integer(), nullable=False),
            sa.Column("rejected_approval_count", sa.Integer(), nullable=False),
            sa.Column("security_finding_count", sa.Integer(), nullable=True),
            sa.Column("open_security_finding_count", sa.Integer(), nullable=True),
            sa.Column(
                "high_critical_security_finding_count",
                sa.Integer(),
                nullable=True,
            ),
            sa.Column("source_run_id", sa.String(), nullable=True),
            sa.Column("source_snapshot_id", sa.String(), nullable=True),
            sa.Column("replay_count", sa.Integer(), nullable=False),
            sa.Column("changed_file_count", sa.Integer(), nullable=False),
            sa.Column("test_count", sa.Integer(), nullable=False),
            sa.Column("verification_count", sa.Integer(), nullable=False),
            sa.Column("step_count", sa.Integer(), nullable=False),
            sa.Column("agent_invocation_count", sa.Integer(), nullable=False),
            sa.Column("legacy_data", sa.Boolean(), nullable=False),
            sa.Column("data_completeness", sa.JSON(), nullable=False),
            sa.Column("details", sa.JSON(), nullable=False),
            sa.Column("source_fingerprint", sa.String(), nullable=False),
            sa.Column("computed_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "run_id", name="uq_run_outcome_analytics_run"
            ),
        )
        for column in ("run_id", "task_id", "project_id", "outcome"):
            op.create_index(
                op.f(f"ix_run_outcome_analytics_{column}"),
                "run_outcome_analytics",
                [column],
                unique=False,
            )

    tables = set(sa.inspect(connection).get_table_names())
    if "run_step_outcome_analytics" not in tables:
        op.create_table(
            "run_step_outcome_analytics",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("step_id", sa.String(), nullable=False),
            sa.Column("schema_version", sa.String(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("outcome", sa.String(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False),
            sa.Column("agent_name", sa.String(), nullable=True),
            sa.Column("provider", sa.String(), nullable=True),
            sa.Column("model_name", sa.String(), nullable=True),
            sa.Column("framework", sa.String(), nullable=True),
            sa.Column("input_tokens", sa.Integer(), nullable=True),
            sa.Column("output_tokens", sa.Integer(), nullable=True),
            sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
            sa.Column("evidence_count", sa.Integer(), nullable=False),
            sa.Column("requirement_count", sa.Integer(), nullable=False),
            sa.Column("changed_file_count", sa.Integer(), nullable=False),
            sa.Column("test_count", sa.Integer(), nullable=False),
            sa.Column("verification_count", sa.Integer(), nullable=False),
            sa.Column("failure", sa.JSON(), nullable=True),
            sa.Column("details", sa.JSON(), nullable=False),
            sa.Column("source_fingerprint", sa.String(), nullable=False),
            sa.Column("computed_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
            sa.ForeignKeyConstraint(["step_id"], ["run_steps.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "step_id", name="uq_run_step_outcome_analytics_step"
            ),
        )
        for column in ("run_id", "step_id", "outcome", "agent_name"):
            op.create_index(
                op.f(f"ix_run_step_outcome_analytics_{column}"),
                "run_step_outcome_analytics",
                [column],
                unique=False,
            )

    tables = set(sa.inspect(connection).get_table_names())
    if "agent_outcome_analytics" not in tables:
        op.create_table(
            "agent_outcome_analytics",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("step_id", sa.String(), nullable=True),
            sa.Column("source_event_id", sa.String(), nullable=False),
            sa.Column("schema_version", sa.String(), nullable=False),
            sa.Column("agent_name", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=True),
            sa.Column("provider", sa.String(), nullable=True),
            sa.Column("model_name", sa.String(), nullable=True),
            sa.Column("framework", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("outcome", sa.String(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=True),
            sa.Column("output_tokens", sa.Integer(), nullable=True),
            sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
            sa.Column("evidence_count", sa.Integer(), nullable=False),
            sa.Column("requirement_count", sa.Integer(), nullable=False),
            sa.Column("security_finding_count", sa.Integer(), nullable=False),
            sa.Column("changed_file_count", sa.Integer(), nullable=False),
            sa.Column("test_count", sa.Integer(), nullable=False),
            sa.Column("verification_count", sa.Integer(), nullable=False),
            sa.Column("failure", sa.JSON(), nullable=True),
            sa.Column("details", sa.JSON(), nullable=False),
            sa.Column("legacy_attribution", sa.Boolean(), nullable=False),
            sa.Column("source_fingerprint", sa.String(), nullable=False),
            sa.Column("computed_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
            sa.ForeignKeyConstraint(["step_id"], ["run_steps.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "source_event_id",
                name="uq_agent_outcome_analytics_source_event",
            ),
        )
        for column in (
            "run_id",
            "step_id",
            "source_event_id",
            "agent_name",
            "outcome",
        ):
            op.create_index(
                op.f(f"ix_agent_outcome_analytics_{column}"),
                "agent_outcome_analytics",
                [column],
                unique=False,
            )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table_name in (
        "agent_outcome_analytics",
        "run_step_outcome_analytics",
        "run_outcome_analytics",
    ):
        if table_name in tables:
            op.drop_table(table_name)
