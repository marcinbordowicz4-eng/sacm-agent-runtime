"""Add task contract intake.

Revision ID: 7f44d635cf1a
Revises: 81160c78fa1e
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7f44d635cf1a"
down_revision: Union[str, Sequence[str], None] = "81160c78fa1e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    task_columns = {column["name"] for column in inspector.get_columns("tasks")}
    columns = (
        sa.Column("contract_version", sa.String(), nullable=True),
        sa.Column("connector_type", sa.String(), nullable=True),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("external_url", sa.String(), nullable=True),
        sa.Column("task_contract", sa.JSON(), nullable=True),
        sa.Column("readiness_score", sa.Float(), nullable=True),
        sa.Column("readiness_details", sa.JSON(), nullable=True),
    )
    for column in columns:
        if column.name not in task_columns:
            op.add_column("tasks", column)

    unique_constraints = {
        item["name"] for item in inspector.get_unique_constraints("tasks")
    }
    if "uq_tasks_connector_external_id" not in unique_constraints:
        with op.batch_alter_table("tasks") as batch_op:
            batch_op.create_unique_constraint(
                "uq_tasks_connector_external_id",
                ["connector_type", "external_id"],
            )

    if "task_clarifications" not in inspector.get_table_names():
        op.create_table(
            "task_clarifications",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("field_name", sa.String(), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("answer", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("answered_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "task_id", "field_name", name="uq_task_clarification_field"
            ),
        )
        op.create_index(
            op.f("ix_task_clarifications_task_id"),
            "task_clarifications",
            ["task_id"],
            unique=False,
        )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "task_clarifications" in inspector.get_table_names():
        op.drop_index(
            op.f("ix_task_clarifications_task_id"),
            table_name="task_clarifications",
        )
        op.drop_table("task_clarifications")
    unique_constraints = {
        item["name"] for item in inspector.get_unique_constraints("tasks")
    }
    if "uq_tasks_connector_external_id" in unique_constraints:
        with op.batch_alter_table("tasks") as batch_op:
            batch_op.drop_constraint(
                "uq_tasks_connector_external_id", type_="unique"
            )
    task_columns = {column["name"] for column in inspector.get_columns("tasks")}
    for column_name in (
        "readiness_details",
        "readiness_score",
        "task_contract",
        "external_url",
        "external_id",
        "connector_type",
        "contract_version",
    ):
        if column_name in task_columns:
            op.drop_column("tasks", column_name)
