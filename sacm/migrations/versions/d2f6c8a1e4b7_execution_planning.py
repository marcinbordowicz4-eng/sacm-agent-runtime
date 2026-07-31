"""Add durable execution planning.

Revision ID: d2f6c8a1e4b7
Revises: b9c4e201a6d8
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2f6c8a1e4b7"
down_revision: Union[str, Sequence[str], None] = "b9c4e201a6d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())

    if "execution_plans" not in tables:
        op.create_table(
            "execution_plans",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("application_context_id", sa.String(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("schema_version", sa.String(), nullable=False),
            sa.Column("planner_version", sa.String(), nullable=False),
            sa.Column("source_hash", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("policy_pack", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["application_context_id"], ["application_contexts.id"]
            ),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "task_id", "revision", name="uq_execution_plans_task_revision"
            ),
            sa.UniqueConstraint(
                "task_id", "source_hash", name="uq_execution_plans_task_source"
            ),
        )
        op.create_index(
            op.f("ix_execution_plans_task_id"),
            "execution_plans",
            ["task_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_execution_plans_application_context_id"),
            "execution_plans",
            ["application_context_id"],
            unique=False,
        )

    tables = set(sa.inspect(connection).get_table_names())
    if "execution_plan_steps" not in tables:
        op.create_table(
            "execution_plan_steps",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("execution_plan_id", sa.String(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("stable_key", sa.String(), nullable=False),
            sa.Column("schema_version", sa.String(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("objective", sa.Text(), nullable=False),
            sa.Column("acceptance_criteria", sa.JSON(), nullable=False),
            sa.Column("context_references", sa.JSON(), nullable=False),
            sa.Column("impacted_node_ids", sa.JSON(), nullable=False),
            sa.Column("required_tools", sa.JSON(), nullable=False),
            sa.Column("risk_tags", sa.JSON(), nullable=False),
            sa.Column("depends_on", sa.JSON(), nullable=False),
            sa.Column("assigned_agent_name", sa.String(), nullable=False),
            sa.Column("assigned_agent_role", sa.String(), nullable=False),
            sa.Column("agent_configuration", sa.JSON(), nullable=False),
            sa.ForeignKeyConstraint(["execution_plan_id"], ["execution_plans.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "execution_plan_id",
                "sequence",
                name="uq_execution_plan_steps_sequence",
            ),
            sa.UniqueConstraint(
                "execution_plan_id",
                "stable_key",
                name="uq_execution_plan_steps_stable_key",
            ),
        )
        op.create_index(
            op.f("ix_execution_plan_steps_execution_plan_id"),
            "execution_plan_steps",
            ["execution_plan_id"],
            unique=False,
        )

    _create_decision_tables()
    tables = set(sa.inspect(connection).get_table_names())
    if "execution_plan_secret_requirements" not in tables:
        op.create_table(
            "execution_plan_secret_requirements",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("execution_plan_id", sa.String(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("schema_version", sa.String(), nullable=False),
            sa.Column("request", sa.JSON(), nullable=False),
            sa.Column("reference", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["execution_plan_id"], ["execution_plans.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "execution_plan_id",
                "position",
                name="uq_execution_plan_secret_requirements_position",
            ),
        )
        op.create_index(
            op.f("ix_execution_plan_secret_requirements_execution_plan_id"),
            "execution_plan_secret_requirements",
            ["execution_plan_id"],
            unique=False,
        )

    tables = set(sa.inspect(connection).get_table_names())
    if "execution_plan_approval_gates" not in tables:
        op.create_table(
            "execution_plan_approval_gates",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("execution_plan_id", sa.String(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("schema_version", sa.String(), nullable=False),
            sa.Column("gate_type", sa.String(), nullable=False),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("step_ids", sa.JSON(), nullable=False),
            sa.Column("approval_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["approval_id"], ["approvals.id"]),
            sa.ForeignKeyConstraint(["execution_plan_id"], ["execution_plans.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "execution_plan_id",
                "position",
                name="uq_execution_plan_approval_gates_position",
            ),
        )
        op.create_index(
            op.f("ix_execution_plan_approval_gates_execution_plan_id"),
            "execution_plan_approval_gates",
            ["execution_plan_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_execution_plan_approval_gates_approval_id"),
            "execution_plan_approval_gates",
            ["approval_id"],
            unique=False,
        )


def _create_decision_tables() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if "execution_plan_risk_decisions" not in tables:
        op.create_table(
            "execution_plan_risk_decisions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("execution_plan_id", sa.String(), nullable=False),
            sa.Column("schema_version", sa.String(), nullable=False),
            sa.Column("decision", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["execution_plan_id"], ["execution_plans.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("execution_plan_id"),
        )
        op.create_index(
            op.f("ix_execution_plan_risk_decisions_execution_plan_id"),
            "execution_plan_risk_decisions",
            ["execution_plan_id"],
            unique=True,
        )

    tables = set(sa.inspect(connection).get_table_names())
    if "execution_plan_policy_decisions" not in tables:
        op.create_table(
            "execution_plan_policy_decisions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("execution_plan_id", sa.String(), nullable=False),
            sa.Column("schema_version", sa.String(), nullable=False),
            sa.Column("policy_pack", sa.String(), nullable=False),
            sa.Column("decision", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["execution_plan_id"], ["execution_plans.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("execution_plan_id"),
        )
        op.create_index(
            op.f("ix_execution_plan_policy_decisions_execution_plan_id"),
            "execution_plan_policy_decisions",
            ["execution_plan_id"],
            unique=True,
        )

    tables = set(sa.inspect(connection).get_table_names())
    if "execution_plan_security_reviews" not in tables:
        op.create_table(
            "execution_plan_security_reviews",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("execution_plan_id", sa.String(), nullable=False),
            sa.Column("schema_version", sa.String(), nullable=False),
            sa.Column("required", sa.Boolean(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("reviewer_configuration", sa.JSON(), nullable=False),
            sa.Column("findings", sa.JSON(), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
            sa.Column("reviewed_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["execution_plan_id"], ["execution_plans.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("execution_plan_id"),
        )
        op.create_index(
            op.f("ix_execution_plan_security_reviews_execution_plan_id"),
            "execution_plan_security_reviews",
            ["execution_plan_id"],
            unique=True,
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table_name in (
        "execution_plan_approval_gates",
        "execution_plan_secret_requirements",
        "execution_plan_security_reviews",
        "execution_plan_policy_decisions",
        "execution_plan_risk_decisions",
        "execution_plan_steps",
        "execution_plans",
    ):
        if table_name in sa.inspect(connection).get_table_names():
            op.drop_table(table_name)
