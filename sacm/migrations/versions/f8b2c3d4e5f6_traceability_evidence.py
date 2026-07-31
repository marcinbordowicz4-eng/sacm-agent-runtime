"""Add durable requirement traceability.

Revision ID: f8b2c3d4e5f6
Revises: e7a1b2c3d4f5
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "e7a1b2c3d4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if "requirements" not in tables:
        op.create_table(
            "requirements",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("schema_version", sa.String(), nullable=False),
            sa.Column("stable_hash", sa.String(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("normalized_text", sa.Text(), nullable=False),
            sa.Column("source_refs", sa.JSON(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "task_id",
                "stable_hash",
                name="uq_requirements_task_stable_hash",
            ),
            sa.UniqueConstraint(
                "task_id",
                "position",
                name="uq_requirements_task_position",
            ),
        )
        op.create_index(
            op.f("ix_requirements_task_id"),
            "requirements",
            ["task_id"],
            unique=False,
        )

    tables = set(sa.inspect(connection).get_table_names())
    if "requirement_links" not in tables:
        op.create_table(
            "requirement_links",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("requirement_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=True),
            sa.Column("schema_version", sa.String(), nullable=False),
            sa.Column("target_type", sa.String(), nullable=False),
            sa.Column("target_id", sa.String(), nullable=False),
            sa.Column("relation", sa.String(), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["requirement_id"], ["requirements.id"]),
            sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "requirement_id",
                "target_type",
                "target_id",
                "relation",
                name="uq_requirement_links_identity",
            ),
        )
        op.create_index(
            op.f("ix_requirement_links_task_id"),
            "requirement_links",
            ["task_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_requirement_links_requirement_id"),
            "requirement_links",
            ["requirement_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_requirement_links_run_id"),
            "requirement_links",
            ["run_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_requirement_links_target_type"),
            "requirement_links",
            ["target_type"],
            unique=False,
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "requirement_links" in tables:
        op.drop_table("requirement_links")
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "requirements" in tables:
        op.drop_table("requirements")
