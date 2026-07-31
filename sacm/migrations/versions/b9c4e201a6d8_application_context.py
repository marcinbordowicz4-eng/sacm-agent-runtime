"""Add durable application context.

Revision ID: b9c4e201a6d8
Revises: 7f44d635cf1a
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9c4e201a6d8"
down_revision: Union[str, Sequence[str], None] = "7f44d635cf1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())

    if "application_contexts" not in tables:
        op.create_table(
            "application_contexts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("schema_version", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("scanner_version", sa.String(), nullable=False),
            sa.Column("graph", sa.JSON(), nullable=False),
            sa.Column("graph_hash", sa.String(), nullable=False),
            sa.Column("impact_analysis", sa.JSON(), nullable=False),
            sa.Column("risk_analysis", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("task_id", name="uq_application_contexts_task_id"),
        )
        op.create_index(
            op.f("ix_application_contexts_task_id"),
            "application_contexts",
            ["task_id"],
            unique=False,
        )

    inspector = sa.inspect(connection)
    if "application_context_repositories" not in inspector.get_table_names():
        op.create_table(
            "application_context_repositories",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("application_context_id", sa.String(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("full_name", sa.String(), nullable=True),
            sa.Column("requested_path", sa.String(), nullable=True),
            sa.Column("resolved_path", sa.String(), nullable=True),
            sa.Column("base_revision", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("error_code", sa.String(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("file_count", sa.Integer(), nullable=False),
            sa.Column("skipped_file_count", sa.Integer(), nullable=False),
            sa.Column("scan_metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["application_context_id"], ["application_contexts.id"]
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "application_context_id",
                "position",
                name="uq_application_context_repositories_position",
            ),
        )
        op.create_index(
            op.f("ix_application_context_repositories_application_context_id"),
            "application_context_repositories",
            ["application_context_id"],
            unique=False,
        )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "application_context_repositories" in inspector.get_table_names():
        indexes = {
            item["name"]
            for item in inspector.get_indexes("application_context_repositories")
        }
        index_name = op.f(
            "ix_application_context_repositories_application_context_id"
        )
        if index_name in indexes:
            op.drop_index(
                index_name,
                table_name="application_context_repositories",
            )
        op.drop_table("application_context_repositories")
    inspector = sa.inspect(connection)
    if "application_contexts" in inspector.get_table_names():
        indexes = {
            item["name"] for item in inspector.get_indexes("application_contexts")
        }
        index_name = op.f("ix_application_contexts_task_id")
        if index_name in indexes:
            op.drop_index(index_name, table_name="application_contexts")
        op.drop_table("application_contexts")
